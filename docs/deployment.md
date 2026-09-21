# Public deployment

The current [public 3D demo](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/) uses a Cloudflare Worker in front of a dedicated public asset directory. The model is streamed from the existing source host. Creating that address and enabling its portfolio iframe did not change the existing private camera service, its authentication or its routes. This repository contains a portable public-only extraction; it contains no source hostname, private service configuration, account ID, OAuth token or deployment credentials.

**R2 status, 2026-09-21:** the optional model-serving code and local tests are prepared. Account activation and production deployment are pending; the live demo has not been switched to R2. No R2 speed improvement is claimed by this preparation.

## Prepare the asset directory

Follow [web-viewer.md](web-viewer.md) to prepare the engine and supply a model with matching camera coordinates. Publish only these eight files from `viewer/`:

```text
index.html
viewer.css
viewer.mjs
scene.json
model.ply
playcanvas-2.22.1.mjs
PLAYCANVAS_LICENSE.txt
robots.txt
```

Do not publish the repository root, source videos, reconstruction runs, original frame directories, environment files or device configuration. The allowlist is a delivery boundary, not authentication. Everything in this public directory is intentionally public. `noindex,nofollow` is a search-engine hint, not access control.

## Dedicated Caddy example

`deploy/Caddyfile.example` starts a separate loopback listener and serves only `/yangdong-3d/` plus the eight exact asset paths. It has no login, camera route or reverse proxy to a private application. It does not import or modify another service's Caddy configuration.

```sh
export PUBLIC_VIEWER_ROOT="$(pwd)/viewer"
caddy validate --config deploy/Caddyfile.example --adapter caddyfile
caddy run --config deploy/Caddyfile.example --adapter caddyfile
```

The Caddy example was not executed during this public-code export; its DSL is not checked by the Python/JavaScript CI. Run `caddy validate` locally before using it.

The example listens on `http://127.0.0.1:8080/yangdong-3d/`. Choose an unused local port if necessary. A Worker cannot connect to that loopback address: an operator must separately provide a dedicated HTTPS public asset origin. Use a static host or independently managed listener under your control; do not route the repository root or a private application through it. No production service is created by the commands in this repository.

Caddy's file server provides ETags and byte ranges. All public assets, including the model and engine, revalidate (`public, max-age=0, must-revalidate`). A matching ETag permits a 304 without retransmitting the model body; a changed model is fetched again. These public cache rules do not apply to private application data. The origin denies framing; the optional Worker replaces that header for its permitted portfolio origin.

## Optional Cloudflare Worker

The `worker/` example is deployed separately from the asset server. Copy its example configuration to a local, ignored configuration and review both environment bindings before any manual deployment:

```sh
cp worker/wrangler.example.jsonc worker/wrangler.local.jsonc
node --test worker/worker.test.mjs worker/r2-model.test.mjs
```

Use your own Cloudflare account and an explicitly chosen Worker name. The repository does not run a deployment automatically or include an account-specific configuration. Use the Cloudflare dashboard or your separately installed Wrangler CLI only after reviewing the configuration.

| Binding | Required value |
| --- | --- |
| `ASSET_BASE_URL` | Canonical HTTPS URL for one public asset directory, ending in `/`, such as `https://assets.example.com/yangdong-3d/`. No credentials, query, fragment, dot segments or encoded path. Directory segments use letters, digits, `_` or `-`. |
| `ALLOWED_FRAME_ORIGIN` | One HTTPS origin without a trailing slash, path, credentials, query or fragment, such as `https://portfolio.example.com`. The published demo uses `https://bluestylo.github.io`. |

Both bindings are required. Missing/invalid configuration returns a generic 503 and never fetches an asset. IP literals, localhost and `.local` hosts are rejected. Configure only a trusted public origin; the settings are operator-controlled, not a general-purpose URL input. The Worker never derives an upstream address from a request query or header.

The Worker exposes `/yangdong-3d/` and the same eight assets. `/` redirects to `/yangdong-3d/`, retaining only a valid `view=0..5`. Only GET and HEAD are accepted. Proxy requests forward only `Range`, `If-None-Match`, `If-Modified-Since` and `If-Range`; cookies, authorization, client identity and arbitrary query strings are discarded. Upstream redirects and unexpected error pages become generic 502 responses. Without an R2 binding, a model response uses the original stream without buffering the model in Worker memory. The origin's ETag/cache headers, HEAD, 206 single/multipart ranges and 304 are preserved.

## Optional R2 model storage

R2 removes the model download's dependence on the source host's upload bandwidth. The Worker still serves the **same URL and iframe**. Only `model.ply` moves to R2; the other seven assets retain the configured HTTPS origin. Those assets still need the origin host online. The example configuration leaves R2 commented out, so copying it does not activate this feature.

After activating R2 in your account, create a **private Standard bucket**, upload the verified compressed model under a versioned key, and add these settings to the ignored `worker/wrangler.local.jsonc`:

| Optional setting | Value |
| --- | --- |
| `r2_buckets` entry | `{"binding":"MODEL_BUCKET","bucket_name":"your-private-model-bucket"}` |
| `vars.MODEL_KEY` | Required with `MODEL_BUCKET`, for example `models/your-model-sha256.ply`. ASCII letters, digits, `_`, `-`, `.`, and directory separators are supported; dot-segment paths are rejected. |

Keep both existing bindings. The object key comes only from the trusted deployment configuration. Neither public bucket access nor an `r2.dev` endpoint is needed: the Worker reads via its binding and exposes only the configured model. That model remains intentionally public through the Worker.

An absent `MODEL_BUCKET` retains the original proxy. A present but invalid binding/key returns 503. A missing object, storage failure, or object replacement between metadata and body reads returns a generic 502. None of those R2 failures falls back to the slow origin or exposes bucket diagnostics.

### HTTP behavior and read operations

- GET first calls R2 `head`, then streams `get` with an ETag precondition: **two Class B reads**. The body is never loaded in full into Worker memory.
- HEAD, matching conditional 304, and an unsatisfiable single-range 416 need **one metadata read** and no body read. HEAD ignores Range and reports the full length.
- Responses use R2's quoted ETag, Last-Modified, and `public, max-age=0, must-revalidate`. `If-None-Match` accepts weak/list/wildcard validators and takes precedence over `If-Modified-Since`.
- A single explicit, open-ended, or suffix byte range returns 206. Multiple ranges, malformed range syntax, and unsupported units are ignored with a full 200 response; this R2 path does not implement multipart responses. `If-Range` accepts the current strong ETag or exact Last-Modified date.
- Switching from the origin's ETag to R2's ETag can cause one fresh download even when the model bytes are identical. Later visits can revalidate the R2 copy without retransmitting it.

This feature stores the model in R2; it does not populate the Workers Cache API. API semantics follow the [R2 Workers API reference](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/).

### Plans and usage

Workers Paid is not required: the Worker can use the [Workers Free plan within its limits](https://developers.cloudflare.com/workers/platform/pricing/). R2 activation is separate. As checked on 2026-09-21, **Standard** includes 10 GB-month of storage, 1 million Class A operations and 10 million Class B operations per month, with no Internet egress fee. Account usage above those allowances is billable; a free allowance is not a spending cap. The approximately 104 MB model fits the storage allowance, but total account usage determines charges. The allowance does not cover Infrequent Access. See [R2 pricing](https://developers.cloudflare.com/r2/pricing/) before activation or changing storage class.

## Embed the current demo

The published viewer's server headers have been verified to permit framing from **HTTPS `bluestylo.github.io`**, including pages below that origin. They do not allow localhost or another portfolio origin. This confirms the viewer-side setting; it does not mean an embedding change has been published or tested on a portfolio site.

```html
<iframe
  src="https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/yangdong-3d/"
  title="양동면 3D 모델"
  loading="lazy"
  allow="fullscreen"
  allowfullscreen
  referrerpolicy="no-referrer"
  style="width:100%;aspect-ratio:16/9;border:0;min-height:360px"
></iframe>
```

Prefer a poster with an explicit “Explore in 3D” button that inserts the iframe after a click: opening the viewer can download about **104 MB**, plus the engine. `loading="lazy"` is an optional deferral hint and is not a substitute for click-to-load. Keep a fallback link to the [standalone viewer](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/). A GitHub README should use the GIF/poster and a link.

If the parent page has its own restrictive CSP, its `frame-src` must include `https://yangdong-3d.yangdong-3d-public-proxy.workers.dev`. Browser caches are separated by origin, so a first visit through a new address can require a new download even when a previous address was cached. `allowfullscreen` permits fullscreen; the iframe still uses the existing viewer controls.

## Release checks

Run the local Worker tests, then verify the intended public host with HEAD, Range and conditional requests; confirm that private/unknown paths return an error. Compare the downloaded model's length and SHA-256 with your release record. Open the viewer in a browser, switch a neighborhood view and return to the front view, and inspect the console. The existing origin-backed demo passed those checks, including the 103,505,301-byte model SHA-256 recorded in `web-viewer.md`.

The prepared R2 tests use a small fake bucket and check protocol semantics, no-body reads, stream identity, error handling, and the absence of origin fallback. They do not prove account activation, successful upload, live R2 service or download speed. After an actual R2 deployment, repeat the public checks, verify the unchanged model hash, measure a fresh download and confirm a cached 304. A new operator deployment needs its own checks.
