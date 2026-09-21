# Public deployment

The current [public 3D demo](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/) uses a Cloudflare Worker in front of a dedicated public asset directory. The model is streamed from a separate HTTPS origin on an existing NAS; the other seven assets retain the existing source host. Creating that address and enabling its portfolio iframe did not change the existing private camera service, its authentication or its routes. This repository contains a portable public-only extraction; it contains no source hostname, private service configuration, account ID, OAuth token or deployment credentials.

**NAS migration, 2026-09-21:** production now uses a separate model origin. The unchanged public Worker URL delivered all 103,505,301 bytes in 14.867 seconds with the expected SHA-256; a matching ETag returned a bodyless 304 in 1.415 seconds. These are one-Mac transfer measurements, excluding browser decode/GPU initialization. Browser checks rendered the house front, switched to a neighborhood view and reset correctly. Reload returned a network 304 for the model, with 731 encoded bytes transferred while reusing the 103.5 MB cached body; the console had no errors or warnings. See the [release measurements](../evidence/model-delivery.json).

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
node --test worker/worker.test.mjs
```

Use your own Cloudflare account and an explicitly chosen Worker name. The repository does not run a deployment automatically or include an account-specific configuration. Use the Cloudflare dashboard or your separately installed Wrangler CLI only after reviewing the configuration.

| Binding | Required value |
| --- | --- |
| `ASSET_BASE_URL` | Canonical HTTPS URL for one public asset directory, ending in `/`, such as `https://assets.example.com/yangdong-3d/`. No credentials, query, fragment, dot segments or encoded path. Directory segments use letters, digits, `_` or `-`. |
| `ALLOWED_FRAME_ORIGIN` | One HTTPS origin without a trailing slash, path, credentials, query or fragment, such as `https://portfolio.example.com`. The published demo uses `https://bluestylo.github.io`. |

Both bindings are required. Missing/invalid configuration returns a generic 503 and never fetches an asset. IP literals, localhost and `.local` hosts are rejected. Configure only a trusted public origin; the settings are operator-controlled, not a general-purpose URL input. The Worker never derives an upstream address from a request query or header.

The optional `MODEL_BASE_URL` setting uses the same HTTPS-directory rules as `ASSET_BASE_URL`. If absent, all eight assets retain the existing origin. If present, it selects a separate origin only for `model.ply`; an invalid value returns 503 for model requests while other viewer assets keep their existing route. The commented example does not enable this option.

The Worker exposes `/yangdong-3d/` and the same eight assets. `/` redirects to `/yangdong-3d/`, retaining only a valid `view=0..5`. Only GET and HEAD are accepted. Requests forward only `Range`, `If-None-Match`, `If-Modified-Since` and `If-Range`; cookies, authorization, client identity and arbitrary query strings are discarded. Upstream redirects and unexpected error pages become generic 502 responses. A model response uses the original stream without buffering the model in Worker memory. ETag/cache headers, HEAD, 206 single/multipart ranges and 304 are preserved.

## Optional separate model origin

An existing NAS or server can provide the model through an independent static service. Keep `ASSET_BASE_URL` pointing at the current viewer assets, and set `MODEL_BASE_URL` to a verified public HTTPS directory containing `model.ply`, for example `https://models.example.com/yangdong-3d/`. The other seven assets continue to use the original host. Changing `ASSET_BASE_URL` alone would move all eight assets, not just the model.

Use a separate service, document root and available listener for the model. Mount only the public model read-only, disable directory listing, and return 404 for unrelated paths. Do not expose an ownCloud directory, private camera application or repository root. Existing ownCloud, CCTV and application routes remain intact. Publishing a new HTTPS hostname may require adding a site block to a shared TLS proxy; validate and back up that configuration before reloading it. This release added such a block without restarting the existing HTTPS container. The complete-viewer Caddy example above is separate from the model-only example below.

[Caddyfile.model.example](../deploy/Caddyfile.model.example) and [compose.model.example.yaml](../deploy/compose.model.example.yaml) reproduce the isolated model service. Place the verified `model.ply` in `deploy/01_Model/`, choose an unused loopback port, then validate before starting it:

```sh
docker compose -f deploy/compose.model.example.yaml config --quiet
docker compose -f deploy/compose.model.example.yaml up -d --pull never
```

The example pins the Caddy image used during validation, mounts the model read-only, runs as UID/GID 1000, publishes only a loopback listener and uses no cloud storage. The image must already be available when using `--pull never`. Only GET/HEAD of `/yangdong-3d/model.ply` is served; other paths return 404 and other methods return 405. Set up your own public HTTPS proxy separately. The live deployment's Caddy configurations were validated and its local HTTP boundary was tested; CI does not provision Docker or public certificates.

The service must be reachable **from Cloudflare**, with public DNS, a valid HTTPS certificate and working external routing. Connecting an operator's laptop through WireGuard does not connect the Worker to that VPN. A private VPN or loopback address alone is insufficient. This Worker configuration requires an HTTPS hostname; an HTTP-only endpoint cannot be substituted without changing that policy.

The model server should provide GET/HEAD, a correct `Content-Length`, `application/octet-stream`, byte ranges, ETag and Last-Modified. Preserve `public, max-age=0, must-revalidate`: a retained browser copy can revalidate with 304, while a changed ETag triggers a fresh download. The Worker streams the selected origin's response and retains its validators and range headers. A selected model-origin failure returns an error instead of silently retrying the previous host. This option does not add a persistent CDN copy or the Workers Cache API.

The public viewer URL, iframe URL, relative `./model.ply` path and allowed portfolio origin stay the same. Browser requests remain on the Worker origin, so no new browser CORS permission is needed. A move can still cause one full download if the new server generates a different ETag. The original host must remain online for the other seven assets.

This uses an existing server and requires no additional object-storage subscription. Existing hardware, electricity, Internet service, upload bandwidth and any public-network service limits still apply. No R2 account activation or R2 binding is used by this option. Keep account-specific origin addresses in the ignored `worker/wrangler.local.jsonc` file rather than the public example.

Before switching, verify the NAS copy against the model length and SHA-256 in [web-viewer.md](web-viewer.md). Check HTTPS reachability from outside the VPN, HEAD, a bounded Range request and an ETag-based 304. After switching, repeat those checks through the unchanged Worker URL and open the viewer. Record actual transfer measurements separately from local unit tests; a host change alone does not establish a speed improvement.

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
