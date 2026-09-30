const MAX_BYTES=10_485_760;
const validationErrors=new Set(['invalid_pdf','invalid_docx','encrypted_pdf','page_limit_exceeded']);
const retryableErrors=new Set(['parser_timeout','parser_unavailable','container_start_failed','storage_unavailable']);
const permanentErrors=new Set([...validationErrors,'parser_failed','output_too_large']);

function binary(value){
 if(value instanceof ArrayBuffer)return value;
 if(ArrayBuffer.isView(value))return value.buffer.slice(value.byteOffset,value.byteOffset+value.byteLength);
 throw Error('invalid_input');
}

async function responseJson(response){
 if(!(response instanceof Response) || !response.ok || response.headers.get('Content-Type')?.split(';')[0].trim()!=='application/json')
  throw Error('invalid_response');
 const declared=response.headers.get('Content-Length');
 if(!declared || !/^\d+$/.test(declared) || Number(declared)>MAX_BYTES)throw Error('invalid_response');
 const reader=response.body?.getReader();
 if(!reader)throw Error('invalid_response');
 const chunks=[];let size=0;
 try{
  while(true){
   const {done,value}=await reader.read();if(done)break;
   size+=value.byteLength;if(size>MAX_BYTES || size>Number(declared))throw Error('invalid_response');
   chunks.push(value);
  }
 }finally{reader.releaseLock();}
 if(size!==Number(declared))throw Error('invalid_response');
 const body=new Uint8Array(size);let offset=0;
 for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body));
}

function failure(action,error,runtimeMs){
 if(typeof error!=='string' || (!retryableErrors.has(error) && !permanentErrors.has(error)))
  return action==='parse'?{ok:false,error:'parser_unavailable',retryable:true,runtimeMs:60_000}:
   {ok:false,error:'parser_unavailable',retryable:true};
 const retryable=retryableErrors.has(error);
 if(action==='parse')return {ok:false,error,retryable,runtimeMs:Number.isSafeInteger(runtimeMs) && runtimeMs>0?Math.min(60_000,runtimeMs):60_000};
 return {ok:false,error,retryable};
}

function sanitize(action,value){
 if(!value || typeof value!=='object')throw Error('invalid_response');
 if(value.ok!==true)return failure(action,value.error,value.runtimeMs);
 if(!Number.isSafeInteger(value.pageCount) || value.pageCount<1 || value.pageCount>20)throw Error('invalid_response');
 if(action==='validate')return {ok:true,pageCount:value.pageCount};
 if(!Number.isSafeInteger(value.tableCount) || value.tableCount<0 || typeof value.markdown!=='string' ||
    new TextEncoder().encode(value.markdown).byteLength>MAX_BYTES || !Number.isSafeInteger(value.runtimeMs) ||
    value.runtimeMs<1 || value.runtimeMs>60_000)throw Error('invalid_response');
 return {ok:true,pageCount:value.pageCount,tableCount:value.tableCount,markdown:value.markdown,runtimeMs:value.runtimeMs};
}

async function safeStop(transport){try{await transport.stop();}catch{/* A failed stop cannot expose platform details. */}}

export async function coordinateContainerRequest(action,input,transport,{timeoutMs=60_000}={}){
 if(!['validate','parse'].includes(action))throw Error('invalid_action');
 const bytes=binary(input);
 if(bytes.byteLength<5 || bytes.byteLength>MAX_BYTES)throw Error('invalid_input');
 if(!transport || typeof transport.start!=='function' || typeof transport.fetch!=='function' || typeof transport.stop!=='function')
  throw Error('invalid_transport');
 if(!Number.isSafeInteger(timeoutMs) || timeoutMs<1 || timeoutMs>60_000)throw Error('invalid_deadline');
 const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(Error('deadline_exceeded')),timeoutMs);
 let phase='start',result;
 try{
  await transport.start(controller.signal);phase='request';
  const response=await transport.fetch('/'+action,bytes,controller.signal);
  result=sanitize(action,await responseJson(response));
 }catch{
  result=phase==='start'?{ok:false,error:'container_start_failed',retryable:true}:
   failure(action,controller.signal.aborted?'parser_timeout':'parser_unavailable',60_000);
 }finally{
  clearTimeout(timer);
  if(action==='parse' || result?.ok!==true)await safeStop(transport);
 }
 return result;
}
