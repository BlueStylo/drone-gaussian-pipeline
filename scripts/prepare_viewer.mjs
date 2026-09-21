import { copyFile, lstat, mkdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PLAYCANVAS_VERSION = '2.22.1';
const defaultRoot = fileURLToPath(new URL('../', import.meta.url));

async function regularFile(path) {
  if (!(await lstat(path)).isFile()) throw new Error(`Expected a regular file: ${path}`);
}

// Local copies only: no registry call, remote model request, or build download.
export async function prepareViewer(projectRoot = defaultRoot) {
  const packageRoot = join(projectRoot, 'node_modules', 'playcanvas');
  let metadata;
  try { metadata = JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8')); }
  catch (cause) { throw new Error('Install the pinned dependencies with npm ci first.', { cause }); }
  if (metadata.version !== PLAYCANVAS_VERSION) {
    throw new Error(`Expected PlayCanvas ${PLAYCANVAS_VERSION}; found ${metadata.version}. Run npm ci.`);
  }
  const viewer = join(projectRoot, 'viewer');
  await mkdir(viewer, { recursive: true });
  if (!(await lstat(viewer)).isDirectory()) throw new Error('viewer must be a regular directory');
  const files = [
    [join(packageRoot, 'build', 'playcanvas.mjs'), join(viewer, `playcanvas-${PLAYCANVAS_VERSION}.mjs`)],
    [join(packageRoot, 'LICENSE'), join(viewer, 'PLAYCANVAS_LICENSE.txt')],
  ];
  // Check both sources and destinations before replacing either generated file.
  for (const [source, destination] of files) {
    await regularFile(source);
    try { await regularFile(destination); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  for (const [source, destination] of files) await copyFile(source, destination);
  return files.map(([, destination]) => destination);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const files = await prepareViewer();
    console.log(`Prepared ${files.length} PlayCanvas runtime files. Supply viewer/model.ply and its matching scene.json separately.`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
