const CACHE_CONTROL = 'public, max-age=0, must-revalidate';
const ETAG = '"[\\x21\\x23-\\x7e\\x80-\\xff]*"';
const STRONG_ETAG = new RegExp(`^${ETAG}$`);
const ETAG_LIST = new RegExp(`^(?:W/)?${ETAG}(?:[ \\t]*,[ \\t]*(?:W/)?${ETAG})*$`);

function unavailable(request, headers, status = 502) {
  const safe = new Headers(headers);
  safe.set('Content-Type', 'text/plain; charset=utf-8');
  safe.set('Cache-Control', 'no-store');
  return new Response(request.method === 'HEAD' ? null : 'Public model temporarily unavailable', { status, headers: safe });
}

function validMetadata(object) {
  return object && Number.isSafeInteger(object.size) && object.size >= 0
    && typeof object.etag === 'string' && object.httpEtag === `"${object.etag}"`
    && STRONG_ETAG.test(object.httpEtag) && object.uploaded instanceof Date
    && Number.isFinite(object.uploaded.getTime());
}

function seconds(date) { return Math.floor(date.getTime() / 1000); }

function httpDate(value) {
  // HTTP-date includes the two obsolete formats that recipients must accept.
  if (!value || !/^(?:[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT|[A-Za-z]+, \d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}:\d{2} GMT|[A-Za-z]{3} [A-Za-z]{3} [ \d]\d \d{2}:\d{2}:\d{2} \d{4})$/.test(value)) return NaN;
  // asctime has no written zone but HTTP dates are always UTC.
  return Math.floor(Date.parse(value.endsWith(' GMT') ? value : value + ' GMT') / 1000);
}

function unchanged(request, object) {
  const ifNoneMatch = request.headers.get('If-None-Match');
  if (ifNoneMatch !== null) {
    const value = ifNoneMatch.trim();
    if (value === '*') return true;
    if (!ETAG_LIST.test(value)) return false;
    return [...value.matchAll(new RegExp(`(?:W/)?${ETAG}`, 'g'))]
      .some(([tag]) => tag.replace(/^W\//, '') === object.httpEtag);
  }
  // If-None-Match takes precedence even if it did not match or was malformed.
  return seconds(object.uploaded) <= httpDate(request.headers.get('If-Modified-Since'));
}

function allowRange(request, object) {
  const value = request.headers.get('If-Range');
  if (value === null) return true;
  if (STRONG_ETAG.test(value)) return value === object.httpEtag;
  if (value.startsWith('W/')) return false;
  // If-Range date comparison is exact, unlike If-Modified-Since.
  return httpDate(value) === seconds(object.uploaded);
}

function byteRange(value, size) {
  // Ignore unsupported units, invalid syntax and multipart ranges. RFC 9110
  // permits serving the full representation; never label a full body as 206.
  const match = /^bytes=(\d*)-(\d*)$/i.exec(value?.trim() ?? '');
  if (!match || (!match[1] && !match[2])) return null;
  const total = BigInt(size);
  let start, end;
  if (!match[1]) {
    const suffix = BigInt(match[2]);
    if (suffix === 0n || total === 0n) return false;
    start = suffix >= total ? 0n : total - suffix;
    end = total - 1n;
  } else {
    start = BigInt(match[1]);
    end = match[2] ? BigInt(match[2]) : total - 1n;
    if (start >= total || end < start) return false;
    if (end >= total) end = total - 1n;
  }
  return { offset: Number(start), length: Number(end - start + 1n) };
}

export async function serveR2Model(request, env, securityHeaders) {
  const bucket = env.MODEL_BUCKET, key = env.MODEL_KEY;
  if (!bucket || typeof bucket.head !== 'function' || typeof bucket.get !== 'function'
    || typeof key !== 'string' || key.length > 1024
    || !/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*\.ply$/.test(key)
    || key.split('/').some(part => part === '.' || part === '..')) {
    return unavailable(request, securityHeaders, 503);
  }

  let body;
  try {
    const object = await bucket.head(key);
    if (!validMetadata(object)) return unavailable(request, securityHeaders);
    const headers = new Headers(securityHeaders);
    headers.set('Content-Type', 'application/octet-stream');
    headers.set('Cache-Control', CACHE_CONTROL);
    headers.set('ETag', object.httpEtag);
    headers.set('Last-Modified', object.uploaded.toUTCString());
    headers.set('Accept-Ranges', 'bytes');
    // Do not copy uploaded HTTP/custom metadata or any arbitrary headers.
    if (unchanged(request, object)) return new Response(null, { status: 304, headers });

    // Range is defined for GET only; HEAD reports the full representation.
    const range = request.method === 'GET' && allowRange(request, object)
      ? byteRange(request.headers.get('Range'), object.size) : null;
    if (range === false) {
      headers.set('Content-Range', `bytes */${object.size}`);
      headers.set('Content-Length', '0');
      return new Response(null, { status: 416, headers });
    }
    headers.set('Content-Length', String(range ? range.length : object.size));
    if (request.method === 'HEAD') return new Response(null, { headers });

    const options = { onlyIf: { etagMatches: object.etag } };
    if (range) options.range = range;
    const result = await bucket.get(key, options);
    body = result?.body;
    // A replacement/deletion between head and get must not combine stale
    // validators or Content-Length with a different object's bytes.
    if (!body || !validMetadata(result) || result.httpEtag !== object.httpEtag
      || result.size !== object.size || result.uploaded.getTime() !== object.uploaded.getTime()
      || result.version !== object.version) {
      await body?.cancel();
      return unavailable(request, securityHeaders);
    }
    if (range) headers.set('Content-Range', `bytes ${range.offset}-${range.offset + range.length - 1}/${object.size}`);
    // Pass the R2 stream through untouched, without loading the model in memory.
    return new Response(body, { status: range ? 206 : 200, headers });
  } catch {
    try { await body?.cancel(); } catch {}
    return unavailable(request, securityHeaders);
  }
}
