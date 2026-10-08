// Headless local integration check. Test identities exist only in ephemeral Miniflare D1.
// Uses the built React application and real Worker APIs. No HTTP response mocks.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {migrate} from './runtime.mjs';
import {digest} from '../src/security.mjs';
const require=createRequire(new URL('../../web/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const {default:AxeBuilder}=require('@axe-core/playwright');
const built=await build({entryPoints:['src/worker.mjs'],bundle:true,write:false,format:'esm',platform:'browser',target:'es2022',external:['cloudflare:workers']});
const mf=new Miniflare(convertV4MiniflareOptions({name:'cloud-ui-local-check',modules:true,script:built.outputFiles[0].text,compatibilityDate:'2026-09-10',host:'127.0.0.1',port:0,d1Databases:['DB'],r2Buckets:['FILES'],bindings:{PROCESSING_ENABLED:'false'},
 assets:{directory:'../web/dist-cloud',binding:'ASSETS',run_worker_first:true,routerConfig:{has_user_worker:true},assetConfig:{not_found_handling:'single-page-application'}}}));
let browser;
try{
 const origin=(await mf.ready).origin.replace('127.0.0.1','localhost');
 const db=await mf.getD1Database('DB');await migrate(db);
 const token=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-',''),csrf=crypto.randomUUID().replaceAll('-','')+crypto.randomUUID().replaceAll('-',''),now=Math.floor(Date.now()/1000);
 const discoveredLongTitle='Institutional climate risk disclosure and resilient infrastructure investment framework for cross-border portfolio governance 2026.pdf';
 const discoveredShortTitle='Short report.pdf';
 const discoveryResults=JSON.stringify([
  {id:'44444444-4444-4444-8444-444444444444',title:discoveredLongTitle,description:'A long-title verification result.',url:'https://www.example.com/reports/climate-risk-2026.pdf',file_type:'pdf'},
  {id:'55555555-5555-4555-8555-555555555555',title:discoveredShortTitle,description:'A short-title verification result.',url:'https://www.example.com/reports/short.pdf',file_type:'pdf'}
 ]);
 await db.batch([
  db.prepare("INSERT INTO users VALUES ('ui-local','https://accounts.google.com','ui-local-only','ui-check@example.test',1)"),
  db.prepare("INSERT INTO workspaces VALUES ('ui-local-workspace','Local verification workspace',1)"),
  db.prepare("INSERT INTO memberships VALUES ('ui-local-workspace','ui-local','owner')"),
  db.prepare('INSERT INTO sessions VALUES (?,?,?,?,?)').bind(await digest(token),'ui-local',await digest(csrf),now,now+3600),
  db.prepare('INSERT INTO discovery_searches (id,workspace_id,request_id,query,file_type,result_limit,status,results_json,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)')
   .bind('33333333-3333-4333-8333-333333333333','ui-local-workspace','66666666-6666-4666-8666-666666666666','verification reports','pdf',2,'succeeded',discoveryResults,null,now,now),
  db.prepare('INSERT INTO discovery_searches (id,workspace_id,request_id,query,file_type,result_limit,status,results_json,error_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)')
   .bind('77777777-7777-4777-8777-777777777777','ui-local-workspace','88888888-8888-4888-8888-888888888888','annual report tables','pdf',2,'succeeded',discoveryResults,null,now-60,now-60)
 ]);
 const output=fileURLToPath(new URL('../../../.local/cloud-ui-check/',import.meta.url));await mkdir(output,{recursive:true});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error')console.log('Browser console:',message.text());});
 const assertAccessible=async(label,scope)=>{
  const builder=new AxeBuilder({page});
  if(scope)builder.include(scope);
  const accessibility=await builder.analyze();
  assert.deepEqual(accessibility.violations.map(item=>({id:item.id,impact:item.impact,nodes:item.nodes.map(node=>({target:node.target,html:node.html,failureSummary:node.failureSummary}))})),[],`${label} should have no detectable accessibility violations`);
 };
 const first=await page.goto(origin);
 try{await page.getByRole('button',{name:'Continue with Google'}).waitFor({timeout:10000});}
 catch(error){console.log('Local page diagnostic',first.status(),await page.locator('body').innerText(),errors);await page.screenshot({path:output+'/failure.png',fullPage:true});throw error;}
 await page.screenshot({path:output+'/login.png',fullPage:true});
 await context.addCookies([{name:'__Host-parserium_cloud',value:token,url:origin.replace('http:','https:'),secure:true,httpOnly:true,sameSite:'Lax'},{name:'__Host-parserium_csrf',value:csrf,url:origin.replace('http:','https:'),secure:true,sameSite:'Lax'}]);
 const searchesResponsePromise=page.waitForResponse(response=>response.url().includes('/api/cloud/v1/discovery/searches')&&response.request().method()==='GET');
 await page.reload();await page.getByRole('heading',{name:'Find documents'}).waitFor();
 const collectTitleStyle=await page.locator('.cloud-page-title').evaluate(node=>{const style=getComputedStyle(node);return {fontFamily:style.fontFamily,fontSize:style.fontSize,lineHeight:style.lineHeight};});
 const searchesResponse=await searchesResponsePromise;
 assert.equal(searchesResponse.status(),200,'seeded discovery search should cross the authenticated API boundary');
 const searchesPayload=await searchesResponse.json();
 assert.equal(searchesPayload.searches?.length,2,'seeded discovery searches should appear in the API response');
 assert.equal((await db.prepare('SELECT COUNT(*) AS count FROM discovery_searches WHERE workspace_id=?').bind('ui-local-workspace').first()).count,2,'seeded discovery searches should remain in D1');
 await page.getByRole('region',{name:'Discovered documents'}).waitFor();
 const discoveredLongMarquee=page.getByRole('link',{name:discoveredLongTitle,exact:true}).locator('.cloud-overflow-marquee');
 const discoveredShortMarquee=page.getByRole('link',{name:discoveredShortTitle,exact:true}).locator('.cloud-overflow-marquee');
 assert.equal(await discoveredLongMarquee.getAttribute('data-overflow'),'true','cutoff discovered document names should enter scrolling state');
 assert.equal(await discoveredShortMarquee.getAttribute('data-overflow'),'false','fitting discovered document names should remain stationary');
 assert.equal(await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'none','reduced motion should disable filename scrolling');
 await page.emulateMedia({reducedMotion:'no-preference'});
 assert.equal(await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'none','cutoff discovered document names should remain still until hovered');
 await discoveredLongMarquee.hover();
 assert.equal(await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'cloud-overflow-pan','hovering a cutoff discovered document name should start scrolling');
 await page.waitForTimeout(400);
 const initialHoverOffset=await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>new DOMMatrixReadOnly(getComputedStyle(node).transform).m41);
 assert.ok(initialHoverOffset < -0.25,`hovered cutoff names should move without an initial hold; measured offset ${initialHoverOffset}`);
 await page.screenshot({path:output+'/collect-filename-scroll-desktop.png',fullPage:true});
 await page.mouse.move(0,0);
 assert.equal(await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'none','leaving a cutoff discovered document name should stop scrolling');
 await discoveredShortMarquee.hover();
 assert.equal(await discoveredShortMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'none','fitting discovered document names should not animate');
 await page.emulateMedia({reducedMotion:'reduce'});
 await discoveredLongMarquee.hover();
 assert.equal(await discoveredLongMarquee.locator('.cloud-overflow-marquee-content').evaluate(node=>getComputedStyle(node).animationName),'none','reduced motion should keep hovered filenames stationary');
 for(const label of ['Document format','Number of results']){
  const box=await page.getByLabel(label).boundingBox();
  assert.ok(box&&box.height>=32,`${label} should remain visibly operable in the local command layout`);
 }
 await page.screenshot({path:output+'/collect-desktop.png',fullPage:true});
 await assertAccessible('Collect');
 await page.getByRole('button',{name:'Upload document',exact:true}).click();
 await page.screenshot({path:output+'/upload-desktop.png',fullPage:true});
 await assertAccessible('Upload dialog','[role="dialog"]');
 await page.locator('input[type=file]').setInputFiles('../../tests/fixtures/analysis/ruled-table.docx');
 await page.getByRole('dialog').getByRole('button',{name:'Upload document',exact:true}).click();
 await page.getByRole('heading',{name:'Documents',exact:true}).waitFor();
 assert.deepEqual(await page.locator('.cloud-page-title').evaluate(node=>{const style=getComputedStyle(node);return {fontFamily:style.fontFamily,fontSize:style.fontSize,lineHeight:style.lineHeight};}),collectTitleStyle,'Documents should share the same page heading typography as Collect');
 const documentRow=page.locator('.cloud-library-ledger').getByRole('button',{name:/ruled-table.docx Uploaded document/});
 await documentRow.waitFor();
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 const initialDocumentRowBox=await documentRow.boundingBox();
 assert.ok(initialDocumentRowBox&&initialDocumentRowBox.y+initialDocumentRowBox.height<=1000,`the working document library should be visible in the initial desktop viewport; row bottom was ${initialDocumentRowBox?.y+initialDocumentRowBox?.height}`);
 assert.equal(await page.locator('.cloud-featured-document').count(),0,'the obsolete document spotlight should be removed');
 assert.equal(await page.locator('.cloud-corpus-ledger').count(),1,'the corpus wall should expose the complete document ledger');
 assert.equal(await page.getByRole('navigation',{name:'Research workflow'}).count(),1,'Documents should retain the shared research workflow rail');
 const uploaded=await db.prepare('SELECT id,filename FROM documents').first();
 assert.equal(uploaded.filename,'ruled-table.docx');
 const corpusGeometry=await page.evaluate(()=>({
  indexTop:document.querySelector('.cloud-corpus-index').getBoundingClientRect().top,
  ledgerTop:document.querySelector('.cloud-corpus-ledger').getBoundingClientRect().top,
  rowTop:document.querySelector('.cloud-corpus-ledger .activity-row').getBoundingClientRect().top
 }));
 assert.ok(corpusGeometry.indexTop<corpusGeometry.ledgerTop&&corpusGeometry.ledgerTop<=corpusGeometry.rowTop,'the corpus index and ledger should retain a clear visual reading order');
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 await page.screenshot({path:output+'/documents-desktop.png',fullPage:true});
 await assertAccessible('Documents');
 await documentRow.click();
 await page.getByRole('heading',{name:'ruled-table.docx',exact:true}).waitFor();
 await page.screenshot({path:output+'/inspector-desktop.png',fullPage:true});
 await assertAccessible('Document details','[role="dialog"]');
 await page.keyboard.press('Escape');
 const jobId='11111111-1111-4111-8111-111111111111',requestId='22222222-2222-4222-8222-222222222222',dayStart=now-(now%86400);
 await db.prepare(`INSERT INTO parse_jobs
  (id,workspace_id,document_id,request_id,status,attempt_count,day_start,reserved_runtime_ms,runtime_ms,created_at,updated_at)
  VALUES (?,?,?,?,?,?,?,?,?,?,?)`).bind(jobId,'ui-local-workspace',uploaded.id,requestId,'running',1,dayStart,120000,0,now,now).run();
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.getByRole('navigation').getByRole('button',{name:'Activity',exact:true}).click();
 await page.getByRole('heading',{name:'Activity',exact:true}).waitFor();
 assert.deepEqual(await page.locator('.cloud-page-title').evaluate(node=>{const style=getComputedStyle(node);return {fontFamily:style.fontFamily,fontSize:style.fontSize,lineHeight:style.lineHeight};}),collectTitleStyle,'Activity should share the same page heading typography as Collect');
 await page.getByRole('button',{name:'Refresh activity'}).click();
 const timelineEvent=page.locator('[data-activity-event]');await timelineEvent.waitFor();
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 const initialTimelineEventBox=await timelineEvent.boundingBox();
 assert.ok(initialTimelineEventBox&&initialTimelineEventBox.y<650,`the live parsing timeline should begin within the initial desktop viewport hierarchy; first event started at ${initialTimelineEventBox?.y}`);
 assert.equal(await page.getByRole('region',{name:'Document processing'}).count(),1,'Activity should expose an independent document processing region');
 assert.equal(await page.getByRole('complementary',{name:'Search history'}).count(),1,'Activity should expose an independent search history region');
 const searchRows=page.locator('.cloud-search-row');
 assert.equal(await searchRows.count(),2,'Activity should render the two seeded search records');
 const searchRowBoxes=await searchRows.evaluateAll(nodes=>nodes.map(node=>{const rect=node.getBoundingClientRect();return {top:rect.top,bottom:rect.bottom};}));
 assert.ok(searchRowBoxes[1].top-searchRowBoxes[0].bottom>=8,`search history records should have visible separation; measured ${searchRowBoxes[1].top-searchRowBoxes[0].bottom}px`);
 assert.equal(await page.locator('[data-search-document-relationship]').count(),0,'Activity must not imply unsupported search-to-document relationships');
 assert.equal(await page.locator('.cloud-timeline-line').evaluate(node=>getComputedStyle(node).width),'1px','the parse lane should retain its meaningful trace line');
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 const marker=page.locator('[data-active-marker]');await marker.waitFor();
 const motionStart=await marker.evaluate(node=>({opacity:getComputedStyle(node).opacity,transform:getComputedStyle(node).transform}));
 await page.waitForTimeout(260);
 const motionLater=await marker.evaluate(node=>({opacity:getComputedStyle(node).opacity,transform:getComputedStyle(node).transform}));
 assert.notDeepEqual(motionLater,motionStart,'active parsing marker should animate when reduced motion is not requested');
 await page.waitForTimeout(650);
 await page.screenshot({path:output+'/activity-desktop.png',fullPage:true});
 await assertAccessible('Activity');
 await page.setViewportSize({width:390,height:844});
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 await page.screenshot({path:output+'/activity-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile activity page should not overflow horizontally');
 await page.locator('.cloud-timeline-panel').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/activity-mobile-timeline.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('navigation').getByRole('button',{name:'Collect',exact:true}).click();
 const activitySnapshot=page.locator('.cloud-activity-snapshot');
 await activitySnapshot.getByText('ruled-table.docx',{exact:true}).waitFor();
 assert.equal(await activitySnapshot.locator('ol button').count(),0,'Collect parsing timeline entries should remain non-interactive');
 assert.equal(await activitySnapshot.getByRole('button',{name:'Open full activity'}).count(),1,'Collect parsing timeline should expose one Activity destination');
 await activitySnapshot.scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/collect-activity-desktop.png',fullPage:true});
 await assertAccessible('Collect activity snapshot','.cloud-activity-snapshot');
 await page.setViewportSize({width:390,height:844});
 await activitySnapshot.scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/collect-activity-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile Collect activity snapshot should not overflow horizontally');
 await page.setViewportSize({width:1440,height:1000});
 await db.prepare('DELETE FROM parse_jobs WHERE id=?').bind(jobId).run();
 await page.getByRole('navigation').getByRole('button',{name:'Documents',exact:true}).click();
 await page.getByRole('button',{name:'Refresh'}).click();
 await page.locator('.cloud-library-ledger').getByRole('button',{name:'Delete ruled-table.docx'}).waitFor();
 await page.setViewportSize({width:390,height:844});
 await page.locator('.workspace-shell').evaluate(node=>{node.scrollTop=0;});
 await page.screenshot({path:output+'/documents-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile documents page should not overflow horizontally');
 const mobileSettingsBox=await page.getByRole('button',{name:'Settings',exact:true}).boundingBox();
 assert.ok(mobileSettingsBox&&mobileSettingsBox.width>=30&&mobileSettingsBox.height>=30,'mobile Settings control should remain reachable');
 const mobileSettingsIcon=await page.getByRole('button',{name:'Settings',exact:true}).locator('svg').boundingBox();
 assert.ok(mobileSettingsIcon&&mobileSettingsIcon.width>=16&&mobileSettingsIcon.height>=16,'mobile Settings icon should remain visible');
 await page.locator('.cloud-library-ledger').scrollIntoViewIfNeeded();
 const mobileLedgerFits=await page.locator('.cloud-library-ledger .activity-table-scroll').evaluate(node=>node.scrollWidth<=node.clientWidth);
 assert.equal(mobileLedgerFits,true,'the mobile document ledger should expose every action without horizontal scrolling');
 for(const actionName of ['Download ruled-table.docx','Delete ruled-table.docx']){
  const actionBox=await page.getByLabel(actionName,{exact:true}).boundingBox();
  assert.ok(actionBox&&actionBox.x>=0&&actionBox.x+actionBox.width<=390&&actionBox.width>=30&&actionBox.height>=30,`mobile document action ${actionName} should remain visible and reachable`);
 }
 await page.screenshot({path:output+'/documents-mobile-library.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'Delete ruled-table.docx'}).click();
 await page.waitForTimeout(250);
 await assertAccessible('Delete confirmation','[role="dialog"]');
 await page.getByRole('dialog').getByRole('button',{name:'Delete document',exact:true}).click();
 await page.getByRole('heading',{name:'A home for your documents'}).waitFor();
 assert.equal((await db.prepare('SELECT count(*) AS n FROM documents').first()).n,0);
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('heading',{name:'Search the wider web.'}).waitFor();await page.waitForTimeout(300);
 await page.screenshot({path:output+'/settings-desktop.png',fullPage:true});await assertAccessible('Workspace settings','[role="dialog"]');await page.keyboard.press('Escape');
 await page.getByRole('navigation').getByRole('button',{name:'Collect',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:output+'/collect-mobile.png',fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,'mobile page should not overflow horizontally');
 assert.deepEqual(errors,[]);
console.log('PASS: login, authenticated React page, accessibility, DOCX upload/list/delete, hover-triggered overflow filenames, editorial documents, animated activity, inspector, settings, and mobile layout. Screenshots: .local/cloud-ui-check');
}finally{await browser?.close();await mf.dispose();}
