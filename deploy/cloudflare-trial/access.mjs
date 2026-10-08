export function allowed(request, token) {
  const url = new URL(request.url);
  return typeof token === 'string' && token.length === 64 &&
    request.method === 'POST' && url.pathname === '/run' && !url.search &&
    request.headers.get('Authorization') === `Bearer ${token}`;
}
