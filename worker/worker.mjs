const PUBLIC_PATH = '/yangdong-3d/';
const ASSETS = new Map([
  ['index.html', 'text/html; charset=utf-8'],
  ['viewer.css', 'text/css; charset=utf-8'],
  ['viewer.mjs', 'text/javascript; charset=utf-8'],
  ['scene.json', 'application/json; charset=utf-8'],
  ['model.ply', 'application/octet-stream'],
  ['playcanvas-2.22.1.mjs', 'text/javascript; charset=utf-8'],
  ['PLAYCANVAS_LICENSE.txt', 'text/plain; charset=utf-8'],
  ['robots.txt', 'text/plain; charset=utf-8'],
]);
const REQUEST_HEADERS = ['Range', 'If-None-Match', 'If-Modified-Since', 'If-Range'];
const RESPONSE_HEADERS = [
  'Content-Length', 'Content-Encoding', 'Cache-Control', 'ETag', 'Last-Modified',
  'Accept-Ranges', 'Content-Range', 'Expires', 'Vary', 'Age',
];
const CSP = "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob:; base-uri 'self'; form-action 'none'";

// These are trusted deployment settings, never values from the request.
function configuration(env) {
  const base = env?.ASSET_BASE_URL;
  const ancestor = env?.ALLOWED_FRAME_ORIGIN;
  if (typeof base !== 'string' || typeof ancestor !== 'string') return null;
  try {
    const asset = new URL(base), frame = new URL(ancestor);
    const validHTTPS = url => url.protocol === 'https:' && !url.username && !url.password
      && !url.search && !url.hash && url.hostname !== 'localhost'
      && !url.hostname.endsWith('.localhost') && !url.hostname.endsWith('.local')
      && !url.hostname.startsWith('[') && !/^(?:\d+\.){3}\d+$/.test(url.hostname)
      && url.hostname.includes('.')
      && url.hostname.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
    if (!validHTTPS(asset) || !validHTTPS(frame)) return null;
    // Canonical URLs reject whitespace, credentials, encoded paths and dot segments.
    if (base !== asset.href || !asset.pathname.endsWith('/')
      || !/^\/(?:[A-Za-z0-9_-]+\/)*$/.test(asset.pathname)) return null;
    if (ancestor !== frame.origin) return null;
    return { base: asset.href, ancestor: frame.origin };
  } catch { return null; }
}

function securityHeaders(ancestor) {
  return new Headers({
    'Content-Security-Policy': `${CSP}; frame-ancestors ${ancestor ? `'self' ${ancestor}` : "'none'"}`,
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex, nofollow',
  });
}

function errorResponse(status, message, method) {
  const headers = securityHeaders();
  headers.set('Content-Type', 'text/plain; charset=utf-8');
  headers.set('Cache-Control', 'no-store');
  if (status === 405) headers.set('Allow', 'GET, HEAD');
  return new Response(method === 'HEAD' ? null : message, { status, headers });
}

function publicRedirect(url, ancestor) {
  const headers = securityHeaders(ancestor);
  const view = url.searchParams.get('view');
  headers.set('Location', PUBLIC_PATH + (/^[0-5]$/.test(view ?? '') ? `?view=${view}` : ''));
  headers.set('Cache-Control', 'no-store');
  return new Response(null, { status: 302, headers });
}

async function discardBody(response) {
  try { await response.body?.cancel(); } catch {}
}

export async function handleRequest(request, env, fetchUpstream = fetch) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return errorResponse(405, 'Method not allowed', request.method);
  }
  let url;
  try { url = new URL(request.url); }
  catch { return errorResponse(400, 'Invalid request', request.method); }
  const redirect = url.pathname === '/' || url.pathname === '/yangdong-3d';
  if (!redirect && !url.pathname.startsWith(PUBLIC_PATH)) return errorResponse(404, 'Not found', request.method);
  const name = url.pathname.slice(PUBLIC_PATH.length) || 'index.html';
  if (!redirect && !ASSETS.has(name)) return errorResponse(404, 'Not found', request.method);
  const config = configuration(env);
  if (!config) return errorResponse(503, 'Public viewer is not configured', request.method);
  if (redirect) return publicRedirect(url, config.ancestor);

  // Create a fresh request to this one configured public directory. Client cookies,
  // credentials, forwarding headers and arbitrary query strings never cross it.
  const upstreamHeaders = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) upstreamHeaders.set(name, value);
  }
  let upstream;
  try {
    upstream = await fetchUpstream(config.base + name, {
      method: request.method, headers: upstreamHeaders, redirect: 'manual',
    });
  } catch {
    return errorResponse(502, 'Public model temporarily unavailable', request.method);
  }
  // Never follow or expose an upstream redirect, including a login destination.
  // Unexpected upstream error pages are also replaced with a generic response.
  if (upstream.redirected || ![200, 206, 304, 416].includes(upstream.status)) {
    await discardBody(upstream);
    return errorResponse(502, 'Public model temporarily unavailable', request.method);
  }
  const headers = securityHeaders(config.ancestor);
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const upstreamType = upstream.headers.get('Content-Type') || '';
  const multipartRange = upstream.status === 206
    && upstreamType.split(';', 1)[0].trim().toLowerCase() === 'multipart/byteranges';
  headers.set('Content-Type', multipartRange ? upstreamType : ASSETS.get(name));
  const noBody = request.method === 'HEAD' || upstream.status === 304 || upstream.status === 416;
  if (upstream.status === 416) headers.delete('Content-Length');
  if (noBody) await discardBody(upstream);
  // Passing the stream directly avoids buffering the 103.5 MB PLY in Worker
  // memory. The origin's validators, ranges and cache policy remain intact.
  return new Response(noBody ? null : upstream.body, { status: upstream.status, headers });
}

export default {
  fetch(request, env) { return handleRequest(request, env); },
};
