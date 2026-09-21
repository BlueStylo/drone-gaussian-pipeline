# Web viewer

The public demo is [양동면 3D 모델](https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/). It opens at the house front and provides six photo-based viewpoints. The repository includes the same UI and its public camera coordinates, without the model or engine bundle.

## Run locally

From the repository root, using Node.js 22.22 or later:

```sh
npm ci
npm run prepare:viewer
python3 -m http.server 8080 --bind 127.0.0.1 --directory viewer
```

Open `http://127.0.0.1:8080/`. The preparation command copies only `node_modules/playcanvas/build/playcanvas.mjs` and its MIT license into the ignored runtime files `viewer/playcanvas-2.22.1.mjs` and `viewer/PLAYCANVAS_LICENSE.txt`. It requires the pinned PlayCanvas 2.22.1 installation and makes no network requests. It never downloads a model. Until a model is supplied, the page displays setup help.

Python's development server is for local viewing. Use the dedicated allowlist configuration in [deployment.md](deployment.md) for public delivery and byte-range/cache support.

## Supply a model

Place a PlayCanvas-compatible Gaussian PLY at `viewer/model.ply`, with a matching `viewer/scene.json`. A regular point-cloud PLY is not sufficient. Keep model files out of Git. For another reconstruction, replace the demo camera coordinates with poses from that reconstruction; a model and an unrelated scene will not frame correctly. Update the HTML title and capture date for your own capture.

The supplied scene has only these fields:

- `title`, `captureDate`, `ply` (always `./model.ply`), `ply_bytes`, `initialView` and `views`.
- Each view has `label`, `position`, `target`, `right`, `up`, `back`, `fov`, `width`, `height` and `distance`.

Coordinates use the reconstruction's coordinate system. The orthonormal `right`, `up` and `back` vectors, the target point and the distance define the starting camera. In particular, `position = target + back × distance`. `fov` is the vertical field of view in degrees; `width` and `height` identify the reference image aspect ratio. Resize framing preserves that composition on narrow screens. The demo scene contains no original image names, GPS metadata, private paths or account data.

### Optional: download the public demo model

This is an explicit **103,505,301-byte download**. It is not part of installation, tests, or engine preparation. Run it only if you want the existing public demo and have reviewed the repository's media licensing terms. The included `scene.json` matches this model.

```sh
curl --fail --show-error --proto '=https' --output viewer/model.ply \
  https://yangdong-3d.yangdong-3d-public-proxy.workers.dev/yangdong-3d/model.ply
python3 -c "import hashlib; from pathlib import Path; p=Path('viewer/model.ply'); expected='c2ce5cf728846c3a791229dd22f0b8635a2f8693842d8ecf6c3ccd3e07d0b428'; f=p.open('rb'); actual=hashlib.file_digest(f, 'sha256').hexdigest(); f.close(); assert p.stat().st_size == 103505301 and actual == expected; print('Model size and SHA-256 verified')"
```

The optional command contacts the public Worker only. The underlying source address and its host configuration are not part of this repository.

## Controls and rendering

Use the viewpoint menu to return to a photographed composition. Drag to orbit; wheel or +/− to zoom; right-drag or Shift-drag to pan. On touchscreens, one finger orbits, and two fingers pan/pinch. Arrow keys orbit, Shift+arrows pan, +/− zoom and Home resets. “처음 시점” always returns to the house front. `?view=0` through `?view=5` can choose an initial demo view.

The viewer renders on demand and requests a new frame after input, resizing or Gaussian sorting. Download progress comes from actual asset progress events. The initial byte count comes from the scene metadata. A WebGL context loss leaves a retry message instead of silently accepting more input.

## Verification

```sh
node --check viewer/viewer.mjs
node --test tests/viewer.test.mjs
```

These tests check public scene fields, pose geometry, dependency preparation, symlink handling and the missing-model startup path. They do not claim a fresh browser rendering check for a different model or device. A final browser check should cover the front view, another neighborhood view, touch/resize behavior and the console.
