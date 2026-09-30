import {pathToFileURL} from 'node:url';
export function stagingConfig(env) {
  const origin=new URL(env.APP_ORIGIN);
  if(origin.protocol!=='https:' || origin.username || origin.password || origin.pathname!=='/' || origin.search || origin.hash ||
    ['parserium.com','www.parserium.com'].includes(origin.hostname) ||
    !/^[a-z0-9][a-z0-9-]*-staging$/.test(env.STAGING_WORKER_NAME || '') ||
    !/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID || '') ||
    !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(env.CLOUDFLARE_D1_DATABASE_ID || '') ||
    !/^[a-zA-Z0-9_-]+$/.test(env.STAGING_D1_NAME || '') ||
    !/^[1-9][0-9]{0,8}$/.test(env.AUTH_RATE_NAMESPACE_ID || '') ||
    !/^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$/.test(env.GOOGLE_CLIENT_ID || '') ||
    !['true','false'].includes(env.PROCESSING_ENABLED)) throw Error('Invalid staging configuration');
  const files=`${env.STAGING_WORKER_NAME}-files`,parse=`${env.STAGING_WORKER_NAME}-parse`,dlq=`${parse}-dlq`;
  return {$schema:'node_modules/wrangler/config-schema.json',name:env.STAGING_WORKER_NAME,
    account_id:env.CLOUDFLARE_ACCOUNT_ID,main:'src/worker.mjs',compatibility_date:'2026-09-10',
    workers_dev:false,preview_urls:false,routes:[{pattern:origin.hostname,custom_domain:true}],
    assets:{directory:'../web/dist-cloud',binding:'ASSETS',not_found_handling:'single-page-application',run_worker_first:true},
    vars:{APP_ORIGIN:origin.origin,GOOGLE_CLIENT_ID:env.GOOGLE_CLIENT_ID,PROCESSING_ENABLED:env.PROCESSING_ENABLED},
    d1_databases:[{binding:'DB',database_name:env.STAGING_D1_NAME,database_id:env.CLOUDFLARE_D1_DATABASE_ID,migrations_dir:'migrations'}],
    r2_buckets:[{binding:'FILES',bucket_name:files}],
    ratelimits:[{name:'AUTH_LIMITER',namespace_id:env.AUTH_RATE_NAMESPACE_ID,simple:{limit:10,period:60}}],
    queues:{producers:[{binding:'PARSE_QUEUE',queue:parse}],consumers:[{queue:parse,max_batch_size:1,max_batch_timeout:1,
      max_retries:5,dead_letter_queue:dlq,max_concurrency:1}]},
    containers:[{class_name:'ParserContainer',image:'./container/Dockerfile',image_build_context:'../..',max_instances:1,instance_type:'basic'}],
    durable_objects:{bindings:[{name:'PARSER',class_name:'ParserContainer'}]},
    exports:{ParserContainer:{type:'durable-object',storage:'sqlite'}},
    triggers:{crons:['*/5 * * * *']}};
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  try { process.stdout.write(JSON.stringify(stagingConfig(process.env),null,2)+'\n'); }
  catch { console.error('Staging configuration incomplete. Supply the documented staging environment variables.'); process.exitCode=1; }
}
