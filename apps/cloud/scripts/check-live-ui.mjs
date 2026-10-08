// Read-only public edge check. No login, uploads, credentials, or billed parse/search calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const origin='https://staging.parserium.com';
const get=path=>fetch(new URL(path,origin),{headers:{'Cache-Control':'no-cache'},signal:AbortSignal.timeout(15000)});
const expected=await readFile('../web/dist-cloud/index.html','utf8');
let shell;
for(let attempt=0;attempt<6;attempt++){
 const response=await get('/?verify='+crypto.randomUUID());const html=await response.text();
 if(response.status===200 && html.trim()===expected.trim()){shell=response;break;}
 console.log(`Waiting for staging assets to converge (${attempt+1}/6, HTTP ${response.status}).`);
 await new Promise(resolve=>setTimeout(resolve,3000));
}
assert.ok(shell,'Staging HTML must match the built Cloud React entry.');
assert.equal(shell.headers.get('Cache-Control'),'no-store');
assert.ok(shell.headers.get('Content-Security-Policy')?.includes("frame-ancestors 'none'"));
const assets=[...expected.matchAll(/(?:src|href)="(\/assets\/[^\"]+)"/g)].map(match=>match[1]);
for(const path of assets){const response=await get(path);assert.equal(response.status,200);await response.arrayBuffer();}
const health=await get('/api/cloud/v1/health?verify='+crypto.randomUUID());assert.equal(health.status,200);assert.equal((await health.json()).processing,'enabled');
for(const path of ['/api/cloud/v1/session','/api/cloud/v1/documents?workspace=unauthorized','/api/cloud/v1/discovery/connection?workspace=unauthorized','/api/cloud/v1/discovery/searches?workspace=unauthorized']){
 const response=await get(path);assert.equal(response.status,401,path);await response.text();
}
const preview=await fetch('https://parserium.com/?verify='+crypto.randomUUID(),{signal:AbortSignal.timeout(15000)});
assert.equal(preview.status,200);const previewBytes=Buffer.from(await preview.arrayBuffer());
const previewHash=createHash('sha256').update(previewBytes).digest('hex');
// Cloudflare injects its analytics beacon for some clients. Check the app bytes when it does not.
if(previewHash!=='45a80102b86137e3b8715fe56eb2fd46e71e24f4ab51d1a2084a8827d19cdf9b'){
 const existingPreview=await readFile('../web/dist-preview/index.html','utf8');
 assert.equal(previewBytes.toString('utf8').replaceAll('\r\n','\n').trim(),existingPreview.replaceAll('\r\n','\n').trim(),'Public preview app must remain unchanged.');
}
const require=createRequire(new URL('../../web/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1366,height:768},reducedMotion:'reduce'});const errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(origin);await page.getByRole('button',{name:'Continue with Google'}).waitFor();
 assert.deepEqual(errors,[]);
 console.log('PASS: deployed login screen renders in Chromium without JavaScript errors.');
}finally{await browser.close();}
console.log('PASS: staging serves exact React build, assets load, security headers apply, parsing is enabled, private endpoints reject anonymous access, and public preview is unchanged.');
