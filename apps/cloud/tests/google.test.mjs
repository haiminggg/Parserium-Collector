import assert from 'node:assert/strict';
import {test} from 'node:test';
import {googleConfig} from '../src/google.mjs';
const config={APP_ORIGIN:'https://cloud.test',GOOGLE_CLIENT_ID:'test-only.apps.googleusercontent.com',GOOGLE_CLIENT_SECRET:'test-only-not-real'};
test('OAuth config fixes callback to a validated origin and requires real configuration fields',()=>{
  assert.equal(googleConfig(config).redirectUri,'https://cloud.test/api/cloud/v1/auth/callback');
  for (const APP_ORIGIN of ['http://cloud.test','https://cloud.test/path','https://cloud.test/?x=y','https://user:pass@cloud.test']) {
    assert.throws(()=>googleConfig({...config,APP_ORIGIN}));
  }
  assert.throws(()=>googleConfig({...config,GOOGLE_CLIENT_SECRET:''}));
  assert.throws(()=>googleConfig({...config,GOOGLE_CLIENT_ID:''}));
});
