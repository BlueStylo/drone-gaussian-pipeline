import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { prepareViewer } from '../scripts/prepare_viewer.mjs';

const viewerURL = new URL('../viewer/', import.meta.url);
const scene = JSON.parse(await readFile(new URL('scene.json', viewerURL), 'utf8'));

async function fixture(t, version = '2.22.1') {
  const root = await mkdtemp(join(tmpdir(), 'gaussian-viewer-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const pkg = join(root, 'node_modules', 'playcanvas');
  await mkdir(join(pkg, 'build'), { recursive: true });
  await writeFile(join(pkg, 'package.json'), JSON.stringify({ version }));
  await writeFile(join(pkg, 'build', 'playcanvas.mjs'), 'export const fixture = true;\n');
  await writeFile(join(pkg, 'LICENSE'), 'Fixture license text\n');
  return { root, pkg };
}

test('preparation copies the pinned local module and license only, without downloading a model', async t => {
  const { root } = await fixture(t);
  await prepareViewer(root);
  assert.deepEqual((await readdir(join(root, 'viewer'))).sort(), ['PLAYCANVAS_LICENSE.txt', 'playcanvas-2.22.1.mjs']);
  assert.equal(await readFile(join(root, 'viewer', 'playcanvas-2.22.1.mjs'), 'utf8'), 'export const fixture = true;\n');
  assert.equal(await readFile(join(root, 'viewer', 'PLAYCANVAS_LICENSE.txt'), 'utf8'), 'Fixture license text\n');
});

test('preparation rejects a different engine version and an absent installation', async t => {
  const { root } = await fixture(t, '2.22.2');
  await assert.rejects(prepareViewer(root), /Expected PlayCanvas 2\.22\.1/);
  await assert.rejects(prepareViewer(join(root, 'absent')), /npm ci/);
});

test('preparation does not copy a symlink or overwrite a symlink destination', async t => {
  const { root, pkg } = await fixture(t);
  const module = join(pkg, 'build', 'playcanvas.mjs');
  await rm(module);
  await symlink(join(pkg, 'LICENSE'), module);
  await assert.rejects(prepareViewer(root), /regular file/);
  await rm(module);
  await writeFile(module, 'export {};');
  await symlink(join(pkg, 'LICENSE'), join(root, 'viewer', 'PLAYCANVAS_LICENSE.txt'));
  await assert.rejects(prepareViewer(root), /regular file/);
  assert.equal(await readFile(join(pkg, 'LICENSE'), 'utf8'), 'Fixture license text\n');
});

test('the public scene contains only rendering fields and six coherent camera presets', () => {
  assert.deepEqual(Object.keys(scene).sort(), ['captureDate', 'initialView', 'ply', 'ply_bytes', 'title', 'views']);
  assert.equal(scene.ply, './model.ply');
  assert.equal(scene.initialView, 0);
  assert.equal(scene.views.length, 6);
  assert.deepEqual(scene.views.map(view => view.label), ['집 정면', '지붕과 텃밭', '집 바로 위', '동네 길과 주택', '논밭 쪽에서', '별장과 주변 도로']);
  const dot = (a, b) => a.reduce((sum, value, i) => sum + value * b[i], 0);
  for (const view of scene.views) {
    assert.deepEqual(Object.keys(view).sort(), ['back', 'distance', 'fov', 'height', 'label', 'position', 'right', 'target', 'up', 'width']);
    for (const key of ['position', 'target', 'right', 'up', 'back']) {
      assert.equal(view[key].length, 3);
      assert.ok(view[key].every(Number.isFinite));
    }
    for (const axis of ['right', 'up', 'back']) assert.ok(Math.abs(dot(view[axis], view[axis]) - 1) < 1e-6);
    assert.ok(Math.abs(dot(view.right, view.up)) < 1e-6);
    assert.ok(Math.abs(dot(view.right, view.back)) < 1e-6);
    assert.ok(Math.abs(dot(view.up, view.back)) < 1e-6);
    assert.ok(view.distance > 0 && view.fov > 0 && view.fov < 180 && view.width > 0 && view.height > 0);
    assert.ok(Math.hypot(...view.position.map((value, i) => value - view.target[i] - view.back[i] * view.distance)) < 1e-6);
  }
});

test('missing-model startup shows actionable help without initializing the engine or requesting model data', async t => {
  const original = Object.fromEntries(['document', 'location', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const log = console.error;
  const elements = new Map();
  const controls = [{ disabled: true }, { disabled: true }];
  const element = selector => {
    if (!elements.has(selector)) elements.set(selector, { hidden: false, textContent: '', attributes: {}, addEventListener() {}, setAttribute(name, value) { this.attributes[name] = value; } });
    return elements.get(selector);
  };
  t.after(() => {
    for (const [key, descriptor] of Object.entries(original)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
    console.error = log;
  });
  globalThis.document = { querySelector: element, querySelectorAll: () => controls };
  globalThis.location = { search: '', reload() {} };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push([url, options?.method || 'GET']);
    return url === './scene.json' ? Response.json(scene) : new Response(null, { status: 404 });
  };
  console.error = () => {};
  await import(new URL(`viewer.mjs?test-missing-model=${Date.now()}`, viewerURL));
  assert.deepEqual(calls, [['./scene.json', 'GET'], ['./model.ply', 'HEAD']]);
  assert.match(element('#status').textContent, /model\.ply.*scene\.json/);
  assert.equal(element('#retry').hidden, false);
  assert.equal(element('#progress').hidden, true);
  assert.equal(element('#viewer-stage').attributes['aria-busy'], 'false');
  assert.ok(controls.every(control => control.disabled));
});
