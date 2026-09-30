import {Container} from '@cloudflare/containers';
import {coordinateContainerRequest} from './parser-container-coordinator.mjs';

export class ParserContainer extends Container{
 defaultPort=8080;
 requiredPorts=[8080];
 sleepAfter='20s';
 enableInternet=false;

 transport(){
  return {
   start:signal=>this.startAndWaitForPorts({
    ports:8080,
    cancellationOptions:{abort:signal,instanceGetTimeoutMS:15_000,portReadyTimeoutMS:45_000,waitInterval:250},
    startOptions:{enableInternet:false}
   }),
   fetch:(path,bytes,signal)=>this.containerFetch(`http://localhost${path}`,{
    method:'POST',headers:{'Content-Type':new Uint8Array(bytes)[0]===80?'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'application/pdf','Content-Length':String(bytes.byteLength)},body:bytes,signal
   },8080),
   stop:()=>this.stop()
  };
 }

 validatePdf(bytes){return coordinateContainerRequest('validate',bytes,this.transport());}
 parsePdf(bytes){return coordinateContainerRequest('parse',bytes,this.transport());}
}
