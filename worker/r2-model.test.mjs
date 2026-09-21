import test from 'node:test';
import assert from 'node:assert/strict';
import { handleRequest } from './worker.mjs';

const KEY = 'models/model-example.compressed.ply';
const ENV = { ASSET_BASE_URL: 'https://assets.example.com/public-scene/', ALLOWED_FRAME_ORIGIN: 'https://portfolio.example.com' };
const META = { size: 10, etag: 'model-etag', httpEtag: '"model-etag"', uploaded: new Date('2026-09-21T01:02:03.456Z'), version: 'version-1' };
const BYTES = new TextEncoder().encode('0123456789');
const request = (headers = {}, method = 'GET', path = '/yangdong-3d/model.ply') => new Request('https://viewer.example.com' + path, { method, headers });
const noOrigin = () => assert.fail('R2 requests must never fall back to the origin');

function fixture({ metadata = META, headError = false, getError = false, resultMetadata } = {}) {
  const calls = { head: [], get: [], pulls: 0, cancelled: 0, body: null };
  const bucket = {
    async head(key) {
      calls.head.push(key);
      if (headError) throw new Error('private bucket diagnostic');
      return metadata;
    },
    async get(key, options) {
      calls.get.push({ key, options });
      if (getError) throw new Error('private storage diagnostic');
      const { offset = 0, length = BYTES.length } = options.range ?? {};
      const body = new ReadableStream({
        pull(controller) { calls.pulls++; controller.enqueue(BYTES.slice(offset, offset + length)); controller.close(); },
        cancel() { calls.cancelled++; },
      }, { highWaterMark: 0 });
      calls.body = body;
      return {
        ...metadata, ...resultMetadata, body,
        arrayBuffer() { assert.fail('The entire model must not be buffered'); },
        text() { assert.fail('The model must not be decoded'); },
        writeHttpMetadata() { assert.fail('Uploaded metadata must not set response headers'); },
      };
    },
  };
  const env = { ...ENV, MODEL_BUCKET: bucket, MODEL_KEY: KEY };
  return { calls, bucket, env, fetch: (req = request()) => handleRequest(req, env, noOrigin) };
}

function checkSecurity(response) {
  assert.match(response.headers.get('Content-Security-Policy'), /frame-ancestors 'self' https:\/\/portfolio\.example\.com$/);
  assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
  assert.equal(response.headers.get('Referrer-Policy'), 'no-referrer');
  assert.equal(response.headers.get('X-Robots-Tag'), 'noindex, nofollow');
  for (const header of ['Set-Cookie', 'Location', 'X-Frame-Options', 'X-Internal']) assert.equal(response.headers.get(header), null);
}

test('R2 GET passes the unread body stream directly and uses only known metadata', async () => {
  const f = fixture({ metadata: { ...META, httpMetadata: { contentType: 'text/html', cacheControl: 'private', contentEncoding: 'gzip' }, customMetadata: { 'Set-Cookie': 'secret' } } });
  const response = await f.fetch(request({ Cookie: 'secret', Authorization: 'secret' }));
  assert.equal(response.status, 200);
  assert.strictEqual(response.body, f.calls.body);
  assert.equal(f.calls.pulls, 0);
  assert.deepEqual(f.calls.head, [KEY]);
  assert.deepEqual(f.calls.get, [{ key: KEY, options: { onlyIf: { etagMatches: META.etag } } }]);
  assert.equal(response.headers.get('Content-Length'), '10');
  assert.equal(response.headers.get('Content-Type'), 'application/octet-stream');
  assert.equal(response.headers.get('ETag'), META.httpEtag);
  assert.equal(response.headers.get('Last-Modified'), 'Mon, 21 Sep 2026 01:02:03 GMT');
  assert.equal(response.headers.get('Accept-Ranges'), 'bytes');
  assert.equal(response.headers.get('Cache-Control'), 'public, max-age=0, must-revalidate');
  assert.equal(response.headers.get('Content-Encoding'), null);
  checkSecurity(response);
  assert.equal(await response.text(), '0123456789');
});

test('R2 HEAD reads metadata only, ignores Range, and sends no body', async () => {
  const f = fixture();
  const response = await f.fetch(request({ Range: 'bytes=2-4' }, 'HEAD'));
  assert.equal(response.status, 200); assert.equal(response.body, null);
  assert.equal(response.headers.get('Content-Length'), '10');
  assert.equal(response.headers.get('Content-Range'), null);
  assert.deepEqual(f.calls.head, [KEY]); assert.equal(f.calls.get.length, 0);
  checkSecurity(response);
});

test('R2 strong, weak, list, and wildcard If-None-Match return 304 without a body read', async () => {
  for (const method of ['GET', 'HEAD']) {
    for (const value of ['"model-etag"', 'W/"model-etag"', '"other", W/"model-etag"', '*']) {
      const f = fixture();
      const response = await f.fetch(request({ 'If-None-Match': value, Range: 'bytes=99-' }, method));
      assert.equal(response.status, 304, value); assert.equal(response.body, null);
      assert.equal(response.headers.get('ETag'), META.httpEtag);
      assert.equal(response.headers.get('Cache-Control'), 'public, max-age=0, must-revalidate');
      assert.equal(response.headers.get('Content-Length'), null);
      assert.equal(response.headers.get('Content-Range'), null);
      assert.equal(f.calls.get.length, 0); checkSecurity(response);
    }
  }
  const f = fixture({ metadata: { ...META, etag: 'model,etag', httpEtag: '"model,etag"' } });
  assert.equal((await f.fetch(request({ 'If-None-Match': '"unrelated", W/"model,etag"' }))).status, 304);
});

test('If-None-Match takes precedence over If-Modified-Since and rejects malformed tag lists', async () => {
  for (const value of ['"other"', 'model-etag', 'broken "model-etag"', '"model-etag",']) {
    const f = fixture();
    const response = await f.fetch(request({ 'If-None-Match': value, 'If-Modified-Since': 'Tue, 22 Sep 2026 01:02:03 GMT' }));
    assert.equal(response.status, 200, value); assert.equal(f.calls.get.length, 1);
    await response.body.cancel();
  }
});

test('R2 If-Modified-Since uses HTTP second precision, including obsolete dates', async () => {
  for (const value of ['Mon, 21 Sep 2026 01:02:03 GMT', 'Tue, 22 Sep 2026 01:02:03 GMT', 'Monday, 21-Sep-26 01:02:03 GMT', 'Mon Sep 21 01:02:03 2026']) {
    const f = fixture();
    assert.equal((await f.fetch(request({ 'If-Modified-Since': value }))).status, 304, value);
    assert.equal(f.calls.get.length, 0);
  }
  for (const value of ['Mon, 21 Sep 2026 01:02:02 GMT', 'invalid', '2026-09-22']) {
    const response = await fixture().fetch(request({ 'If-Modified-Since': value }));
    assert.equal(response.status, 200, value); await response.body.cancel();
  }
});

test('R2 byte ranges support explicit, open, suffix, oversized, and large numeric boundaries', async () => {
  for (const [range, expected, offset, length] of [
    ['bytes=2-4', '234', 2, 3], ['bytes=7-', '789', 7, 3], ['bytes=-3', '789', 7, 3],
    ['bytes=7-999999999999999999999999999999', '789', 7, 3],
    ['bytes=-999999999999999999999999999999', '0123456789', 0, 10],
    ['bytes=0-0', '0', 0, 1],
  ]) {
    const f = fixture(), response = await f.fetch(request({ Range: range }));
    assert.equal(response.status, 206, range);
    assert.equal(response.headers.get('Content-Range'), `bytes ${offset}-${offset + length - 1}/10`);
    assert.equal(response.headers.get('Content-Length'), String(length));
    assert.deepEqual(f.calls.get[0].options, { onlyIf: { etagMatches: META.etag }, range: { offset, length } });
    assert.strictEqual(response.body, f.calls.body); checkSecurity(response);
    assert.equal(await response.text(), expected);
  }
});

test('unsatisfiable ranges return 416 and never fetch an R2 body', async () => {
  for (const range of ['bytes=10-', 'bytes=999999999999999999999999999-', 'bytes=4-2', 'bytes=-0']) {
    const f = fixture(), response = await f.fetch(request({ Range: range }));
    assert.equal(response.status, 416, range); assert.equal(response.body, null);
    assert.equal(response.headers.get('Content-Range'), 'bytes */10');
    assert.equal(response.headers.get('Content-Length'), '0');
    assert.equal(f.calls.get.length, 0); checkSecurity(response);
  }
  const f = fixture({ metadata: { ...META, size: 0 } });
  assert.equal((await f.fetch(request({ Range: 'bytes=0-' }))).status, 416);
});

test('multipart, unsupported units, and malformed Range headers are ignored with a full 200', async () => {
  for (const range of ['bytes=0-1,4-5', 'items=0-1', 'bytes=-', 'bytes=nope', 'bytes=1.5-4', 'bytes=1--4']) {
    const f = fixture(), response = await f.fetch(request({ Range: range }));
    assert.equal(response.status, 200, range);
    assert.equal(response.headers.get('Content-Range'), null);
    assert.equal(response.headers.get('Content-Length'), '10');
    assert.equal(f.calls.get[0].options.range, undefined);
    assert.equal(await response.text(), '0123456789');
  }
});

test('If-Range permits only the current strong ETag or exact Last-Modified date', async () => {
  for (const [ifRange, status] of [
    ['"model-etag"', 206], ['Mon, 21 Sep 2026 01:02:03 GMT', 206],
    ['"other"', 200], ['W/"model-etag"', 200], ['Mon, 21 Sep 2026 01:02:02 GMT', 200],
    ['Tue, 22 Sep 2026 01:02:03 GMT', 200], ['invalid', 200],
  ]) {
    const f = fixture(), response = await f.fetch(request({ Range: 'bytes=2-4', 'If-Range': ifRange }));
    assert.equal(response.status, status, ifRange);
    assert.equal(await response.text(), status === 206 ? '234' : '0123456789');
  }
});

test('missing objects and storage errors fail closed without leaking diagnostics or falling back', async () => {
  for (const options of [{ metadata: null }, { headError: true }, { metadata: { ...META, httpEtag: 'unquoted' } }, { metadata: { ...META, size: NaN } }, { getError: true }]) {
    for (const method of options.getError ? ['GET'] : ['GET', 'HEAD']) {
      const f = fixture(options), response = await f.fetch(request({}, method));
      assert.equal(response.status, 502); assert.equal(response.headers.get('Cache-Control'), 'no-store');
      assert.equal(response.headers.get('ETag'), null); assert.equal(response.headers.get('Content-Length'), null);
      const text = await response.text(); assert.doesNotMatch(text, /private|bucket|diagnostic|storage/);
      if (method === 'HEAD') assert.equal(response.body, null);
      checkSecurity(response);
    }
  }
});

test('a deleted or changed object between HEAD and GET cannot send stale headers with new bytes', async () => {
  for (const result of [null, { ...META }, { ...META, etag: 'changed', httpEtag: '"changed"' }]) {
    const f = fixture(); f.bucket.get = async () => result;
    const response = await f.fetch();
    assert.equal(response.status, 502); checkSecurity(response);
  }
  for (const resultMetadata of [{ size: 11 }, { version: 'version-2' }, { uploaded: new Date('2026-09-22') }]) {
    const f = fixture({ resultMetadata }), response = await f.fetch();
    assert.equal(response.status, 502); assert.equal(f.calls.cancelled, 1); assert.equal(f.calls.pulls, 0);
    assert.equal(response.headers.get('Content-Length'), null);
  }
});

test('invalid R2 bindings or keys fail closed while an absent binding retains the existing proxy', async () => {
  const f = fixture();
  const invalid = [
    { MODEL_BUCKET: null }, { MODEL_BUCKET: {} }, { MODEL_BUCKET: { head() {} } },
    ...[undefined, '', 'https://evil.invalid/model.ply', '../model.ply', '/model.ply', 'models/../model.ply', 'models//model.ply', 'models/model.ply?private', 'models/model.ply\n', 'x'.repeat(1025) + '.ply'].map(MODEL_KEY => ({ MODEL_KEY })),
  ];
  for (const overrides of invalid) {
    for (const method of ['GET', 'HEAD']) {
      const response = await handleRequest(request({}, method), { ...f.env, ...overrides }, noOrigin);
      assert.equal(response.status, 503); assert.equal(response.headers.get('Cache-Control'), 'no-store');
      checkSecurity(response); if (method === 'HEAD') assert.equal(response.body, null);
    }
  }
  const response = await handleRequest(request(), ENV, async url => {
    assert.equal(url, ENV.ASSET_BASE_URL + 'model.ply'); return new Response('origin model');
  });
  assert.equal(await response.text(), 'origin model');
});

test('only the exact model asset uses a trusted R2 key; all other allowed assets retain their proxy', async () => {
  const f = fixture();
  const response = await f.fetch(request({}, 'GET', '/yangdong-3d/model.ply?MODEL_KEY=private.ply&key=other.ply&url=https://evil.invalid/'));
  assert.deepEqual(f.calls.head, [KEY]); assert.equal(f.calls.get[0].key, KEY); await response.body.cancel();
  for (const file of ['index.html', 'viewer.css', 'viewer.mjs', 'scene.json', 'playcanvas-2.22.1.mjs', 'PLAYCANVAS_LICENSE.txt', 'robots.txt']) {
    const before = f.calls.head.length;
    const asset = await handleRequest(request({}, 'GET', '/yangdong-3d/' + file), f.env, async url => {
      assert.equal(url, ENV.ASSET_BASE_URL + file); return new Response('asset');
    });
    assert.equal(asset.status, 200); assert.equal(f.calls.head.length, before);
  }
  for (const path of ['/yangdong-3d/%6dodel.ply', '/yangdong-3d/model.ply/extra', '/private/model.ply']) {
    assert.equal((await f.fetch(request({}, 'GET', path))).status, 404);
  }
});
