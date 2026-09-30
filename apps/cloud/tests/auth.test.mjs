import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
const configuration = {APP_ORIGIN:'https://cloud.test',GOOGLE_CLIENT_ID:'test-only.apps.googleusercontent.com',GOOGLE_CLIENT_SECRET:'test-only-not-a-real-secret'};
// Test config is never used for a token exchange. These requests fail before contacting Google.
async function setup(t,bindings=configuration) {
  const mf=await runtime(bindings); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  return {mf,db};
}
test('login fails closed without OAuth configuration',async t=>{
  const {mf}=await setup(t,{});
  const r=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST'});
  assert.equal(r.status,503);
});
test('login rejects other origins and unsafe configured origin',async t=>{
  const {mf}=await setup(t);
  assert.equal((await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST',headers:{Origin:'https://evil.test'}})).status,403);
  const bad=await setup(t,{...configuration,APP_ORIGIN:'http://cloud.test'});
  assert.equal((await bad.mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST'})).status,503);
});
test('login rejects oversized or malformed invitation forms before storing transactions',async t=>{
  const {mf,db}=await setup(t);
  const send=body=>mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST',headers:{Origin:'https://cloud.test','Content-Type':'application/x-www-form-urlencoded'},body});
  assert.equal((await send('invite=invalid')).status,400);
  assert.equal((await send('invite='+ 'a'.repeat(4096))).status,413);
  assert.equal((await send('invite='+ 'a'.repeat(64))).status,403);
  assert.equal((await db.prepare('SELECT * FROM login_transactions').all()).results.length,0);
});
test('callback without browser-bound state fails safely without a session',async t=>{
  const {mf,db}=await setup(t);
  const r=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/callback?code=not-a-real-code&state=wrong',{redirect:'manual'});
  assert.equal(r.status,303);
  assert.equal(r.headers.get('Location'),'https://cloud.test/?auth=failed');
  assert.match(r.headers.get('Set-Cookie'),/__Host-parserium_login=;.*Max-Age=0/);
  assert.equal((await db.prepare('SELECT * FROM sessions').all()).results.length,0);
});
test('Google error callback consumes its browser-bound transaction without exchanging a code',async t=>{
  const {mf,db}=await setup(t);
  const {digest}=await import('../src/security.mjs');
  const browser='b'.repeat(64),state='s'.repeat(43),now=Math.floor(Date.now()/1000);
  await db.prepare('INSERT INTO login_transactions VALUES (?,?,?,?,NULL,?,?)').bind(await digest(browser),await digest(state),'nonce','verifier',now,now+600).run();
  const r=await mf.dispatchFetch(`https://cloud.test/api/cloud/v1/auth/callback?error=access_denied&state=${state}`,{headers:{Cookie:`__Host-parserium_login=${browser}`},redirect:'manual'});
  assert.equal(r.status,303);
  assert.equal((await db.prepare('SELECT * FROM login_transactions').all()).results.length,0);
  assert.equal((await db.prepare('SELECT * FROM sessions').all()).results.length,0);
});
