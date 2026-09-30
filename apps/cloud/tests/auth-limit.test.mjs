import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
const config={APP_ORIGIN:'https://cloud.test',GOOGLE_CLIENT_ID:'test-only.apps.googleusercontent.com',GOOGLE_CLIENT_SECRET:'test-only'};
test('login traffic is limited before invitation lookup, independently from callbacks',async t=>{
  const mf=await runtime(config); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  const send=()=>mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST',headers:{Origin:config.APP_ORIGIN,'Content-Type':'application/x-www-form-urlencoded'},body:'invite=invalid'});
  for(let i=0;i<10;i++) assert.equal((await send()).status,400);
  const denied=await send();
  assert.equal(denied.status,429);
  assert.equal(denied.headers.get('Retry-After'),'60');
  assert.equal((await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/callback',{redirect:'manual'})).status,303);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM login_transactions').first()).n,0);
});
test('authentication fails closed if its rate limiter is absent',async t=>{
  const mf=await runtime(config,{}); t.after(()=>mf.dispose());
  const response=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST',headers:{Origin:config.APP_ORIGIN,'Content-Type':'application/x-www-form-urlencoded'},body:'invite=invalid'});
  assert.equal(response.status,503);
});
test('callback attempts are limited and forwarded headers cannot reset the bucket',async t=>{
  const mf=await runtime(config); t.after(()=>mf.dispose());
  for(let i=0;i<10;i++) {
    const r=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/callback',{
      headers:{'X-Forwarded-For':`192.0.2.${i+1}`},redirect:'manual'});
    assert.equal(r.status,303);
  }
  const r=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/callback',{
    headers:{'X-Forwarded-For':'192.0.2.100'},redirect:'manual'});
  assert.equal(r.status,429);
  assert.equal(r.headers.get('Cache-Control'),'no-store');
});
