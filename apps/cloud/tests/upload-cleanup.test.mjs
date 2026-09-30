import assert from 'node:assert/strict';
import {test} from 'node:test';
import {runtime,migrate} from './runtime.mjs';
import {reserveUpload} from '../src/upload-admission.mjs';
import * as cleanup from '../src/upload-cleanup.mjs';
async function setup(t){
 const mf=await runtime();t.after(()=>mf.dispose());const db=await mf.getD1Database('DB');await migrate(db);
 await db.batch([db.prepare("INSERT INTO users VALUES ('u','https://accounts.google.com','cleanup-test','cleanup@example.test',1)"),db.prepare("INSERT INTO workspaces VALUES ('w','Test',1)"),db.prepare("INSERT INTO memberships VALUES ('w','u','owner')")]);
 const reserve=()=>reserveUpload(db,{userId:'u',workspaceId:'w',requestId:crypto.randomUUID(),filename:'test.pdf',sizeBytes:100},100);
 return {db,bucket:await mf.getR2Bucket('FILES'),reserve};
}
test('expired claimed upload is fenced before quota release, blocking delayed writes',async t=>{
 assert.equal(typeof cleanup.cleanupUploads,'function');
 const {db,bucket,reserve}=await setup(t);const row=await reserve();
 await db.prepare('UPDATE upload_reservations SET write_token=? WHERE id=?').bind('test-write-token',row.id).run();
 const key='workspaces/w/originals/'+row.id+'.pdf';
 const guard=await bucket.put(key,'test-write-token');
 const result=await cleanup.cleanupUploads(db,bucket,3700);
 assert.equal(result.cleaned,1);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,0);
 assert.equal((await bucket.head(key)).size,0);
 assert.equal(await bucket.put(key,'late PDF bytes',{onlyIf:{etagMatches:guard.etag}}),null);
 assert.equal(await bucket.put(key,'late guard',{onlyIf:{etagDoesNotMatch:'*'}}),null);
});
test('cleanup is bounded and never expires stored documents or live reservations',async t=>{
 assert.equal(typeof cleanup.cleanupUploads,'function');
 const {db,bucket,reserve}=await setup(t);
 const stored=await reserve();await db.prepare("UPDATE upload_reservations SET status='stored' WHERE id=?").bind(stored.id).run();
 await bucket.put('workspaces/w/originals/'+stored.id+'.pdf','retained test bytes');
 for(let i=0;i<26;i++)await reserve();
 assert.equal((await cleanup.cleanupUploads(db,bucket,200)).cleaned,0);
 assert.equal((await cleanup.cleanupUploads(db,bucket,3700)).cleaned,25);
 assert.equal((await cleanup.cleanupUploads(db,bucket,3700)).cleaned,1);
 assert.equal((await db.prepare('SELECT count(*) AS n FROM upload_reservations').first()).n,1);
 assert.equal(await (await bucket.get('workspaces/w/originals/'+stored.id+'.pdf')).text(),'retained test bytes');
});
