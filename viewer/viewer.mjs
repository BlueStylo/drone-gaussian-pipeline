const stage = document.querySelector('#viewer-stage');
const canvas = document.querySelector('#canvas');
const status = document.querySelector('#status');
const loading = document.querySelector('#loading');
const progress = document.querySelector('#progress');
const retry = document.querySelector('#retry');
const selector = document.querySelector('#viewpoint');
const controls = [...document.querySelectorAll('.viewer-toolbar button, .viewer-toolbar select')];
const query = new URLSearchParams(location.search);

retry.addEventListener('click', () => location.reload());
let contextLost = false;
function showError(message) {
  stage.setAttribute('aria-busy', 'false');
  status.textContent = message;
  loading.hidden = false;
  progress.hidden = true;
  retry.hidden = false;
  controls.forEach(control => { control.disabled = true; });
}
canvas.addEventListener('webglcontextlost', event => {
  event.preventDefault();
  contextLost = true;
  showError('3D 화면이 잠시 멈췄어요. 다시 불러와 주세요.');
});

try {
  const response = await fetch('./scene.json');
  if (!response.ok) throw new Error(`Scene request failed: ${response.status}`);
  const seed = await response.json();
  if (!Array.isArray(seed.views) || !seed.views.length) throw new Error('No camera views');
  if (Number.isFinite(seed.ply_bytes) && seed.ply_bytes > 0) {
    status.textContent = `3D 불러오는 중 (${Math.ceil(seed.ply_bytes / 1000000)}MB)…`;
  }
  if (seed.ply !== './model.ply') throw new Error('scene.json must use ./model.ply');
  const modelCheck = await fetch(seed.ply, { method: 'HEAD' });
  if (modelCheck.status === 404 || modelCheck.status === 410) {
    const error = new Error('Model file is missing');
    error.userMessage = 'model.ply가 없습니다. viewer 폴더에 모델과 그 모델의 scene.json을 넣어 주세요. 준비 방법: docs/web-viewer.md';
    throw error;
  }
  if (!modelCheck.ok) throw new Error(`Model request failed: ${modelCheck.status}`);
  let pc;
  try { pc = await import('./playcanvas-2.22.1.mjs'); }
  catch (cause) {
    const error = new Error('PlayCanvas module is unavailable', { cause });
    error.userMessage = '3D 엔진을 준비해 주세요. 프로젝트 폴더에서 npm ci 후 npm run prepare:viewer를 실행해 주세요.';
    throw error;
  }
  selector.replaceChildren(...seed.views.map((view, index) => new Option(view.label || `보기 ${index + 1}`, String(index))));

  const app = new pc.Application(canvas, { graphicsDeviceOptions: { antialias: false, alpha: false } });
  app.graphicsDevice.maxPixelRatio = 1;
  app.autoRender = false;
  const requestFrame = () => { if (!contextLost) app.renderNextFrame = true; };
  app.scene.on('gsplat:sorted', requestFrame);
  app.setCanvasFillMode(pc.FILLMODE_NONE, stage.clientWidth, stage.clientHeight);
  app.setCanvasResolution(pc.RESOLUTION_AUTO);

  const camera = new pc.Entity('Inspection camera');
  camera.addComponent('camera', { nearClip: .01, farClip: 10000, clearColor: new pc.Color(.08, .095, .115) });
  app.root.addChild(camera);
  let active, yaw = 0, pitch = 0, distance;
  const target = new pc.Vec3(), right = new pc.Vec3(), up = new pc.Vec3(), back = new pc.Vec3();
  function fitFraming() {
    const aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight);
    const factor = Math.max(1, (active.width / active.height) / aspect) * 1.05;
    camera.camera.fov = Math.min(145, 2 * Math.atan(Math.tan(active.fov * Math.PI / 360) * factor) * 180 / Math.PI);
  }
  function updateCamera() {
    const offset = right.clone().mulScalar(Math.sin(yaw) * Math.cos(pitch))
      .add(up.clone().mulScalar(Math.sin(pitch)))
      .add(back.clone().mulScalar(Math.cos(yaw) * Math.cos(pitch))).mulScalar(distance);
    camera.setPosition(target.clone().add(offset));
    camera.lookAt(target, up);
    requestFrame();
  }
  function selectView(index) {
    active = seed.views[index] || seed.views[0];
    selector.value = String(seed.views.indexOf(active));
    yaw = pitch = 0;
    distance = active.distance;
    target.set(...active.target);
    right.set(...active.right);
    up.set(...active.up);
    back.set(...active.back);
    fitFraming();
    camera.camera.nearClip = Math.max(.001, distance / 10000);
    camera.camera.farClip = Math.max(1000, distance * 1000);
    updateCamera();
  }
  function resize() {
    if (stage.clientWidth < 1 || stage.clientHeight < 1) return;
    app.resizeCanvas(stage.clientWidth, stage.clientHeight);
    if (active) fitFraming();
    requestFrame();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(stage);
  window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) requestFrame(); });

  function pan(dx, dy) {
    const scale = 2 * distance * Math.tan(camera.camera.fov * Math.PI / 360) / Math.max(1, canvas.clientHeight);
    target.add(camera.right.clone().mulScalar(-dx * scale)).add(camera.up.clone().mulScalar(dy * scale));
  }
  function orbit(dx, dy) {
    yaw -= dx * .005;
    pitch = Math.max(-1.45, Math.min(1.45, pitch + dy * .005));
  }
  function zoom(factor) {
    distance = Math.max(active.distance * .015, Math.min(active.distance * 20, distance * factor));
  }
  selector.addEventListener('change', () => selectView(Number(selector.value)));
  document.querySelector('#reset').addEventListener('click', () => selectView(0));
  document.querySelector('#zoom-in').addEventListener('click', () => { zoom(.8); updateCamera(); });
  document.querySelector('#zoom-out').addEventListener('click', () => { zoom(1.25); updateCamera(); });

  // Pointer capture keeps gestures continuous at the canvas edge; resetting the
  // gesture after each finger enters or leaves avoids jumps between one and two.
  const pointers = new Map();
  let gesture = null;
  function touchPair() {
    if (pointers.size < 2) return null;
    const [a, b] = [...pointers.values()];
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, span: Math.hypot(b.x - a.x, b.y - a.y) };
  }
  canvas.addEventListener('pointerdown', event => {
    if (contextLost || (event.pointerType === 'mouse' && event.button !== 0 && event.button !== 2)) return;
    event.preventDefault();
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY, pan: event.pointerType === 'mouse' && (event.button === 2 || event.shiftKey) });
    canvas.setPointerCapture(event.pointerId);
    canvas.focus({ preventScroll: true });
    gesture = touchPair();
  });
  canvas.addEventListener('pointermove', event => {
    const previous = pointers.get(event.pointerId);
    if (!previous || contextLost) return;
    const dx = event.clientX - previous.x, dy = event.clientY - previous.y;
    pointers.set(event.pointerId, { ...previous, x: event.clientX, y: event.clientY });
    const next = touchPair();
    if (next && gesture) {
      pan(next.x - gesture.x, next.y - gesture.y);
      if (gesture.span > 4 && next.span > 4) zoom(gesture.span / next.span);
    } else if (!next) {
      if (previous.pan || (event.pointerType === 'mouse' && event.shiftKey)) pan(dx, dy);
      else orbit(dx, dy);
    }
    gesture = next;
    updateCamera();
  });
  function endPointer(event) {
    pointers.delete(event.pointerId);
    gesture = touchPair();
  }
  for (const eventName of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(eventName, endPointer);
  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (contextLost) return;
    const pixels = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1);
    zoom(Math.exp(Math.max(-1000, Math.min(1000, pixels)) * .001));
    updateCamera();
  }, { passive: false });
  canvas.addEventListener('keydown', event => {
    if (contextLost) return;
    const movement = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
    if (movement) {
      if (event.shiftKey) pan(...movement); else orbit(...movement);
    } else if (event.key === '+' || event.key === '=') zoom(.8);
    else if (event.key === '-' || event.key === '_') zoom(1.25);
    else if (event.key === 'Home') selectView(0);
    else return;
    event.preventDefault();
    updateCamera();
  });

  const initial = Number(query.get('view') ?? seed.initialView ?? 0);
  selectView(Number.isInteger(initial) && initial >= 0 && initial < seed.views.length ? initial : 0);
  app.start();
  requestFrame();
  const asset = new pc.Asset('Yangdong Gaussian splat', 'gsplat', { url: seed.ply });
  let lastPercent = -1;
  asset.on('progress', (received, total) => {
    if (contextLost) return;
    if (Number.isFinite(total) && total > 0) {
      const percent = Math.min(100, Math.floor(received / total * 100));
      if (percent !== lastPercent) {
        lastPercent = percent;
        progress.value = percent;
        status.textContent = percent === 100 ? '3D 화면을 준비하고 있어요…' : `3D 불러오는 중 · ${percent}%`;
      }
    }
  });
  app.assets.add(asset);
  await new Promise((resolve, reject) => {
    asset.once('load', resolve);
    asset.once('error', reject);
    app.assets.load(asset);
  });
  const splat = new pc.Entity('Yangdong');
  splat.addComponent('gsplat', { asset });
  app.root.addChild(splat);
  splat.gsplat.instance?.sorter?.on('updated', requestFrame);
  requestFrame();
  if (!contextLost) {
    loading.hidden = true;
    stage.setAttribute('aria-busy', 'false');
    status.textContent = '3D를 불러왔습니다.';
    controls.forEach(control => { control.disabled = false; });
  }
  window.viewer = { app, camera, splat, seed, selectView, reset: () => selectView(0), requestFrame };
} catch (error) {
  console.error('Yangdong 3D viewer:', error);
  if (!contextLost) showError(error.userMessage || '3D를 불러오지 못했어요. 연결을 확인하고 다시 시도해 주세요.');
}
