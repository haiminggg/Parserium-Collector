import {beginGoogle,finishGoogle,googleConfig} from './google.mjs';
import {consumeLogin} from './login-storage.mjs';
import {completeIdentity} from './identity.mjs';
import {digest,randomToken,readTokenCookie,cookie,LOGIN_COOKIE,SESSION_COOKIE,CSRF_COOKIE} from './security.mjs';
const responseHeaders = {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'};
const error = (code,status) => Response.json({error:code},{status,headers:responseHeaders});
function redirect(location,cookies=[]) {
  const headers = new Headers({...responseHeaders,Location:location});
  for (const value of cookies) headers.append('Set-Cookie',value);
  return new Response(null,{status:303,headers});
}
async function limitAuth(request,env,action) {
  try {
    // Trust only Cloudflare's ingress header, never X-Forwarded-For.
    // Missing ingress information shares a restrictive fallback bucket.
    const address=request.headers.get('CF-Connecting-IP') || 'unknown';
    const key=`${action}:${await digest(address)}`;
    const result=await env.AUTH_LIMITER.limit({key});
    if(result.success===true) return null;
    return Response.json({error:'too_many_requests'},{status:429,
      headers:{...responseHeaders,'Retry-After':'60'}});
  } catch { return error('service_unavailable',503); }
}
async function smallForm(request) {
  if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/x-www-form-urlencoded') return error('form_required',415);
  if (!request.body) return new URLSearchParams();
  const reader = request.body.getReader();
  const chunks=[]; let size=0;
  try {
    while (true) {
      const {value,done}=await reader.read(); if (done) break;
      size+=value.byteLength;
      if (size>2048) { await reader.cancel(); return error('request_too_large',413); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const body=new Uint8Array(size); let offset=0;
  for (const chunk of chunks) {body.set(chunk,offset);offset+=chunk.byteLength;}
  return new URLSearchParams(new TextDecoder().decode(body));
}

export async function authRoute(request,env) {
  const url = new URL(request.url);
  let config;
  try { config = googleConfig(env); } catch { return error('auth_not_configured',503); }
  if (!env.DB) return error('service_unavailable',503);
  if (url.origin !== config.origin) return error('forbidden',403);
  if (url.pathname.endsWith('/login') && request.method === 'POST') {
    if (request.headers.get('Origin') !== config.origin) return error('forbidden',403);
    const limited=await limitAuth(request,env,'login'); if(limited) return limited;
    const form=await smallForm(request); if (form instanceof Response) return form;
    if (form.getAll('invite').length>1 || [...form.keys()].some(key=>key!=='invite')) return error('invalid_form',400);
    const invitation=form.get('invite') || null;
    if (invitation && !/^[a-f0-9]{64}$/.test(invitation)) return error('invalid_invitation',400);
    const now=Math.floor(Date.now()/1000), invitationDigest=invitation ? await digest(invitation) : null;
    if (invitationDigest && !await env.DB.prepare('SELECT 1 FROM invitations WHERE token_digest=? AND expires_at>? AND redeemed_at IS NULL').bind(invitationDigest,now).first()) return error('invitation_required',403);
    const flow=await beginGoogle(config), browser=randomToken();
    const oldBrowser=readTokenCookie(request,LOGIN_COOKIE);
    await env.DB.batch([
      env.DB.prepare('DELETE FROM login_transactions WHERE browser_digest=?').bind(oldBrowser ? await digest(oldBrowser) : ''),
      env.DB.prepare('INSERT INTO login_transactions VALUES (?,?,?,?,?,?,?)').bind(await digest(browser),await digest(flow.state),flow.nonce,flow.verifier,invitationDigest,now,now+600),
    ]);
    return redirect(flow.url,[cookie(LOGIN_COOKIE,browser,600)]);
  }
  if (url.pathname.endsWith('/callback') && request.method === 'GET') {
    const limited=await limitAuth(request,env,'callback'); if(limited) return limited;
    const failed = () => redirect(`${config.origin}/?auth=failed`,[cookie(LOGIN_COOKIE,'',0)]);
    const browser=readTokenCookie(request,LOGIN_COOKIE), states=url.searchParams.getAll('state');
    if (!browser || states.length!==1 || !/^[A-Za-z0-9_-]{43}$/.test(states[0])) return failed();
    try {
      const transaction=await consumeLogin(env.DB,await digest(browser),await digest(states[0]),Math.floor(Date.now()/1000));
      if (!transaction || url.searchParams.has('error')) return failed();
      const claims=await finishGoogle(config,url,transaction,states[0]);
      const issued=await completeIdentity(env.DB,claims,transaction.invitation_digest,Math.floor(Date.now()/1000));
      if (!issued) return failed();
      return redirect(`${config.origin}/`,[cookie(LOGIN_COOKIE,'',0),cookie(SESSION_COOKIE,issued.token,86400),cookie(CSRF_COOKIE,issued.csrf,86400,false)]);
    } catch { return failed(); }
  }
  return error('not_found',404);
}
