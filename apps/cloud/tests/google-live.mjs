// Explicit read-only integration check against Google's public discovery service.
// No token exchange, credentials or simulated identity provider.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {googleMetadata,beginGoogle,googleConfig} from '../src/google.mjs';
import {runtime,migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';
const config={APP_ORIGIN:'https://cloud.test',GOOGLE_CLIENT_ID:'test-only.apps.googleusercontent.com',GOOGLE_CLIENT_SECRET:'not-used-for-exchange'};
test('Google discovery and authorization URL use state, nonce and PKCE',async()=>{
  const metadata=await googleMetadata();
  assert.equal(metadata.issuer,'https://accounts.google.com');
  const first=await beginGoogle(googleConfig(config)),second=await beginGoogle(googleConfig(config));
  const url=new URL(first.url);
  assert.equal(url.origin,new URL(metadata.authorization_endpoint).origin);
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('redirect_uri'),'https://cloud.test/api/cloud/v1/auth/callback');
  assert.equal(url.searchParams.get('state'),first.state);
  assert.equal(url.searchParams.get('nonce'),first.nonce);
  assert.notEqual(first.state,second.state);
  assert.notEqual(first.verifier,second.verifier);
});
test('Worker creates a browser-bound D1 transaction before redirecting to real Google authorization endpoint',async t=>{
  const mf=await runtime(config); t.after(()=>mf.dispose());
  const db=await mf.getD1Database('DB'); await migrate(db);
  const r=await mf.dispatchFetch('https://cloud.test/api/cloud/v1/auth/login',{method:'POST',headers:{Origin:'https://cloud.test','Content-Type':'application/x-www-form-urlencoded'},body:'',redirect:'manual'});
  assert.equal(r.status,303,await r.text());
  const state=new URL(r.headers.get('Location')).searchParams.get('state');
  assert.match(r.headers.get('Set-Cookie'),/Secure; SameSite=Lax; HttpOnly/);
  const transaction=await db.prepare('SELECT * FROM login_transactions').first();
  assert.equal(transaction.state_digest,await digest(state));
  assert.equal(transaction.expires_at-transaction.created_at,600);
});
