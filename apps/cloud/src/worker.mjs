import {authRoute} from './auth.mjs';
import {discoveryRoute} from './discovery.mjs';
import {stagingPage} from './staging-page.mjs';
import {fileRoute} from './files.mjs';
import {parseRoute} from './parse-jobs.mjs';
import {processParseMessage,reconcileParseJobs} from './parse-queue.mjs';
import {ParserContainer} from './parser-container.mjs';
import {cleanupUploads} from './upload-cleanup.mjs';
import {cleanupAuth} from './maintenance.mjs';
import {SESSION_COOKIE as COOKIE, CSRF_COOKIE, digest, readTokenCookie, cookie} from './security.mjs';
const PREFIX = '/api/cloud/v1';
const headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'};
const json = (body, status = 200) => Response.json(body, {status, headers});
export {ParserContainer};

async function handle(request, env) {
  const url = new URL(request.url);
  if(!url.pathname.startsWith('/api/') && ['GET','HEAD'].includes(request.method) && env.ASSETS){
    const response=await env.ASSETS.fetch(request),secured=new Response(response.body,response);
    secured.headers.set('X-Content-Type-Options','nosniff');
    secured.headers.set('Referrer-Policy','same-origin');
    secured.headers.set('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self' https://accounts.google.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
    if(response.headers.get('Content-Type')?.includes('text/html'))secured.headers.set('Cache-Control','no-store');
    return secured;
  }
  if(request.method==='GET' && (url.pathname==='/' || url.pathname==='/auth-ui.js')) return stagingPage(url.pathname);
  if (!url.pathname.startsWith(`${PREFIX}/`)) return json({error: 'not_found'}, 404);
  if(url.pathname===`${PREFIX}/health` && request.method==='GET')
    return json({ok:true,processing:env.PROCESSING_ENABLED==='true'?'enabled':'disabled'});
  if (url.pathname === `${PREFIX}/auth/login` || url.pathname === `${PREFIX}/auth/callback`) return authRoute(request,env);
  const token = readTokenCookie(request,COOKIE);
  if (!token) return json({error: 'authentication_required'}, 401);
  if (!env.DB) return json({error: 'service_unavailable'}, 503);
  const tokenDigest = await digest(token);
  const session = await env.DB.prepare(`
    SELECT s.user_id, s.csrf_digest, u.email FROM sessions s
    JOIN users u ON u.id = s.user_id
    WHERE s.token_digest = ? AND s.expires_at > ?
  `).bind(tokenDigest, Math.floor(Date.now()/1000)).first();
  if (!session) return json({error: 'authentication_required'}, 401);
  if(url.pathname.startsWith(`${PREFIX}/discovery/`))return discoveryRoute(request,env,session);
  if(url.pathname===`${PREFIX}/uploads` || url.pathname.startsWith(`${PREFIX}/files/`)) return fileRoute(request,env,session);
  if(url.pathname===`${PREFIX}/parse-jobs` || url.pathname.startsWith(`${PREFIX}/parse-jobs/`)) return parseRoute(request,env,session);

  if (url.pathname === `${PREFIX}/session` && request.method === 'GET') {
    const rows = await env.DB.prepare(`SELECT w.id, w.name, m.role FROM workspaces w
      JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.id`)
      .bind(session.user_id).all();
    return json({user: {id: session.user_id, email: session.email}, workspaces: rows.results});
  }

  if (url.pathname === `${PREFIX}/logout` && request.method === 'POST') {
    const csrf = request.headers.get('X-CSRF-Token');
    if (request.headers.get('Origin') !== url.origin || !csrf ||
        !/^[a-f0-9]{64}$/.test(csrf) || await digest(csrf) !== session.csrf_digest) {
      return json({error: 'forbidden'}, 403);
    }
    await env.DB.prepare('DELETE FROM sessions WHERE token_digest=?').bind(tokenDigest).run();
    const cleared = new Headers(headers);
    cleared.append('Set-Cookie',cookie(COOKIE,'',0));
    cleared.append('Set-Cookie',cookie(CSRF_COOKIE,'',0,false));
    return new Response(null, {status: 204, headers: cleared});
  }

  if (url.pathname === `${PREFIX}/documents` && request.method === 'GET') {
    const workspace = url.searchParams.get('workspace');
    if (!workspace || workspace.length > 128) return json({error: 'workspace_required'}, 400);
    const member = await env.DB.prepare('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?')
      .bind(workspace, session.user_id).first();
    if (!member) return json({error: 'not_found'}, 404);
    // Authorization is repeated in the data query so revocation between queries cannot leak rows.
    const offset=Number(url.searchParams.get('offset')||0);
    if(!Number.isSafeInteger(offset)||offset<0||offset>100000)return json({error:'invalid_page'},400);
    const rows = await env.DB.prepare(`SELECT d.id,d.filename,d.size_bytes,d.created_at,d.validation_status,COALESCE(d.format_error_code,d.validation_error_code) AS validation_error_code,d.source_url,
      p.id AS job_id,p.status AS job_status,p.error_code AS job_error_code FROM documents d
      LEFT JOIN parse_jobs p ON p.document_id=d.id
      JOIN memberships m ON m.workspace_id=d.workspace_id
      WHERE d.workspace_id=? AND m.user_id=? ORDER BY d.created_at DESC, d.id DESC LIMIT 51 OFFSET ?`)
      .bind(workspace, session.user_id,offset).all();
    return json({documents: rows.results.slice(0,50),has_more:rows.results.length>50});
  }

  if (url.pathname.startsWith(`${PREFIX}/documents/`) && request.method === 'GET') {
    const id = url.pathname.slice(`${PREFIX}/documents/`.length);
    if (!id || id.length > 128 || id.includes('/')) return json({error: 'not_found'}, 404);
    const document = await env.DB.prepare(`SELECT d.id,d.filename,d.size_bytes,d.created_at,d.validation_status,COALESCE(d.format_error_code,d.validation_error_code) AS validation_error_code,d.source_url,
      p.id AS job_id,p.status AS job_status,p.error_code AS job_error_code FROM documents d
      LEFT JOIN parse_jobs p ON p.document_id=d.id
      JOIN memberships m ON m.workspace_id=d.workspace_id WHERE d.id=? AND m.user_id=?`)
      .bind(id, session.user_id).first();
    return document ? json({document}) : json({error: 'not_found'}, 404);
  }
  return json({error: 'not_found'}, 404);
}

export default {
  async scheduled(controller,env) {
    const now=Math.floor(controller.scheduledTime/1000);
    for(const task of [()=>cleanupAuth(env.DB,now),()=>cleanupUploads(env.DB,env.FILES,now),()=>reconcileParseJobs(env,now)]){
      try{await task();}catch{/* Each bounded recovery task remains independent. */}
    }
  },
  async queue(batch,env) {
    const parser=env.PARSER?.getByName?.('global-parser');
    for(const message of batch.messages){
      try{await processParseMessage(env,message,Math.floor(Date.now()/1000),parser);}
      catch{message.retry({delaySeconds:30});}
    }
  },
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch { return json({error: 'service_unavailable'}, 503); }
  }
};
