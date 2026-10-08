import {build} from 'esbuild';
import {readFile, readdir} from 'node:fs/promises';
import {Miniflare, convertV4MiniflareOptions} from 'miniflare';

export async function runtime(bindings = {}, ratelimits = {AUTH_LIMITER:{namespace_id:'1',simple:{limit:10,period:60}}}) {
  const output = await build({entryPoints: ['src/worker.mjs'], bundle: true, write: false,
    format: 'esm', platform: 'browser', target: 'es2022',external:['cloudflare:workers']});
  return new Miniflare(convertV4MiniflareOptions({name: 'cloud', modules: true,
    script: output.outputFiles[0].text, compatibilityDate: '2026-09-10', d1Databases: ['DB'], r2Buckets:['FILES'], bindings, ratelimits}));
}

export async function migrate(db) {
  for (const file of (await readdir('migrations')).filter(file => file.endsWith('.sql')).sort()) {
    for (const sql of (await readFile(`migrations/${file}`, 'utf8')).split(';').filter(s => s.trim())) {
      await db.prepare(sql).run();
    }
  }
}
