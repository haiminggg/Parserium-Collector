// Staging-only operator command. Preserve an existing wrapping key across deployments.
import {readFile} from 'node:fs/promises';
import {randomBytes} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const config=JSON.parse(await readFile('wrangler.staging.json','utf8'));
if(config.name!=='parserium-cloud-staging' || !process.env.CLOUDFLARE_API_TOKEN)throw Error('Reviewed staging config and operator token are required.');
const cli='node_modules/wrangler/bin/wrangler.js';
const listed=spawnSync(process.execPath,[cli,'secret','list','--config','wrangler.staging.json'],{encoding:'utf8',windowsHide:true});
if(listed.status!==0)throw Error('Unable to inspect staging secret names. No secret was changed.');
const secrets=JSON.parse(listed.stdout);
if(!Array.isArray(secrets))throw Error('Unexpected secret listing. No secret was changed.');
if(secrets.some(secret=>secret.name==='FIRECRAWL_CREDENTIAL_KEY')){
 console.log('Existing staging credential wrapping key preserved.');
}else{
 const key=randomBytes(32).toString('base64');
 const installed=spawnSync(process.execPath,[cli,'secret','put','FIRECRAWL_CREDENTIAL_KEY','--config','wrangler.staging.json'],{input:key+'\n',encoding:'utf8',windowsHide:true});
 if(installed.status!==0)throw Error('Credential key installation failed. Inspect staging secret names before retrying.');
 console.log('New staging credential wrapping key installed. Its value was not printed or written to disk.');
}
