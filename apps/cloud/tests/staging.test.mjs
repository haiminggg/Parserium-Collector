import assert from 'node:assert/strict';
import {test} from 'node:test';
import * as staging from '../scripts/staging-config.mjs';
import {remoteDatabase} from '../scripts/remote-d1.mjs';
// These identifiers are confined to local configuration tests. Never deployed.
const input={STAGING_WORKER_NAME:'test-parserium-staging',APP_ORIGIN:'https://staging.example.test',
  CLOUDFLARE_ACCOUNT_ID:'a'.repeat(32),CLOUDFLARE_D1_DATABASE_ID:'12345678-1234-1234-1234-123456789abc',
  STAGING_D1_NAME:'test-staging-db',AUTH_RATE_NAMESPACE_ID:'123',GOOGLE_CLIENT_ID:'test-only.apps.googleusercontent.com',
  PROCESSING_ENABLED:'false'};
test('staging configuration includes private bounded parsing resources and no secrets',()=>{
  assert.equal(typeof staging.stagingConfig,'function');
  const config=staging.stagingConfig(input);
  assert.equal(config.d1_databases[0].database_id,input.CLOUDFLARE_D1_DATABASE_ID);
  assert.deepEqual(config.r2_buckets,[{binding:'FILES',bucket_name:'test-parserium-staging-files'}]);
  assert.equal(config.ratelimits[0].simple.limit,10);
  assert.deepEqual(config.triggers.crons,['*/5 * * * *']);
  assert.deepEqual(config.routes,[{pattern:'staging.example.test',custom_domain:true}]);
  assert.equal(config.vars.PROCESSING_ENABLED,'false');
  assert.deepEqual(config.queues.producers,[{binding:'PARSE_QUEUE',queue:'test-parserium-staging-parse'}]);
  assert.deepEqual(config.queues.consumers,[{queue:'test-parserium-staging-parse',max_batch_size:1,max_batch_timeout:1,
    max_retries:5,dead_letter_queue:'test-parserium-staging-parse-dlq',max_concurrency:1}]);
  assert.deepEqual(config.containers,[{class_name:'ParserContainer',image:'./container/Dockerfile',image_build_context:'../..',max_instances:1,instance_type:'basic'}]);
  assert.deepEqual(config.durable_objects.bindings,[{name:'PARSER',class_name:'ParserContainer'}]);
  assert.deepEqual(config.exports.ParserContainer,{type:'durable-object',storage:'sqlite'});
  assert.equal(config.migrations,undefined);
  assert.equal(config.vars.GOOGLE_CLIENT_SECRET,undefined);
  assert.equal(JSON.stringify(config).includes('CLOUDFLARE_API_TOKEN'),false);
});
test('staging refuses missing resources and production origin',()=>{
  assert.equal(typeof staging.stagingConfig,'function');
  for(const config of [{},{...input,APP_ORIGIN:'https://parserium.com'},{...input,CLOUDFLARE_D1_DATABASE_ID:''},
    {...input,STAGING_WORKER_NAME:'production'},{...input,PROCESSING_ENABLED:'yes'}]) {
    assert.throws(()=>staging.stagingConfig(config));
  }
  assert.throws(()=>remoteDatabase({}));
});
