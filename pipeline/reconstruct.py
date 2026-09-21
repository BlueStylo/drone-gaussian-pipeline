#!/usr/bin/env python3
"""Public adaptation: multi-video -> COLMAP CPU -> Brush, with stage checkpoints.

First run: --manifest capture_manifest.json --stop-after sfm
Resume SfM: --run-dir runs/example --stop-after sfm
Train after inspecting SfM: --run-dir runs/example --mode train
Manifest: [{"path":"/absolute/video.MP4", "start":20, "end":330, "label":"orbit"}]
A {"segments": [...]} object is also accepted. Endpoints are seconds, end exclusive.
Successful preprocessing stages are reused. An interrupted training is restarted
in a new output folder; a PLY is not a full optimizer-state checkpoint.
"""
import argparse
from contextlib import contextmanager
import fcntl
import platform
import datetime as dt
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import time

DEFAULT_RUNS = Path('runs')
DEFAULTS = dict(fps=1.0, resolution=1600, features=6144, threads=6,
                overlap=12, global_stride=5, matcher='hybrid', min_registration=.85)
TRAIN_DEFAULTS = dict(steps=6000, train_resolution=1280, max_splats=1500000, sh_degree=3)
GIB = 1024**3


def now():
    return dt.datetime.now().isoformat(timespec='seconds')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def visible_files(directory, pattern):
    """Ignore ExFAT AppleDouble ._ sidecars and other hidden entries."""
    return sorted(p for p in directory.glob(pattern) if p.is_file()
                  and not any(part.startswith('.') for part in p.relative_to(directory).parts))


def process_identity(pid):
    try:
        result = subprocess.run(['/bin/ps', '-p', str(int(pid)), '-o', 'lstart=', '-o', 'command='],
                                capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (ValueError, TypeError):
        return ''


def guard_existing(metrics, run):
    """Check live owners and orphaned stages in addition to the run file lock."""
    owner = metrics.get('invocation_owner', {})
    if owner.get('host') == socket.gethostname() and owner.get('pid'):
        identity = process_identity(owner['pid'])
        if identity and identity == owner.get('identity'):
            raise RuntimeError(f'This run is still owned by live PID {owner["pid"]}; wait for it to exit: {run}')
    for stage in metrics.get('stages', {}).values():
        if stage.get('status') == 'complete' or not stage.get('pid'):
            continue
        identity = process_identity(stage['pid'])
        # Older checkpoints only stored a PID. Match the run path as well to
        # avoid refusing a reused PID belonging to an unrelated process.
        if identity and ((stage.get('process_identity') and identity == stage['process_identity'])
                         or (not stage.get('process_identity') and str(run) in identity)):
            raise RuntimeError(f'Previous stage still runs as PID {stage["pid"]}; do not resume concurrently: {run}')
        listing = subprocess.run(['/bin/ps', '-axo', 'pid=,pgid=,command='],
                                 capture_output=True, text=True, check=False).stdout
        for row in listing.splitlines():
            fields = row.strip().split(None, 2)
            if len(fields) == 3 and fields[1] == str(stage['pid']) and str(run) in fields[2]:
                raise RuntimeError(f'Previous stage child PID {fields[0]} is still in process group {stage["pid"]}; stop it before resuming.')


def terminate_group(process):
    if process is None:
        return
    group = process.pid
    def group_alive():
        process.poll()  # Reap the time wrapper if it exited before its children.
        try:
            os.killpg(group, 0)
            return True
        except ProcessLookupError:
            return False
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 10
    while group_alive() and time.monotonic() < deadline:
        time.sleep(.1)
    if group_alive():
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def interrupted_by_signal(signum, _frame):
    raise InterruptedError(f'Interrupted by signal {signum}; stopping active subprocess group.')


def storage_check(path, required_gib=2, required_mount=None):
    """Check before creating output; an optional mount guard survives resumes."""
    if not math.isfinite(required_gib) or required_gib < 0:
        raise ValueError('Required free space must be finite and nonnegative.')
    resolved = Path(path).expanduser().resolve()
    if required_mount is not None:
        mount = Path(required_mount).expanduser().resolve()
        if not mount.is_mount() or not resolved.is_relative_to(mount):
            raise RuntimeError('Required output volume is not mounted or output is outside it.')
    probe = resolved
    while not probe.exists():
        probe = probe.parent
    if not probe.is_dir():
        raise ValueError('Output or its existing parent is not a directory.')
    free = shutil.disk_usage(probe).free
    if free < required_gib * GIB:
        raise RuntimeError(f'Free space {free/GIB:.1f} GiB; need at least {required_gib:.1f} GiB.')
    return free


def timed_command(command):
    """Resource logging when available; Linux is not end-to-end validated."""
    timer = Path('/usr/bin/time')
    if timer.is_file() and platform.system() in ('Darwin', 'Linux'):
        return [str(timer), '-l' if platform.system() == 'Darwin' else '-v', *command]
    return list(command)


def validate_settings(settings, training=False):
    defaults = TRAIN_DEFAULTS if training else DEFAULTS
    for key in defaults:
        value = settings[key]
        if key == 'matcher':
            if value not in ('hybrid', 'exhaustive'):
                raise ValueError('Unknown matcher.')
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{key} must be finite and positive.')
        elif key not in ('fps', 'min_registration') and not isinstance(value, int):
            raise ValueError(f'{key} must be an integer.')
    if not training and settings['min_registration'] > 1:
        raise ValueError('min_registration must be at most 1.')
    if training and settings['sh_degree'] > 3:
        raise ValueError('sh_degree must be 1, 2, or 3.')
    return settings


def ensure_new_run(run):
    """A new run must not overwrite existing work or follow a leaf symlink."""
    if run.is_symlink() or (run.exists() and (not run.is_dir() or any(run.iterdir()))):
        raise ValueError(f'New run directory must be empty and not a symlink: {run}')


@contextmanager
def initialize_run(run):
    """Reserve initialization too, before writing a config or copying a seed."""
    run.mkdir(parents=True, exist_ok=True)
    with (run/'.pipeline.lock').open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another process is initializing this run.') from None
        if any(p.name != '.pipeline.lock' and not p.name.startswith('._') for p in run.iterdir()):
            raise ValueError('Run became nonempty during initialization; refusing to overwrite it.')
        yield


def validate_resume(config, overrides=None):
    if config.get('kind', 'base') != 'base':
        raise ValueError('Use pipeline.extend_reconstruction to resume an extension run.')
    validate_settings(config['sfm'])
    for key, value in (overrides or {}).items():
        if value is not None and value != config['sfm'][key]:
            raise ValueError(f'Cannot change {key} in an existing run; create a new run.')


def tool(name):
    result = shutil.which(name)
    if not result:
        raise RuntimeError(f'Required command is missing from PATH: {name}')
    return result


def read_manifest(path, ffprobe):
    raw = json.loads(path.read_text())
    entries = raw.get('segments') if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise ValueError('Manifest must contain a nonempty segments list.')
    probes, segments = {}, []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not entry['path']:
            raise ValueError('Each segment requires a nonempty path.')
        source = Path(entry['path']).expanduser()
        if not source.is_absolute():
            source = path.parent / source
        source = source.resolve()
        if not source.is_file():
            raise ValueError(f'Missing input: {source}')
        if str(source) not in probes:
            data = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_format',
                              '-show_streams', '-of', 'json', str(source)]))
            stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'
                           and not s.get('disposition', {}).get('attached_pic')), None)
            if stream is None:
                raise ValueError('Input has no usable video stream.')
            if stream.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
                raise ValueError(f'HDR/HLG/PQ input needs a separately validated SDR copy: {source}')
            duration = float(data.get('format', {}).get('duration', stream.get('duration', 'nan')))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError(f'Cannot determine video duration: {source}')
            stat = source.stat()
            # Deliberately exclude raw ffprobe tags (GPS, serials and other metadata).
            probes[str(source)] = dict(duration=duration, bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns, width=stream['width'], height=stream['height'],
                codec=stream.get('codec_name'), frame_rate=stream.get('avg_frame_rate'),
                video_duration=stream.get('duration'))
        info = probes[str(source)]
        start, end = float(entry.get('start', 0)), float(entry.get('end', info['duration']))
        if not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= info['duration'] + .1:
            raise ValueError(f'Invalid start/end for {source}: {start}, {end} (duration {info["duration"]})')
        end = min(end, info['duration'])
        label = str(entry.get('label', source.stem))
        slug = re.sub(r'[^A-Za-z0-9_-]+', '_', label).strip('_')[:32] or 'segment'
        segments.append(dict(path=str(source), start=start, end=end, label=label,
                             prefix=f's{i:03d}_{slug}_', bytes=info['bytes'], mtime_ns=info['mtime_ns']))
    sizes = {(v['width'], v['height']) for v in probes.values()}
    if len(sizes) != 1:
        raise ValueError('single_camera requires the same input dimensions across all clips.')
    return segments, probes


def registered_names(path):
    names = []
    with path.open('rb') as f:
        for _ in range(struct.unpack('<Q', f.read(8))[0]):
            f.read(64)
            name = bytearray()
            while True:
                char = f.read(1)
                if not char:
                    raise ValueError(f'Truncated COLMAP image model: {path}')
                if char == b'\0':
                    break
                name.extend(char)
            names.append(name.decode())
            f.seek(24 * struct.unpack('<Q', f.read(8))[0], 1)
    return names


class Runner:
    def __init__(self, run, settings, required_mount=None):
        self.run, self.settings = Path(run).resolve(), validate_settings(settings)
        run = self.run
        self.required_mount = required_mount
        storage_check(run, 0, required_mount)
        self.lock = (run / '.pipeline.lock').open('a+')
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Another process holds the run lock.') from None
        self.path = run / 'metrics.json'
        self.metrics = json.loads(self.path.read_text()) if self.path.exists() else {
            'created_at': now(), 'run': str(run), 'stages': {}, 'training_attempts': []}
        try:
            guard_existing(self.metrics, run)
        except BaseException:
            self.lock.close()
            raise
        self.env = os.environ.copy()
        self.env.update(OMP_NUM_THREADS=str(settings['threads']), OPENBLAS_NUM_THREADS=str(settings['threads']))
        self.metrics.update(status='running', invocation_started_at=now(), invocation_owner=dict(
            pid=os.getpid(), host=socket.gethostname(), identity=process_identity(os.getpid())))
        self.save()

    def save(self):
        storage_check(self.run, 0, self.required_mount)
        write_json(self.path, self.metrics)

    def close(self):
        if not self.lock.closed:
            try:
                self.metrics.pop('invocation_owner', None)
                self.save()
            finally:
                self.lock.close()

    def stage(self, name, command, valid=lambda: True, prepare=None):
        command = list(map(str, command))
        signature = digest(command)
        old = self.metrics['stages'].get(name, {})
        if old.get('status') == 'complete' and old.get('signature') == signature and valid():
            print(f'[{now()}] REUSE {name}', flush=True)
            return
        if old.get('status') == 'complete':
            raise RuntimeError(f'Completed {name} changed or lost outputs; use a new run to avoid stale downstream checkpoints.')
        storage_check(self.run, required_mount=self.required_mount)
        if prepare:
            prepare()
        attempt = int(old.get('attempt', 0)) + 1
        log = self.run / 'logs' / f'{name}_{attempt:02d}.log'
        record = dict(status='running', started_at=now(), command=command, signature=signature,
                      log=str(log), attempt=attempt)
        self.metrics['stages'][name] = record
        self.save()
        print(f'[{now()}] START {name}\n  log: {log}', flush=True)
        started = time.monotonic()
        process = None
        try:
            with log.open('w') as output:
                output.write('Command: ' + json.dumps(command, ensure_ascii=False) + '\n')
                output.flush()
                process = subprocess.Popen(timed_command(command), stdout=output,
                    stderr=subprocess.STDOUT, cwd=self.run, env=self.env, start_new_session=True)
                record['pid'] = process.pid
                record['process_identity'] = process_identity(process.pid)
                self.save()
                try:
                    while True:
                        try:
                            code = process.wait(timeout=30)
                            break
                        except subprocess.TimeoutExpired:
                            storage_check(self.run, 1, self.required_mount)
                            print(f'[{now()}] RUNNING {name} · {time.monotonic()-started:.0f}s', flush=True)
                except BaseException:
                    terminate_group(process)
                    raise
            record['exit_code'] = code
            if code != 0 or not valid():
                raise RuntimeError(f'{name} failed ({code}) or expected outputs missing. Read {log}')
            record['status'] = 'complete'
        except BaseException as error:
            terminate_group(process)
            record.update(status='failed', error=str(error) or type(error).__name__)
            raise
        finally:
            record.update(seconds=round(time.monotonic()-started, 2), finished_at=now())
            self.save()
        print(f'[{now()}] DONE {name} · {record["seconds"]:.1f}s', flush=True)


def clear_owned(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir()


def run_sfm(runner, config):
    run, s = runner.run, config['sfm']
    ffmpeg, colmap = tool('ffmpeg'), tool('colmap')
    frames, db = run / 'frames', run / 'database.db'
    counts = {}
    for index, segment in enumerate(config['segments'], 1):
        source = Path(segment['path'])
        if not source.is_file() or source.stat().st_size != segment['bytes'] or source.stat().st_mtime_ns != segment['mtime_ns']:
            raise RuntimeError(f'Original input changed or is unavailable: {source}')
        pattern = segment['prefix'] + '*.jpg'
        def cleanup(pattern=pattern):
            for item in visible_files(frames, pattern):
                item.unlink()
        command = [ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
            '-threads', s['threads'], '-ss', segment['start'], '-i', source,
            '-t', segment['end']-segment['start'], '-map', '0:v:0', '-an', '-sn',
            '-vf', f'fps={s["fps"]},scale={s["resolution"]}:{s["resolution"]}:force_original_aspect_ratio=decrease:force_divisible_by=2',
            '-q:v', '2', frames / (segment['prefix'] + '%06d.jpg')]
        runner.stage(f'01_extract_{index:03d}', command,
                     valid=lambda pattern=pattern, prefix=segment['prefix']: (
                         len(visible_files(frames, pattern)) >= 2 and
                         len(visible_files(frames, pattern)) == runner.metrics.get('frames_per_segment', {}).get(
                             prefix, len(visible_files(frames, pattern)))), prepare=cleanup)
        counts[segment['prefix']] = len(visible_files(frames, pattern))
        runner.metrics.setdefault('frames_per_segment', {})[segment['prefix']] = counts[segment['prefix']]
        runner.save()
    images = [p.name for p in visible_files(frames, '*.jpg')]
    if len(images) < 16:
        raise RuntimeError(f'Only {len(images)} frames; use at least 16.')
    runner.metrics.update(extracted_frames=len(images), frames_per_segment=counts)
    runner.save()
    image_list = run / 'image_list.txt'
    image_list.write_text(''.join(name + '\n' for name in images))
    runner.stage('02_features', [colmap, 'feature_extractor', '--database_path', db,
        '--image_path', frames, '--image_list_path', image_list,
        '--ImageReader.single_camera', '1', '--ImageReader.camera_model', 'SIMPLE_RADIAL',
        '--FeatureExtraction.use_gpu', '0', '--FeatureExtraction.num_threads', s['threads'],
        '--FeatureExtraction.max_image_size', s['resolution'], '--SiftExtraction.max_num_features', s['features']],
        valid=lambda: db.is_file() and db.stat().st_size > 0)
    common = ['--database_path', db, '--FeatureMatching.use_gpu', '0', '--FeatureMatching.num_threads', s['threads']]
    runner.stage('03_sequential', [colmap, 'sequential_matcher', *common,
        '--SequentialMatching.overlap', s['overlap'], '--SequentialMatching.quadratic_overlap', '1',
        '--SequentialMatching.loop_detection', '0'])
    if s['matcher'] == 'exhaustive':
        runner.stage('04_exhaustive', [colmap, 'exhaustive_matcher', *common])
        runner.metrics['global_pair_candidates'] = len(images)*(len(images)-1)//2
    else:
        # Every Nth image plus all segment endpoints; links repeated orbits and clips.
        anchors = set(images[::s['global_stride']])
        for segment in config['segments']:
            members = [n for n in images if n.startswith(segment['prefix'])]
            anchors.update((members[0], members[-1]))
        pairs = list(itertools.combinations(sorted(anchors), 2))
        pair_file = run / 'global_pairs.txt'
        pair_file.write_text(''.join(f'{a} {b}\n' for a, b in pairs))
        runner.metrics.update(global_anchor_count=len(anchors), global_pair_candidates=len(pairs))
        runner.save()
        runner.stage('04_global_pairs', [colmap, 'matches_importer', *common,
                     '--match_list_path', pair_file, '--match_type', 'pairs'])
    # Matching has exited and checkpointed the WAL. Immutable read avoids
    # creating SQLite shared-memory sidecars on the ExFAT volume.
    with sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        runner.metrics['database'] = dict(images=connection.execute('SELECT COUNT(*) FROM images').fetchone()[0],
            verified_pairs=connection.execute('SELECT COUNT(*) FROM two_view_geometries WHERE rows > 0').fetchone()[0])
    sparse = run / 'sparse'
    runner.stage('05_mapping', [colmap, 'mapper', '--database_path', db, '--image_path', frames,
        '--output_path', sparse, '--Mapper.num_threads', s['threads'], '--Mapper.ba_use_gpu', '0'],
        valid=lambda: bool(visible_files(sparse, '*/images.bin')), prepare=lambda: clear_owned(sparse))
    models = []
    for path in visible_files(sparse, '*/images.bin'):
        names = registered_names(path)
        models.append(dict(path=str(path.parent), registered_frames=len(names),
            segments={seg['prefix']:sum(n.startswith(seg['prefix']) for n in names) for seg in config['segments']}))
    selected = max(models, key=lambda m:m['registered_frames'])
    runner.metrics.update(models=models, selected_model=selected['path'],
        registered_frames=selected['registered_frames'], registration_ratio=selected['registered_frames']/len(images),
        registration_by_segment={prefix: selected['segments'][prefix]/count for prefix, count in counts.items()})
    runner.save()
    runner.stage('06_model_analysis', [colmap, 'model_analyzer', '--path', selected['path']])
    dataset = run / 'dataset'
    runner.stage('07_undistort', [colmap, 'image_undistorter', '--image_path', frames,
        '--input_path', selected['path'], '--output_path', dataset, '--output_type', 'COLMAP',
        '--copy_policy', 'copy', '--max_image_size', s['resolution'], '--num_threads', s['threads']],
        valid=lambda: (dataset/'sparse/images.bin').is_file() and len(visible_files(dataset/'images', '*.jpg')) == selected['registered_frames'],
        prepare=lambda: clear_owned(dataset))
    runner.metrics.update(status='sfm_complete', sfm_finished_at=now())
    runner.save()
    print(f'SfM ready: {selected["registered_frames"]}/{len(images)} images; {len(models)} model(s).\n'
          f'Inspect {run / "metrics.json"} and {dataset} before running --mode train.', flush=True)


def run_train(runner, args):
    if runner.metrics.get('stages', {}).get('07_undistort', {}).get('status') != 'complete':
        raise RuntimeError('SfM/undistortion has not completed; run --mode sfm first.')
    ratios = [runner.metrics.get('registration_ratio', 0),
              *runner.metrics.get('registration_by_segment', {}).values()]
    if min(ratios) < runner.settings['min_registration'] and not args.allow_low_registration:
        raise RuntimeError('Registration below threshold; inspect models first. Explicit --allow-low-registration overrides.')
    dataset = runner.run / 'dataset'
    if not (dataset/'sparse/images.bin').is_file() or not (dataset/'images').is_dir():
        raise RuntimeError('Completed dataset is missing.')
    brush = Path(args.brush or tool('brush_app')).expanduser().resolve()
    if not brush.is_file():
        raise RuntimeError(f'Brush executable missing: {brush}')
    settings = {key:getattr(args, key) if getattr(args, key) is not None else default for key, default in TRAIN_DEFAULTS.items()}
    validate_settings(settings, training=True)
    signature = digest(dict(settings=settings, brush=str(brush)))
    for attempt in runner.metrics.get('training_attempts', []):
        final = Path(attempt['output']) / f'export_{settings["steps"]}.ply'
        if attempt['signature'] == signature and runner.metrics['stages'].get(attempt['stage'], {}).get('status') == 'complete' and final.is_file():
            print(f'Reusing completed training: {final}', flush=True)
            runner.metrics.update(status='complete', final_ply=str(final))
            runner.save()
            return
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    output = runner.run / 'output' / ('train_' + stamp)
    storage_check(output, args.min_free_gib, runner.required_mount)
    output.mkdir()
    stage = '08_train_' + stamp
    runner.metrics.setdefault('training_attempts', []).append(dict(stage=stage, output=str(output),
        signature=signature, settings=settings, started_at=now(), optimizer_resume=False))
    runner.save()
    steps = settings['steps']
    runner.stage(stage, [brush, dataset, '--total-steps', steps, '--max-resolution', settings['train_resolution'],
        '--max-splats', settings['max_splats'], '--sh-degree', settings['sh_degree'], '--refine-every', '200',
        '--growth-stop-iter', max(1, int(steps*.75)), '--eval-split-every', '10', '--eval-every', '1000',
        '--eval-save-to-disk', '--export-every', min(2000, steps), '--export-path', output],
        valid=lambda: (output/f'export_{steps}.ply').is_file())
    runner.metrics.update(status='complete', final_ply=str(output/f'export_{steps}.ply'),
        exports=[dict(path=str(p), bytes=p.stat().st_size) for p in visible_files(output, '*.ply')])
    runner.save()


def main():
    signal.signal(signal.SIGTERM, interrupted_by_signal)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--run-dir', type=Path, help='Resume this run; settings/manifest are loaded from its run_config.json.')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_RUNS)
    parser.add_argument('--required-mount', type=Path, help='Optional external volume that must remain mounted; stored in the run config.')
    parser.add_argument('--name', default='drone')
    parser.add_argument('--mode', choices=['sfm', 'train', 'all'], default='sfm')
    parser.add_argument('--stop-after', choices=['sfm', 'train'], help='sfm stops before training; train runs the full pipeline.')
    for key in ('resolution','features','threads','overlap','global_stride','steps','train_resolution','max_splats','sh_degree'):
        parser.add_argument('--'+key.replace('_','-'), type=int)
    parser.add_argument('--fps', type=float)
    parser.add_argument('--min-registration', type=float)
    parser.add_argument('--matcher', choices=['hybrid','exhaustive'])
    parser.add_argument('--min-free-gib', type=float, default=8)
    parser.add_argument('--allow-low-registration', action='store_true')
    parser.add_argument('--brush', type=Path)
    args = parser.parse_args()
    for key in (*DEFAULTS, *TRAIN_DEFAULTS, 'min_free_gib'):
        value = getattr(args, key, None)
        if isinstance(value, (int, float)) and (not math.isfinite(value) or value <= 0):
            parser.error(f'{key} must be finite and positive.')
    if args.min_registration is not None and args.min_registration > 1:
        parser.error('--min-registration must be at most 1.')
    if args.sh_degree is not None and args.sh_degree > 3:
        parser.error('--sh-degree must be 1, 2, or 3.')
    mode = ('sfm' if args.stop_after == 'sfm' else 'all') if args.stop_after else args.mode
    if mode == 'train' and not args.run_dir:
        parser.error('--mode train requires --run-dir.')
    if Path(args.name).name != args.name or args.name in ('', '.', '..'):
        parser.error('--name must be a single folder name.')
    existing_config = args.run_dir and (args.run_dir.expanduser().resolve()/'run_config.json').is_file()
    if existing_config:
        run = args.run_dir.expanduser().resolve()
        config = json.loads((run/'run_config.json').read_text())
        try:
            validate_resume(config, {key: getattr(args, key) for key in DEFAULTS})
        except ValueError as error:
            parser.error(str(error))
        required_mount = config.get('required_mount')
        if args.required_mount and str(args.required_mount.expanduser().resolve()) != required_mount:
            parser.error('Cannot change the saved required mount on resume.')
        storage_check(run, args.min_free_gib, required_mount)
        if args.manifest:
            segments, _ = read_manifest(args.manifest.expanduser().resolve(), tool('ffprobe'))
            if segments != config['segments']:
                parser.error('Manifest differs from the existing run; create a new run.')
    else:
        if not args.manifest:
            parser.error('--manifest is required for a new run.')
        segments, probes = read_manifest(args.manifest.expanduser().resolve(), tool('ffprobe'))
        settings = {key:getattr(args,key) if getattr(args,key) is not None else default for key,default in DEFAULTS.items()}
        validate_settings(settings)
        estimated_frames = sum(math.ceil((v['end']-v['start'])*settings['fps']) for v in segments)
        required = max(args.min_free_gib, estimated_frames * .008)
        candidate = args.run_dir.expanduser() if args.run_dir else (
            args.output_root.expanduser().resolve() / (dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+args.name))
        ensure_new_run(candidate)
        run = candidate.resolve()
        required_mount = str(args.required_mount.expanduser().resolve()) if args.required_mount else None
        storage_check(run, required, required_mount)
        config = dict(version=1, kind='base', created_at=now(), manifest=str(args.manifest.resolve()), segments=segments,
                      sfm=settings, expected_frames=estimated_frames, source_probes=probes, required_mount=required_mount)
        with initialize_run(run):
            write_json(run/'run_config.json', config)
    for name in ('frames','logs','sparse','dataset','output'):
        (run/name).mkdir(exist_ok=True)
    runner = Runner(run, config['sfm'], required_mount=required_mount)
    print(f'Run: {run}', flush=True)
    started = time.monotonic()
    try:
        if mode != 'train':
            run_sfm(runner, config)
        if mode != 'sfm':
            run_train(runner, args)
    except BaseException as error:
        runner.metrics.update(status='failed', error=str(error) or type(error).__name__)
        raise
    finally:
        runner.metrics.update(last_invocation_seconds=round(time.monotonic()-started,2), updated_at=now())
        runner.close()
        print(f'Result: {run}', flush=True)


if __name__ == '__main__':
    main()
