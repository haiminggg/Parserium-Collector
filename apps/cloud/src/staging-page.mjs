const page=`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Parserium staging</title>
<body><main><h1>Parserium staging</h1><p>Google sign-in test. This is not the document collection application.</p>
<p id="status" role="status">Checking session...</p>
<form id="login" action="/api/cloud/v1/auth/login" method="post" hidden>
<label>Invitation token (first sign-in only) <input name="invite" type="password" autocomplete="off" maxlength="64"></label>
<button type="submit">Continue with Google</button></form>
<section id="account" hidden><h2>Your workspaces</h2><ul id="workspaces"></ul><button id="logout">Log out</button>
<h2>Private document parsing test</h2><p>PDF only, up to 10 MiB and 20 pages. Upload a PDF, then choose Parse PDF. Firecrawl collection and DOCX output remain unavailable in this staging build.</p>
<label>Workspace <select id="workspace"></select></label>
<form id="upload"><label>PDF <input id="pdf" type="file" accept=".pdf,application/pdf" required></label><button id="upload-button">Upload PDF</button></form>
<p id="file-status" role="status"></p><button id="refresh-files" type="button">Refresh files</button><ul id="files"></ul></section>
</main><script src="/auth-ui.js" defer></script></body></html>`;
const script=`const status=document.getElementById('status');
async function refresh(){
 try {
  const r=await fetch('/api/cloud/v1/session',{cache:'no-store'});
  if(r.status===401){document.getElementById('login').hidden=false;status.textContent=new URLSearchParams(location.search).has('auth')?'Sign-in failed. Check the invitation and Google account, then try again.':'Sign in with your invited Google account.';return;}
  if(!r.ok)throw Error();
  const data=await r.json();status.textContent='Signed in as '+data.user.email;
  document.getElementById('account').hidden=false;
  for(const workspace of data.workspaces){const item=document.createElement('li');item.textContent=workspace.name+' ('+workspace.role+')';document.getElementById('workspaces').append(item);const option=document.createElement('option');option.value=workspace.id;option.textContent=workspace.name;document.getElementById('workspace').append(option);}
  await listFiles();
 }catch{status.textContent='Session unavailable. Please try again later.';}
}
document.getElementById('logout').addEventListener('click',async event=>{
 event.target.disabled=true;
 try {
  const csrf=document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('__Host-parserium_csrf='))?.split('=')[1];
  const r=await fetch('/api/cloud/v1/logout',{method:'POST',headers:{'X-CSRF-Token':csrf||''}});
  if(!r.ok)throw Error();location.replace('/');
 }catch{status.textContent='Logout failed. Please try again.';event.target.disabled=false;}
});
const fileStatus=document.getElementById('file-status');
const csrfToken=()=>document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('__Host-parserium_csrf='))?.split('=')[1]||'';
const parseAttempts=new Map(),polling=new Map();
const errorText=code=>({invalid_pdf:'The file is not a structurally valid PDF.',encrypted_pdf:'Encrypted PDFs cannot be parsed.',page_limit_exceeded:'The PDF exceeds the 20-page limit.',daily_job_limit:'This workspace has reached its daily parse limit.',processing_budget_exceeded:'The daily staging processing budget is exhausted.',processing_disabled:'Parsing is temporarily disabled.',processing_unavailable:'Parsing is temporarily unavailable.',parser_timeout:'Parsing timed out after two attempts.',parser_failed:'The parser could not process this document.',output_too_large:'The Markdown output exceeds 10 MiB.',original_missing:'The stored original is missing.',storage_unavailable:'Storage is temporarily unavailable.'}[code]||'The request could not be completed.');
function clearPolls(){for(const timer of polling.values())clearTimeout(timer);polling.clear();}
function pollJob(jobId){
 if(polling.has(jobId))return;
 const schedule=()=>polling.set(jobId,setTimeout(async()=>{
  try{
   const r=await fetch('/api/cloud/v1/parse-jobs/'+encodeURIComponent(jobId),{cache:'no-store'});
   if(!r.ok)throw Error();const {job}=await r.json();
   if(['pending_dispatch','queued','running'].includes(job.status)){schedule();return;}
   polling.delete(jobId);await listFiles();
  }catch{schedule();}
 },2000));
 schedule();
}
async function startParse(doc,button){
 button.disabled=true;let requestId=parseAttempts.get(doc.id);
 if(!requestId){requestId=crypto.randomUUID();parseAttempts.set(doc.id,requestId);}
 fileStatus.textContent='Validating and queueing '+doc.filename+'...';
 try{
  const r=await fetch('/api/cloud/v1/parse-jobs?document='+encodeURIComponent(doc.id),{method:'POST',headers:{'X-CSRF-Token':csrfToken(),'Idempotency-Key':requestId}});
  const data=await r.json();
  if(!r.ok){fileStatus.textContent=errorText(data.error)+' Try again later if the condition is temporary.';button.disabled=false;return;}
  parseAttempts.delete(doc.id);fileStatus.textContent='Parse job queued.';await listFiles();
 }catch{fileStatus.textContent='Parse submission was interrupted. Retry safely with the same document.';button.disabled=false;}
}
async function showMarkdown(jobId,pre,button){
 button.disabled=true;
 try{
  const r=await fetch('/api/cloud/v1/parse-jobs/'+encodeURIComponent(jobId)+'/output',{cache:'no-store'});if(!r.ok)throw Error();
  const markdown=await r.text();pre.textContent=markdown;pre.hidden=false;
 }catch{fileStatus.textContent='Markdown could not be loaded. Try again.';}
 finally{button.disabled=false;}
}
async function listFiles(){
 const workspace=document.getElementById('workspace').value;
 document.getElementById('files').replaceChildren();
 if(!workspace){fileStatus.textContent='No workspace available.';return;}
 try{
  const r=await fetch('/api/cloud/v1/documents?workspace='+encodeURIComponent(workspace),{cache:'no-store'});
  if(!r.ok)throw Error();const data=await r.json();
  if(document.getElementById('workspace').value!==workspace)return;
  for(const doc of data.documents){
   const item=document.createElement('li'),link=document.createElement('a'),remove=document.createElement('button'),state=document.createElement('span');
   link.href='/api/cloud/v1/files/'+encodeURIComponent(doc.id);link.textContent=doc.filename;link.download=doc.filename;
   remove.textContent='Delete';remove.type='button';
   remove.addEventListener('click',async()=>{
    if(!confirm('Permanently delete '+doc.filename+'?'))return;
    remove.disabled=true;
    try{const r=await fetch('/api/cloud/v1/files/'+encodeURIComponent(doc.id),{method:'DELETE',headers:{'X-CSRF-Token':csrfToken()}});if(!r.ok){const data=await r.json();if(data.error==='parse_in_progress'){fileStatus.textContent='Wait for parsing to finish before deleting.';remove.disabled=false;return;}throw Error();}fileStatus.textContent='File deleted.';await listFiles();}
    catch{fileStatus.textContent='Deletion failed. You can retry safely.';remove.disabled=false;}
   });
   item.append(link,document.createTextNode(' | '+doc.size_bytes+' bytes | '));
   if(!doc.job_id){
    state.textContent=doc.validation_status==='invalid'?errorText(doc.validation_error_code):'Awaiting validation';item.append(state,document.createTextNode(' '));
    if(doc.validation_status!=='invalid'){const parse=document.createElement('button');parse.type='button';parse.textContent='Parse PDF';parse.addEventListener('click',()=>{void startParse(doc,parse);});item.append(parse,document.createTextNode(' '));}
   }else if(['pending_dispatch','queued'].includes(doc.job_status)){
    state.textContent='Queued';item.append(state,document.createTextNode(' '));pollJob(doc.job_id);
   }else if(doc.job_status==='running'){
    state.textContent='Parsing';item.append(state,document.createTextNode(' '));pollJob(doc.job_id);
   }else if(doc.job_status==='succeeded'){
    state.textContent='Markdown ready';const preview=document.createElement('button'),download=document.createElement('a'),pre=document.createElement('pre');
    preview.type='button';preview.textContent='Preview Markdown';preview.addEventListener('click',()=>{void showMarkdown(doc.job_id,pre,preview);});
    download.href='/api/cloud/v1/parse-jobs/'+encodeURIComponent(doc.job_id)+'/output';download.textContent='Download Markdown';download.download='parsed.md';pre.hidden=true;
    item.append(state,document.createTextNode(' '),preview,document.createTextNode(' '),download,pre,document.createTextNode(' '));
   }else{
    state.textContent='Parsing failed: '+errorText(doc.job_error_code)+' Upload the document again to retry.';item.append(state,document.createTextNode(' '));
   }
   item.append(remove);document.getElementById('files').append(item);
  }
  if(!data.documents.length)fileStatus.textContent='No stored files in this workspace.';
 }catch{fileStatus.textContent='Could not load files. Try refreshing.';}
}
let uploadAttempt;
document.getElementById('pdf').addEventListener('change',()=>{uploadAttempt=null;});
document.getElementById('workspace').addEventListener('change',()=>{uploadAttempt=null;clearPolls();void listFiles();});
document.getElementById('refresh-files').addEventListener('click',()=>{void listFiles();});
document.getElementById('upload').addEventListener('submit',async event=>{
 event.preventDefault();const file=document.getElementById('pdf').files[0],workspace=document.getElementById('workspace').value;
 if(!file || !workspace || file.size<1 || file.size>10485760 || !file.name.toLowerCase().endsWith('.pdf')){fileStatus.textContent='Choose a PDF between 1 byte and 10 MiB.';return;}
 if(!uploadAttempt || uploadAttempt.file!==file || uploadAttempt.workspace!==workspace)uploadAttempt={file,workspace,id:crypto.randomUUID()};
 const button=document.getElementById('upload-button');button.disabled=true;fileStatus.textContent='Uploading...';
 try{
  const r=await fetch('/api/cloud/v1/uploads?'+new URLSearchParams({workspace,filename:file.name}),{method:'POST',headers:{'Content-Type':'application/pdf','X-CSRF-Token':csrfToken(),'Idempotency-Key':uploadAttempt.id},body:file});
  if(!r.ok){const result=await r.json();fileStatus.textContent='Upload failed: '+result.error+'.';return;}
  fileStatus.textContent='Original stored. Choose Parse PDF to validate and process it.';document.getElementById('pdf').value='';uploadAttempt=null;await listFiles();
 }catch{fileStatus.textContent='Upload interrupted. Retry with the same file to avoid a duplicate.';}
 finally{button.disabled=false;}
});
refresh();`;
export function stagingPage(path) {
 const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
  'Content-Security-Policy':"default-src 'none'; script-src 'self'; connect-src 'self'; form-action 'self' https://accounts.google.com; base-uri 'none'; frame-ancestors 'none'"};
 return new Response(path==='/'?page:script,{headers:{...headers,'Content-Type':path==='/'?'text/html; charset=utf-8':'text/javascript; charset=utf-8'}});
}
