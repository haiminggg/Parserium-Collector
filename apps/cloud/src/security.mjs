export const SESSION_COOKIE = '__Host-parserium_cloud';
export const LOGIN_COOKIE = '__Host-parserium_login';
export const CSRF_COOKIE = '__Host-parserium_csrf';
export async function digest(value) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}
export function randomToken() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), b => b.toString(16).padStart(2,'0')).join('');
}
export function readTokenCookie(request,name) {
  const values = (request.headers.get('Cookie') ?? '').split(';').map(v => v.trim())
    .filter(v => v.startsWith(`${name}=`));
  if (values.length !== 1) return null;
  const value = values[0].slice(name.length + 1);
  return /^[a-f0-9]{64}$/.test(value) ? value : null;
}
export function cookie(name,value,maxAge,httpOnly = true) {
  return `${name}=${value}; Path=/; Max-Age=${maxAge}; Secure; SameSite=Lax${httpOnly ? '; HttpOnly' : ''}`;
}
