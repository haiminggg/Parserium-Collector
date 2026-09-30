import * as oauth from 'oauth4webapi';
const ISSUER = new URL('https://accounts.google.com');
const transport = {signal: () => AbortSignal.timeout(10000)};
let metadataCache;

export function googleConfig(env) {
  const origin = new URL(env.APP_ORIGIN);
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash ||
      typeof env.GOOGLE_CLIENT_ID !== 'string' || !/^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$/.test(env.GOOGLE_CLIENT_ID) ||
      typeof env.GOOGLE_CLIENT_SECRET !== 'string' || !env.GOOGLE_CLIENT_SECRET.trim()) throw new Error('OAuth configuration unavailable');
  return {origin:origin.origin,redirectUri:`${origin.origin}/api/cloud/v1/auth/callback`,
    client:{client_id:env.GOOGLE_CLIENT_ID,id_token_signed_response_alg:'RS256'},secret:env.GOOGLE_CLIENT_SECRET};
}

export async function googleMetadata() {
  if (metadataCache && metadataCache.until > Date.now()) return metadataCache.value;
  const response = await oauth.discoveryRequest(ISSUER,{algorithm:'oidc',...transport});
  const value = await oauth.processDiscoveryResponse(ISSUER,response);
  metadataCache = {value,until:Date.now()+3600000};
  return value;
}

export async function beginGoogle(config) {
  const metadata = await googleMetadata();
  const state = oauth.generateRandomState(), nonce = oauth.generateRandomNonce();
  const verifier = oauth.generateRandomCodeVerifier();
  const url = new URL(metadata.authorization_endpoint);
  url.search = new URLSearchParams({client_id:config.client.client_id,redirect_uri:config.redirectUri,
    response_type:'code',scope:'openid email profile',state,nonce,
    code_challenge:await oauth.calculatePKCECodeChallenge(verifier),code_challenge_method:'S256'}).toString();
  return {url:url.href,state,nonce,verifier};
}

export async function finishGoogle(config,url,transaction,state) {
  const metadata = await googleMetadata();
  const parameters = oauth.validateAuthResponse(metadata,config.client,url,state);
  const response = await oauth.authorizationCodeGrantRequest(metadata,config.client,
    oauth.ClientSecretPost(config.secret),parameters,config.redirectUri,transaction.code_verifier,transport);
  const tokens = await oauth.processAuthorizationCodeResponse(metadata,config.client,response,
    {expectedNonce:transaction.nonce,requireIdToken:true});
  // Claim validation above is not signature verification. Both must complete before admission.
  await oauth.validateApplicationLevelSignature(metadata,response,transport);
  const claims = oauth.getValidatedIdTokenClaims(tokens);
  if (!claims || claims.iss !== ISSUER.href.replace(/\/$/,'') || claims.email_verified !== true ||
      typeof claims.email !== 'string' || typeof claims.sub !== 'string' || !claims.sub) throw new Error('Identity rejected');
  return claims;
}
