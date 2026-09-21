import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { handleRequest as configuredRequest } from './worker.mjs';

const ORIGIN = 'https://yangdong.example.workers.dev';
const UPSTREAM = 'https://assets.example.com/public-scene/';
const ENV = { ASSET_BASE_URL: UPSTREAM, ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com' };
const handleRequest = (request, fetcher) => configuredRequest(request, ENV, fetcher);
const req = (path, options) => new Request(ORIGIN + path, options);
const neverFetch = () => assert.fail('Rejected request must not reach upstream');

test('root and slash redirects stay relative and retain only a valid view', async () => {
  for (const path of ['/', '/yangdong-3d']) {
    for (const method of ['GET', 'HEAD']) {
      for (const [query, expected] of [
        ['', '/yangdong-3d/'], ['?view=0', '/yangdong-3d/?view=0'],
        ['?view=5&camera=private&next=https://elsewhere.invalid', '/yangdong-3d/?view=5'],
        ['?view=6', '/yangdong-3d/'], ['?view=-1', '/yangdong-3d/'],
        ['?view=1.0', '/yangdong-3d/'], ['?view=https://elsewhere.invalid', '/yangdong-3d/'],
      ]) {
        const response = await handleRequest(req(path + query, { method }), neverFetch);
        assert.equal(response.status, 302); assert.equal(response.headers.get('Location'), expected);
        assert.equal(await response.text(), '');
      }
    }
  }
});

test('only the exact public file allowlist reaches the fixed origin without query strings', async () => {
  const files = ['index.html', 'viewer.css', 'viewer.mjs', 'scene.json', 'model.ply',
    'playcanvas-2.22.1.mjs', 'PLAYCANVAS_LICENSE.txt', 'robots.txt'];
  for (const file of ['', ...files]) {
    let calls = 0;
    const response = await handleRequest(req('/yangdong-3d/' + file + '?view=4&url=https://elsewhere.invalid&camera=private'), async (url, options) => {
      calls++;
      assert.equal(url, UPSTREAM + (file || 'index.html'));
      assert.equal(options.redirect, 'manual'); assert.equal(options.method, 'GET');
      return new Response('public asset', { headers: { 'Cache-Control': 'public, max-age=3600' } });
    });
    assert.equal(response.status, 200); assert.equal(calls, 1);
    assert.equal(await response.text(), 'public asset');
    assert.equal(response.headers.get('Cache-Control'), 'public, max-age=3600');
    assert.match(response.headers.get('Content-Security-Policy'), /worker-src 'self' blob:/);
  }
});

test('CCTV, API, private, traversal and encoded alias paths are rejected before fetch', async () => {
  for (const path of ['/api/cameras', '/site/', '/site/splat/model.ply', '/recordings.html', '/model.ply',
    '/private/runtime.env', '/yangdong-3d/api/session', '/yangdong-3d/.env',
    '/yangdong-3d/../api/cameras', '/yangdong-3d/%2e%2e%2fapi/cameras',
    '/yangdong-3d/%2e%2e%5cruntime.env', '/yangdong-3d/%6dodel.ply',
    '/yangdong-3d//model.ply', '/yangdong-3d/model.ply/extra', '/yangdong-3d/%00', '/yangdong-3d/unknown.txt']) {
    const response = await handleRequest(req(path), neverFetch);
    assert.equal(response.status, 404, path);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  }
});

test('public HTML permits only self and the portfolio as frame ancestors, replacing conflicting upstream policy', async () => {
  for (const [method, status] of [['GET', 200], ['HEAD', 200], ['GET', 304]]) {
    const response = await handleRequest(req('/yangdong-3d/', { method }), async () => new Response(
      method === 'HEAD' || status === 304 ? null : '<html>public model</html>',
      { status, headers: { 'Content-Security-Policy': "frame-ancestors 'none'", 'X-Frame-Options': 'DENY' } },
    ));
    const csp = response.headers.get('Content-Security-Policy');
    const ancestors = csp.split(';').map(value => value.trim()).filter(value => value.startsWith('frame-ancestors'));
    assert.deepEqual(ancestors, ["frame-ancestors 'self' https://portfolio.example.com"]);
    assert.match(csp, /worker-src 'self' blob:/);
    assert.match(csp, /base-uri 'self'; form-action 'none'/);
    assert.equal(response.headers.get('X-Frame-Options'), null);
  }
});

test('all unsupported methods are rejected before even redirecting or fetching', async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    for (const path of ['/', '/yangdong-3d', '/yangdong-3d/', '/yangdong-3d/model.ply']) {
      const response = await handleRequest(req(path, { method }), neverFetch);
      assert.equal(response.status, 405); assert.equal(response.headers.get('Allow'), 'GET, HEAD');
    }
  }
});

test('only range and conditional headers are forwarded; cookies and credentials never pass through', async () => {
  const headers = {
    Cookie: 'private-session=secret', Authorization: 'Bearer secret', 'Proxy-Authorization': 'secret',
    'X-Forwarded-Host': 'private.invalid', 'X-Forwarded-For': '127.0.0.1', Host: 'private.invalid',
    Origin: 'https://private.invalid', Referer: 'https://private.invalid/cctv', 'User-Agent': 'client identity',
    'Accept-Encoding': 'gzip', 'X-Custom': 'private', Range: 'bytes=0-3',
    'If-None-Match': '"public-etag"', 'If-Modified-Since': 'Mon, 21 Sep 2026 00:00:00 GMT', 'If-Range': '"public-etag"',
  };
  await handleRequest(req('/yangdong-3d/model.ply', { headers }), async (_url, options) => {
    assert.deepEqual(Object.fromEntries(options.headers), {
      'if-modified-since': headers['If-Modified-Since'], 'if-none-match': headers['If-None-Match'],
      'if-range': headers['If-Range'], range: headers.Range,
    });
    assert.equal(options.body, undefined);
    return new Response('data');
  });
});

test('model response body is the original unread stream with cache and validator headers preserved', async () => {
  let pulls = 0;
  const stream = new ReadableStream({
    pull(controller) { pulls++; controller.enqueue(new Uint8Array([1, 2, 3])); controller.close(); },
  }, { highWaterMark: 0 });
  const headers = {
    'Content-Type': 'incorrect/type', 'Content-Length': '103505301', 'Cache-Control': 'public, max-age=3600',
    ETag: '"model-sha256"', 'Last-Modified': 'Mon, 21 Sep 2026 00:00:00 GMT', 'Accept-Ranges': 'bytes',
    'Set-Cookie': 'private-session=secret', Location: 'https://private.invalid/', Server: 'private server',
    'WWW-Authenticate': 'private realm', 'X-Internal': 'private',
  };
  const response = await handleRequest(req('/yangdong-3d/model.ply'), async () => new Response(stream, { headers }));
  assert.equal(response.status, 200); assert.strictEqual(response.body, stream); assert.equal(pulls, 0);
  for (const name of ['Content-Length', 'Cache-Control', 'ETag', 'Last-Modified', 'Accept-Ranges']) {
    assert.equal(response.headers.get(name), headers[name]);
  }
  assert.equal(response.headers.get('Content-Type'), 'application/octet-stream');
  for (const name of ['Set-Cookie', 'Location', 'Server', 'WWW-Authenticate', 'X-Internal']) assert.equal(response.headers.get(name), null);
  await response.body.cancel();
});

test('partial content, conditional 304, HEAD, and unsatisfiable ranges retain correct protocol semantics', async () => {
  const partial = await handleRequest(req('/yangdong-3d/model.ply', { headers: { Range: 'bytes=0-3' } }), async () => new Response('part', {
    status: 206, headers: { 'Content-Length': '4', 'Content-Range': 'bytes 0-3/103505301', 'Accept-Ranges': 'bytes', ETag: '"same"' },
  }));
  assert.equal(partial.status, 206); assert.equal(await partial.text(), 'part');
  assert.equal(partial.headers.get('Content-Range'), 'bytes 0-3/103505301');
  assert.equal(partial.headers.get('Content-Length'), '4');
  const unchanged = await handleRequest(req('/yangdong-3d/model.ply', { headers: { 'If-None-Match': '"same"' } }), async () => new Response(null, {
    status: 304, headers: { ETag: '"same"', 'Cache-Control': 'public, max-age=3600' },
  }));
  assert.equal(unchanged.status, 304); assert.equal(unchanged.body, null);
  assert.equal(unchanged.headers.get('ETag'), '"same"');
  const head = await handleRequest(req('/yangdong-3d/model.ply', { method: 'HEAD' }), async (_url, options) => {
    assert.equal(options.method, 'HEAD');
    return new Response(null, { headers: { 'Content-Length': '103505301', ETag: '"same"' } });
  });
  assert.equal(head.status, 200); assert.equal(head.body, null); assert.equal(head.headers.get('Content-Length'), '103505301');
  const invalid = await handleRequest(req('/yangdong-3d/model.ply'), async () => new Response('Range error', {
    status: 416, headers: { 'Content-Range': 'bytes */103505301', 'Content-Length': '11' },
  }));
  assert.equal(invalid.status, 416); assert.equal(invalid.body, null);
  assert.equal(invalid.headers.get('Content-Range'), 'bytes */103505301');
  assert.equal(invalid.headers.get('Content-Length'), null);
});

test('script and text MIME types are set locally while origin encoding and vary are preserved', async () => {
  for (const [name, mime] of [['viewer.mjs', 'text/javascript; charset=utf-8'], ['scene.json', 'application/json; charset=utf-8'],
    ['viewer.css', 'text/css; charset=utf-8'], ['robots.txt', 'text/plain; charset=utf-8'], ['index.html', 'text/html; charset=utf-8']]) {
    const response = await handleRequest(req('/yangdong-3d/' + name), async () => new Response('encoded', {
      headers: { 'Content-Encoding': 'gzip', Vary: 'Accept-Encoding', 'Cache-Control': 'public, max-age=0, must-revalidate' },
    }));
    assert.equal(response.headers.get('Content-Type'), mime);
    assert.equal(response.headers.get('Content-Encoding'), 'gzip'); assert.equal(response.headers.get('Vary'), 'Accept-Encoding');
    assert.equal(response.headers.get('Cache-Control'), 'public, max-age=0, must-revalidate');
  }
});

test('multipart range responses preserve their boundary MIME and the exact stream and per-part range headers', async () => {
  const contentType = 'multipart/byteranges; boundary="public-model-parts"';
  const text = '--public-model-parts\r\nContent-Type: application/octet-stream\r\nContent-Range: bytes 0-1/6\r\n\r\nab\r\n'
    + '--public-model-parts\r\nContent-Type: application/octet-stream\r\nContent-Range: bytes 4-5/6\r\n\r\nef\r\n--public-model-parts--\r\n';
  const bytes = new TextEncoder().encode(text);
  const stream = new ReadableStream({ start(controller) { controller.enqueue(bytes); controller.close(); } });
  const response = await handleRequest(req('/yangdong-3d/model.ply', { headers: { Range: 'bytes=0-1,4-5' } }), async (_url, options) => {
    assert.equal(options.headers.get('Range'), 'bytes=0-1,4-5');
    return new Response(stream, { status: 206, headers: {
      'Content-Type': contentType, 'Content-Length': String(bytes.byteLength), 'Accept-Ranges': 'bytes',
      ETag: '"multipart-model"', 'Cache-Control': 'public, max-age=3600',
    } });
  });
  assert.equal(response.status, 206); assert.strictEqual(response.body, stream);
  assert.equal(response.headers.get('Content-Type'), contentType);
  assert.equal(response.headers.get('Content-Length'), String(bytes.byteLength));
  assert.equal(response.headers.get('Accept-Ranges'), 'bytes');
  assert.equal(response.headers.get('ETag'), '"multipart-model"');
  assert.equal(response.headers.get('Cache-Control'), 'public, max-age=3600');
  assert.equal(await response.text(), text);
  for (const [status, mime] of [[200, contentType], [206, 'text/html'], [206, 'multipart/mixed; boundary=other']]) {
    const ordinary = await handleRequest(req('/yangdong-3d/model.ply'), async () => new Response('data', {
      status, headers: { 'Content-Type': mime },
    }));
    assert.equal(ordinary.headers.get('Content-Type'), 'application/octet-stream');
  }
});

test('upstream redirects and unexpected error pages fail closed without their locations or body', async () => {
  for (const status of [301, 302, 303, 307, 308, 401, 403, 404, 500]) {
    let cancelled = false;
    const body = new ReadableStream({ cancel() { cancelled = true; } });
    const response = await handleRequest(req('/yangdong-3d/index.html'), async (_url, options) => {
      assert.equal(options.redirect, 'manual');
      return new Response(body, { status, headers: { Location: 'https://private.invalid/cctv', 'Set-Cookie': 'secret' } });
    });
    assert.equal(response.status, 502); assert.equal(cancelled, true);
    assert.equal(response.headers.get('Location'), null); assert.equal(response.headers.get('Set-Cookie'), null);
    assert.doesNotMatch(await response.text(), /private|secret|cctv/);
  }
});

test('an already-followed redirect or a network exception also fails closed, including HEAD', async () => {
  const upstream = new Response('private redirected page');
  Object.defineProperty(upstream, 'redirected', { value: true });
  assert.equal((await handleRequest(req('/yangdong-3d/'), async () => upstream)).status, 502);
  for (const method of ['GET', 'HEAD']) {
    const response = await handleRequest(req('/yangdong-3d/model.ply', { method }), async () => { throw new Error('secret upstream address'); });
    assert.equal(response.status, 502); assert.equal(response.headers.get('Cache-Control'), 'no-store');
    const text = await response.text(); assert.doesNotMatch(text, /secret|upstream/);
    if (method === 'HEAD') assert.equal(text, '');
  }
});

test('missing or invalid deployment settings fail closed before fetching or redirecting', async () => {
  const invalid = [undefined, {}, { ...ENV, ASSET_BASE_URL: '' }, { ...ENV, ALLOWED_FRAME_ORIGIN: '' },
    { ...ENV, ASSET_BASE_URL: 'http://assets.example.com/' },
    { ...ENV, ASSET_BASE_URL: 'https://user:password@assets.example.com/' },
    { ...ENV, ASSET_BASE_URL: UPSTREAM + '?token=secret' },
    { ...ENV, ASSET_BASE_URL: UPSTREAM + '#fragment' },
    { ...ENV, ASSET_BASE_URL: 'https://assets.example.com/no-trailing-slash' },
    { ...ENV, ASSET_BASE_URL: 'https://assets.example.com/scene/../private/' },
    { ...ENV, ASSET_BASE_URL: 'https://assets.example.com/%70rivate/' },
    { ...ENV, ASSET_BASE_URL: 'https://127.0.0.1/' },
    { ...ENV, ASSET_BASE_URL: 'https://[::1]/' },
    { ...ENV, ASSET_BASE_URL: 'https://localhost/' },
    { ...ENV, ASSET_BASE_URL: 'https://device.local/' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: '*' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://*.example.com' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://portfolio..example.com' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'http://portfolio.example.com' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com/project' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com/' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com?frame=any' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com#frame' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: 'https://user@portfolio.example.com' },
    { ...ENV, ALLOWED_FRAME_ORIGIN: "https://portfolio.example.com; frame-ancestors *" },
  ];
  for (const env of invalid) {
    for (const path of ['/', '/yangdong-3d/model.ply']) {
      for (const method of ['GET', 'HEAD']) {
        const response = await configuredRequest(req(path, { method }), env, neverFetch);
        assert.equal(response.status, 503);
        assert.equal(response.headers.get('Cache-Control'), 'no-store');
        assert.equal(response.headers.get('Location'), null);
        assert.doesNotMatch(await response.text(), /secret|password|assets\.example/);
      }
    }
  }
});

test('configuration comes only from trusted bindings and the default handler passes those bindings', async () => {
  const env = { ASSET_BASE_URL: 'https://other-assets.example.com/', ALLOWED_FRAME_ORIGIN: 'https://other-portfolio.example.com' };
  const response = await configuredRequest(req('/yangdong-3d/model.ply?ASSET_BASE_URL=https://evil.invalid/&ALLOWED_FRAME_ORIGIN=*'), env, async (url) => {
    assert.equal(url, 'https://other-assets.example.com/model.ply');
    return new Response('model');
  });
  assert.match(response.headers.get('Content-Security-Policy'), /frame-ancestors 'self' https:\/\/other-portfolio\.example\.com$/);
  assert.doesNotMatch(response.headers.get('Content-Security-Policy'), /evil|portfolio\.example\.com;/);
  // A redirect exercises the deployed entry point without making a network call.
  const redirected = await worker.fetch(req('/?view=3'), env);
  assert.equal(redirected.status, 302);
  assert.equal(redirected.headers.get('Location'), '/yangdong-3d/?view=3');
  assert.match(redirected.headers.get('Content-Security-Policy'), /other-portfolio\.example\.com/);
  assert.equal((await worker.fetch(req('/'), {})).status, 503);
});
