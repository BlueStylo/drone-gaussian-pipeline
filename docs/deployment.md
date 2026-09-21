> 공개 이력 재구성 진행 중: 이 문서는 완성될 실행 흐름을 설명하며, 현재 단계에서 아직 추가되지 않은 코드는 뒤의 PR에서 공개합니다.

# Public deployment

The current [public 3D demo](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/) uses a Cloudflare Worker in front of a dedicated public asset directory. The model is streamed from the existing source host. Creating that address and enabling its portfolio iframe did not change the existing private camera service, its authentication or its routes. This repository contains a portable public-only extraction; it contains no source hostname, private service configuration, account ID, OAuth token or deployment credentials.

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

The example listens on `http://127.0.0.1:8080/yangdong-3d/`. Choose an unused local port if necessary. A Worker cannot connect to that loopback address: an operator must separately provide a dedicated HTTPS public asset origin. Use a static host or independently managed listener under your control; do not route the repository root or a private application through it. No production service is created by the commands in this repository.

Caddy's file server provides ETags and byte ranges. All public assets, including the model and engine, revalidate (`public, max-age=0, must-revalidate`). A matching ETag permits a 304 without retransmitting the model body; a changed model is fetched again. These public cache rules do not apply to private application data. The origin denies framing; the optional Worker replaces that header for its permitted portfolio origin.

## Optional Cloudflare Worker

The `worker/` example is deployed separately from the asset server. Copy its example configuration to a local, ignored configuration and review both environment bindings before any manual deployment:

```sh
cp worker/wrangler.example.jsonc worker/wrangler.local.jsonc
node --test worker/worker.test.mjs
```

Use your own Cloudflare account and an explicitly chosen Worker name. The repository does not run a deployment automatically or include an account-specific configuration. Use the Cloudflare dashboard or your separately installed Wrangler CLI only after reviewing the configuration.

| Binding | Required value |
| --- | --- |
| `ASSET_BASE_URL` | Canonical HTTPS URL for one public asset directory, ending in `/`, such as `https://assets.example.com/yangdong-3d/`. No credentials, query, fragment, dot segments or encoded path. Directory segments use letters, digits, `_` or `-`. |
| `ALLOWED_FRAME_ORIGIN` | One HTTPS origin without a trailing slash, path, credentials, query or fragment, such as `https://portfolio.example.com`. The published demo uses `https://bluestylo.github.io`. |

Both bindings are required. Missing/invalid configuration returns a generic 503 and never fetches an asset. IP literals, localhost and `.local` hosts are rejected. Configure only a trusted public origin; the settings are operator-controlled, not a general-purpose URL input. The Worker never derives an upstream address from a request query or header.

The Worker exposes `/yangdong-3d/` and the same eight assets. `/` redirects to `/yangdong-3d/`, retaining only a valid `view=0..5`. Only GET and HEAD are accepted. Requests forward only `Range`, `If-None-Match`, `If-Modified-Since` and `If-Range`; cookies, authorization, client identity and arbitrary query strings are discarded. Upstream redirects and unexpected error pages become generic 502 responses. A model response uses the original stream without buffering the model in Worker memory. ETag/cache headers, HEAD, 206 single/multipart ranges and 304 are preserved.

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

Run the local Worker tests, then verify the intended public host with HEAD, Range and conditional requests; confirm that private/unknown paths return an error. Compare the downloaded model's length and SHA-256 with your release record. Open the viewer in a browser, switch a neighborhood view and return to the front view, and inspect the console. The current published demo passed those checks, including the 103,505,301-byte model SHA-256 recorded in `web-viewer.md`. A new operator deployment needs its own checks.
