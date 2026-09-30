// Exercise the redirect policies from the discovery source in real workerd.
// No network requests, credentials, or external API test doubles are used.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';

test('discovery redirect modes are accepted by workerd and never follow automatically',async t=>{
 const source=await readFile(new URL('../src/discovery.mjs',import.meta.url),'utf8');
 const modes=[...source.matchAll(/\bredirect\s*:\s*'([^']+)'/g)].map(match=>match[1]);
 assert.equal(modes.length,3,'cover Firecrawl, public DNS, and document requests');
 const mf=new Miniflare(convertV4MiniflareOptions({modules:true,compatibilityDate:'2026-09-10',
  script:`export default {fetch(){return Response.json(${JSON.stringify(modes)}.map(redirect=>
   new Request('https://api.firecrawl.dev/v2/search',{redirect}).redirect));}}`}));
 t.after(()=>mf.dispose());
 const response=await mf.dispatchFetch('https://runtime-check.invalid/');
 assert.equal(response.status,200);
 assert.deepEqual(await response.json(),['manual','manual','manual']);
});
