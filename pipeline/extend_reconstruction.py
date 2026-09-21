#!/usr/bin/env python3
"""Extend a copied COLMAP run with one new clip; preserve the source run.

Default stops after SfM for per-clip and geometry inspection. --train starts a
new combined Gaussian training, never resumes the old PLY optimizer state.
"""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import signal
import sqlite3
import time

from . import reconstruct as base

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def disjoint_runs(source, output):
    source, output = source.resolve(), output.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Source and extension runs must be separate, non-nested directories.')


def inspect_source(source):
    """Read a completed, quiescent source; do not create a lock in that source."""
    old = json.loads((source/'run_config.json').read_text())
    metrics = json.loads((source/'metrics.json').read_text())
    base.validate_settings(old['sfm'])
    base.guard_existing(metrics, source)
    if metrics.get('status') not in ('sfm_complete', 'complete'):
        raise ValueError('Source SfM must be complete and not running.')
    frames = base.visible_files(source/'frames', '*.jpg')
    if not frames or len(frames) != metrics.get('extracted_frames'):
        raise ValueError('Source frame count differs from the completed run.')
    model = Path(metrics['selected_model']).resolve()
    if not model.is_relative_to((source/'sparse').resolve()):
        raise ValueError('Source model must belong to the source run sparse directory.')
    for name in ('cameras.bin', 'images.bin', 'points3D.bin'):
        if not (model/name).is_file():
            raise ValueError('Source sparse model is incomplete.')
    names = base.registered_names(model/'images.bin')
    if len(names) != metrics.get('registered_frames') or not names or not set(names).issubset({p.name for p in frames}):
        raise ValueError('Source sparse registration does not match its frames/metrics.')
    database = source/'database.db'
    if not database.is_file() or any((source/('database.db'+suffix)).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('Source database must be closed and checkpointed, without WAL/SHM sidecars.')
    return old, metrics, frames, model


def prepare(config_path, required_mount=None, min_free_gib=8):
    config_path = config_path.expanduser().resolve()
    request = json.loads(config_path.read_text())
    def local_path(key):
        value = Path(request[key]).expanduser()
        return value if value.is_absolute() else config_path.parent/value
    source, candidate = local_path('source_run').resolve(), local_path('output_run')
    disjoint_runs(source, candidate)
    run = candidate.resolve()
    fingerprint = base.digest(request)
    if (run/'run_config.json').is_file():
        saved = json.loads((run/'run_config.json').read_text())
        if saved.get('kind') != 'extension' or saved.get('extension_config_sha256') != fingerprint:
            raise ValueError('Extension configuration changed; use a new output run.')
        if required_mount and str(Path(required_mount).resolve()) != saved.get('required_mount'):
            raise ValueError('Cannot change the saved required mount on resume.')
        base.storage_check(run, min_free_gib, saved.get('required_mount'))
        return run, saved
    base.ensure_new_run(candidate)
    old, metrics, frames, model = inspect_source(source)
    segments, probes = base.read_manifest(config_path, base.tool('ffprobe'))
    if len(segments) != 1:
        raise ValueError('Each extension adds exactly one clip; create another extension for more clips.')
    new = segments[0]
    new['prefix'] = f"s{len(old['segments'])+1:03d}_" + new['prefix'].split('_', 1)[1]
    if any(p.name.startswith(new['prefix']) for p in frames):
        raise ValueError('New frame prefix collides with the source run.')
    settings = {**old['sfm'], 'min_registration': request.get('min_registration', old['sfm']['min_registration'])}
    base.validate_settings(settings)
    matching = {'source_stride': 3, 'new_clip_prefix_frames': 65, 'new_stride': 2}
    extra = request.get('cross_match', {})
    if not isinstance(extra, dict) or set(extra)-set(matching):
        raise ValueError('Unknown cross_match setting.')
    matching.update(extra)
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in matching.values()):
        raise ValueError('Cross-match sampling settings must be positive integers.')
    mount = str(Path(required_mount).expanduser().resolve()) if required_mount else None
    required = max(min_free_gib, (sum(p.stat().st_size for p in frames)+(source/'database.db').stat().st_size)/base.GIB+2)
    base.storage_check(run, required, mount)
    with base.initialize_run(run):
        for name in ('frames', 'logs', 'sparse', 'seed', 'dataset', 'output'):
            (run/name).mkdir(parents=True, exist_ok=True)
        for path in frames:
            shutil.copyfile(path, run/'frames'/path.name)
        database_hash = sha(source/'database.db')
        shutil.copyfile(source/'database.db', run/'database.db')
        if sha(run/'database.db') != database_hash or sha(source/'database.db') != database_hash:
            raise RuntimeError('Source database changed during copying.')
        for name in ('cameras.bin', 'images.bin', 'points3D.bin'):
            before = sha(model/name)
            shutil.copyfile(model/name, run/'seed'/name)
            if sha(run/'seed'/name) != before or sha(model/name) != before:
                raise RuntimeError('Source sparse model changed during copying.')
        base.guard_existing(json.loads((source/'metrics.json').read_text()), source)
        config = dict(version=1, kind='extension', created_at=base.now(), source_run=str(source),
            source_database_sha256=database_hash, source_frames=len(frames),
            source_frame_names=[p.name for p in frames], source_registered_frames=len(base.registered_names(model/'images.bin')),
            segments=old['segments']+[new], source_probes=probes, sfm=settings, cross_match=matching,
            expected_frames=len(frames)+math.ceil((new['end']-new['start'])*settings['fps']),
            extension_config_sha256=fingerprint, required_mount=mount)
        base.write_json(run/'run_config.json', config)
    return run, config

def db_stats(run, config):
    with sqlite3.connect((run/'database.db').as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        names = dict(db.execute('SELECT image_id,name FROM images'))
        clip = {i: next(seg['prefix'] for seg in config['segments'] if n.startswith(seg['prefix'])) for i,n in names.items()}
        pairs = {}
        for pair_id, count in db.execute('SELECT pair_id,rows FROM two_view_geometries WHERE rows>0'):
            b = pair_id % 2147483647; a = (pair_id-b)//2147483647
            key = ' | '.join(sorted([clip[a],clip[b]]))
            item = pairs.setdefault(key, {'pairs':0,'inliers':0})
            item['pairs'] += 1; item['inliers'] += count
        return {'images':len(names),'verified_by_clips':pairs,
                'camera_groups':[dict(id=row[0], images=row[1]) for row in db.execute('SELECT camera_id,count(*) FROM images GROUP BY camera_id')]}

def sfm(runner, config):
    run = runner.run
    settings = config['sfm']; new = config['segments'][-1]
    new_path, prefix = Path(new['path']), new['prefix']
    matching = config['cross_match']
    if new_path.stat().st_size != new['bytes'] or new_path.stat().st_mtime_ns != new['mtime_ns']:
        raise RuntimeError('New input changed or is unavailable.')
    frames=run/'frames'; db=run/'database.db'; colmap=base.tool('colmap')
    if sorted(p.name for p in base.visible_files(frames, '*.jpg') if not p.name.startswith(prefix)) != sorted(config['source_frame_names']):
        raise RuntimeError('Copied source frame set changed; inspect the extension run.')
    def reset_new():
        for p in base.visible_files(frames, prefix+'*.jpg'): p.unlink()
    runner.stage(f"01_extract_{len(config['segments']):03d}", [base.tool('ffmpeg'), '-hide_banner','-loglevel','warning','-nostdin','-y',
        '-threads',settings['threads'],'-ss',new['start'],'-i',new_path,'-t',new['end']-new['start'],'-map','0:v:0','-an','-sn',
        '-vf',f'fps={settings["fps"]},scale={settings["resolution"]}:{settings["resolution"]}:force_original_aspect_ratio=decrease:force_divisible_by=2',
        '-q:v','2', frames/(prefix+'%06d.jpg')],
        valid=lambda: len(base.visible_files(frames,prefix+'*.jpg'))>=2 and
            len(base.visible_files(frames,prefix+'*.jpg')) == runner.metrics.get('frames_per_segment', {}).get(
                prefix, len(base.visible_files(frames,prefix+'*.jpg'))), prepare=reset_new)
    images=[p.name for p in base.visible_files(frames,'*.jpg')]
    added=[n for n in images if n.startswith(prefix)]
    counts={seg['prefix']:sum(n.startswith(seg['prefix']) for n in images) for seg in config['segments']}
    runner.metrics.update(extracted_frames=len(images), frames_per_segment=counts)
    runner.save()
    new_list=run/'new_images.txt';new_list.write_text('\n'.join(added)+'\n')
    (run/'image_list.txt').write_text('\n'.join(images)+'\n')
    runner.stage('02_features', [colmap,'feature_extractor','--database_path',db,'--image_path',frames,
        '--image_list_path',new_list,'--ImageReader.single_camera','1','--ImageReader.camera_model','SIMPLE_RADIAL',
        '--FeatureExtraction.use_gpu','0','--FeatureExtraction.num_threads',settings['threads'],
        '--FeatureExtraction.max_image_size',settings['resolution'],'--SiftExtraction.max_num_features',settings['features']])
    common=['--database_path',db,'--FeatureMatching.use_gpu','0','--FeatureMatching.num_threads',settings['threads']]
    runner.stage('03_sequential',[colmap,'sequential_matcher',*common,'--SequentialMatching.overlap',settings['overlap'],
        '--SequentialMatching.quadratic_overlap','1','--SequentialMatching.loop_detection','0'])
    anchors=set(images[::settings['global_stride']])
    for seg in config['segments']:
        seq=[n for n in images if n.startswith(seg['prefix'])];anchors.update([seq[0],seq[-1]])
    # Existing pairs are in the copied DB. Extra cross-clip pairs cover ascent.
    pairs={tuple(sorted((a,b))) for a,b in itertools.combinations(sorted(anchors),2) if a.startswith(prefix) or b.startswith(prefix)}
    house=[n for n in images if not n.startswith(prefix)]
    for a in house[::matching['source_stride']]:
        for b in added[:matching['new_clip_prefix_frames']:matching['new_stride']]:pairs.add(tuple(sorted((a,b))))
    pairfile=run/'global_pairs.txt';pairfile.write_text(''.join(f'{a} {b}\n' for a,b in sorted(pairs)))
    runner.metrics.update(global_pair_candidates=len(pairs));runner.save()
    runner.stage('04_global_pairs',[colmap,'matches_importer',*common,'--match_list_path',pairfile,'--match_type','pairs'])
    runner.metrics['database']=db_stats(run, config);runner.save()
    runner.stage('05_mapping',[colmap,'mapper','--database_path',db,'--image_path',frames,'--input_path',run/'seed',
        '--output_path',run/'sparse','--Mapper.num_threads',settings['threads'],'--Mapper.ba_use_gpu','0'],
        valid=lambda:bool(base.visible_files(run/'sparse','*/images.bin')) or (run/'sparse/images.bin').is_file(),
        prepare=lambda:base.clear_owned(run/'sparse'))
    models=[]
    candidates=base.visible_files(run/'sparse','*/images.bin')
    if (run/'sparse/images.bin').is_file():candidates.append(run/'sparse/images.bin')
    for p in candidates:
        names=base.registered_names(p)
        models.append(dict(path=str(p.parent), registered_frames=len(names), segments={prefix:sum(n.startswith(prefix) for n in names) for prefix in counts}))
    selected=max(models,key=lambda m:m['registered_frames'])
    ratios={p:selected['segments'][p]/n for p,n in counts.items()}
    runner.metrics.update(models=models,selected_model=selected['path'],registered_frames=selected['registered_frames'],
                          registration_ratio=selected['registered_frames']/len(images),registration_by_segment=ratios)
    runner.save()
    runner.stage('06_model_analysis',[colmap,'model_analyzer','--path',selected['path']])
    if min(ratios.values())<settings['min_registration']:
        raise RuntimeError('Inspect low per-clip registration before training: '+str(ratios))
    runner.stage('07_undistort',[colmap,'image_undistorter','--image_path',frames,'--input_path',selected['path'],
        '--output_path',run/'dataset','--output_type','COLMAP','--copy_policy','copy','--max_image_size',settings['resolution'],'--num_threads',settings['threads']],
        valid=lambda:(run/'dataset/sparse/images.bin').is_file() and len(base.visible_files(run/'dataset/images','*.jpg'))==selected['registered_frames'],
        prepare=lambda:base.clear_owned(run/'dataset'))
    runner.metrics.update(status='sfm_complete',sfm_finished_at=base.now());runner.save()
    print(json.dumps({'models':models,'registration_by_segment':ratios},ensure_ascii=False),flush=True)

def main():
    signal.signal(signal.SIGTERM, base.interrupted_by_signal)
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--config', type=Path, help='Extension JSON; paths resolve relative to this file.')
    inputs.add_argument('--run-dir', type=Path, help='Resume an already prepared extension.')
    parser.add_argument('--train', action='store_true', help='Train a new combined model after inspecting SfM.')
    parser.add_argument('--required-mount', type=Path)
    parser.add_argument('--min-free-gib', type=float, default=8)
    parser.add_argument('--brush', type=Path)
    for key, value in {**base.TRAIN_DEFAULTS, 'steps': 12000, 'max_splats': 2000000}.items():
        parser.add_argument('--'+key.replace('_', '-'), type=int, default=value)
    args = parser.parse_args()
    base.validate_settings({k: getattr(args, k) for k in base.TRAIN_DEFAULTS}, training=True)
    if args.config:
        run, config = prepare(args.config, args.required_mount, args.min_free_gib)
    else:
        run = args.run_dir.expanduser().resolve()
        config = json.loads((run/'run_config.json').read_text())
        if config.get('kind') != 'extension':
            parser.error('This is not an extension run.')
        if args.required_mount and str(args.required_mount.expanduser().resolve()) != config.get('required_mount'):
            parser.error('Cannot change the saved required mount on resume.')
        base.storage_check(run, args.min_free_gib, config.get('required_mount'))
    runner = base.Runner(run, config['sfm'], required_mount=config.get('required_mount'))
    start = time.monotonic()
    try:
        if args.train:
            if min(runner.metrics.get('registration_by_segment', {'missing': 0}).values()) < config['sfm']['min_registration']:
                raise RuntimeError('Inspect registration for every clip before training.')
            args.allow_low_registration = False
            base.run_train(runner, args)
        else:
            sfm(runner, config)
    except BaseException as error:
        runner.metrics.update(status='failed', error=str(error) or type(error).__name__)
        raise
    finally:
        runner.metrics.update(last_invocation_seconds=round(time.monotonic()-start, 2), updated_at=base.now())
        runner.close()


if __name__ == '__main__':
    main()
