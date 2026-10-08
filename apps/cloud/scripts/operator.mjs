import {parseArgs} from 'node:util';
import {pathToFileURL} from 'node:url';
import {issueInvitation} from '../src/invitations.mjs';
import {remoteDatabase} from './remote-d1.mjs';

export async function runOperator(db,args,now=Math.floor(Date.now()/1000)) {
  const [command,...rest]=args;
  let values;
  try {
    if(!['workspace-create','invite'].includes(command)) throw Error();
    const options=command==='workspace-create' ? {name:{type:'string'}} : {
      'workspace-id':{type:'string'},email:{type:'string'},role:{type:'string'},'ttl-seconds':{type:'string'}};
    ({values}=parseArgs({args:rest,options,strict:true,allowPositionals:false}));
    if(command==='workspace-create' && (!values.name?.trim() || values.name.length>128)) throw Error();
    if(command==='invite' && (!values['workspace-id'] || !values.email)) throw Error();
  } catch { throw Error('Invalid operator arguments'); }
  if(command==='workspace-create') {
    const workspaceId=crypto.randomUUID();
    await db.prepare('INSERT INTO workspaces (id,name,created_at) VALUES (?,?,?)').bind(workspaceId,values.name.trim(),now).run();
    return {workspaceId};
  }
  return issueInvitation(db,{workspaceId:values['workspace-id'],email:values.email,
    role:values.role || 'member',ttlSeconds:values['ttl-seconds']===undefined ? 86400 : Number(values['ttl-seconds'])},now);
}

if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const args=process.argv.slice(2);
    if(args.shift()!=='--confirm-remote') throw Error('Remote operation requires --confirm-remote');
    const result=await runOperator(remoteDatabase(process.env),args);
    // Intentionally print the newly issued invitation once, never the Cloudflare credential.
    process.stdout.write(JSON.stringify(result)+'\n');
  } catch {
    console.error('Operator command failed. Check arguments, remote confirmation, credentials, database ID and migrations. No credentials or upstream response were logged.');
    process.exitCode=1;
  }
}
