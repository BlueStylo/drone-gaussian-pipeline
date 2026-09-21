#!/usr/bin/env python3
"""Read-only COLMAP geometry comparison; write a JSON report and optional PNG.

Example: python check_expanded_geometry.py --old-model OLD/sparse/0 \
    --new-model NEW/sparse/0 --output NEW/review --plot

Coordinates are arbitrary SfM units, not metres. Jump flags are review hints;
the tool never removes cameras or changes either input model.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import struct

import numpy as np

def camera_center(quaternion, translation):
    norm = math.sqrt(sum(value * value for value in quaternion))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError('Degenerate camera quaternion')
    w, x, y, z = [value / norm for value in quaternion]
    rotation = [[1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w],
                [2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w],
                [2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y]]
    return [-sum(rotation[j][i] * translation[j] for j in range(3)) for i in range(3)]


PARAM_COUNTS = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12, 7: 5, 8: 4, 9: 5, 10: 12, 11: 16}
OBSERVATION = np.dtype([('x', '<f8'), ('y', '<f8'), ('point', '<i8')])


def read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise ValueError('Truncated COLMAP binary input')
    return data


def unpack(stream, fmt):
    return struct.unpack('<' + fmt, read_exact(stream, struct.calcsize('<' + fmt)))


def natural(name):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', name)]


def clip_name(name):
    return re.sub(r'\d+\.[^.]+$', '', name)


def frame_index(name):
    match = re.search(r'(\d+)\.[^.]+$', name)
    return int(match.group(1)) if match else None


def summary(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return {'count': 0, 'min': None, 'median': None, 'p95': None, 'max': None}
    return {'count': int(len(values)), 'min': float(values.min()),
            'median': float(np.median(values)), 'p95': float(np.percentile(values, 95)),
            'max': float(values.max())}


def load_model(folder, sample_limit):
    """Keep camera centres and bounded point samples, not millions of 2D tuples."""
    cameras, images, observed = {}, {}, defaultdict(set)
    with (folder / 'cameras.bin').open('rb') as stream:
        for _ in range(unpack(stream, 'Q')[0]):
            cid, model, width, height = unpack(stream, 'iiQQ')
            if model not in PARAM_COUNTS:
                raise ValueError(f'Unsupported camera model ID {model}')
            params = unpack(stream, 'd' * PARAM_COUNTS[model])
            cameras[cid] = {'model_id': model, 'width': width, 'height': height, 'parameters': list(params)}
    with (folder / 'images.bin').open('rb') as stream:
        for _ in range(unpack(stream, 'Q')[0]):
            iid, *pose = unpack(stream, 'idddddddi')
            name = bytearray()
            while True:
                char = read_exact(stream, 1)
                if char == b'\0':
                    break
                name.extend(char)
            name = name.decode('utf8')
            if name in images:
                raise ValueError(f'Duplicate registered image name: {name}')
            camera_id = pose[7]
            if camera_id not in cameras:
                raise ValueError(f'Image references missing camera {camera_id}')
            count = unpack(stream, 'Q')[0]
            observations = np.frombuffer(read_exact(stream, count * 24), dtype=OBSERVATION)
            points = observations['point']
            observed[clip_name(name)].update(map(int, points[points >= 0]))
            center = camera_center(pose[:4], pose[4:7])
            if not np.isfinite(center).all():
                raise ValueError(f'Nonfinite camera centre: {name}')
            images[name] = {'id': iid, 'camera_id': camera_id, 'center': center, 'clip': clip_name(name)}
    sampled, errors, nonfinite = [], [], 0
    with (folder / 'points3D.bin').open('rb') as stream:
        total_points = unpack(stream, 'Q')[0]
        stride = max(1, math.ceil(total_points / max(1, sample_limit)))
        for index in range(total_points):
            _pid, x, y, z, _r, _g, _b, error = unpack(stream, 'QdddBBBd')
            if all(math.isfinite(value) for value in (x, y, z)):
                if sample_limit and index % stride == 0:
                    sampled.append((x, y, z))
            else:
                nonfinite += 1
            if math.isfinite(error):
                errors.append(error)
            stream.seek(unpack(stream, 'Q')[0] * 8, 1)
        if stream.tell() > (folder / 'points3D.bin').stat().st_size:
            raise ValueError('Truncated point track data')
    clips = {}
    for clip in sorted({image['clip'] for image in images.values()}):
        members = [image for image in images.values() if image['clip'] == clip]
        clips[clip] = {'registered_images': len(members),
                      'camera_groups': dict(sorted(Counter(str(image['camera_id']) for image in members).items())),
                      'observed_unique_point_ids': len(observed[clip])}
    details = {'path': str(folder), 'registered_images': len(images), 'sparse_points': total_points,
               'nonfinite_sparse_points': nonfinite, 'point_reprojection_error_px': summary(errors),
               'clips': clips, 'cameras': {str(cid): value for cid, value in cameras.items()},
               'point_sample_count': len(sampled)}
    return images, np.asarray(sampled, dtype=float).reshape(-1, 3), details


def umeyama(source, target):
    """Fit target = scale * rotation @ source + translation; no reflection."""
    if len(source) < 3:
        raise ValueError('At least three shared camera centres are required')
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    x, y = source - source_mean, target - target_mean
    variance = float(np.sum(x * x) / len(x))
    if variance <= np.finfo(float).eps or np.linalg.matrix_rank(x) < 2 or np.linalg.matrix_rank(y) < 2:
        raise ValueError('Shared camera centres are coincident or collinear; similarity is underdetermined')
    u, singular, vt = np.linalg.svd(y.T @ x / len(x))
    signs = np.ones(3)
    if np.linalg.det(u @ vt) < 0:
        signs[-1] = -1
    rotation = u @ np.diag(signs) @ vt
    scale = float(np.dot(singular, signs) / variance)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Invalid fitted similarity scale')
    translation = target_mean - scale * (rotation @ source_mean)
    return scale, rotation, translation


def aligned(points, transform):
    scale, rotation, translation = transform
    return scale * (np.asarray(points, dtype=float).reshape(-1, 3) @ rotation.T) + translation


def inspect_steps(images, transform, prefix, extent):
    names = sorted((name for name in images if name.startswith(prefix)), key=natural)
    centers = aligned([images[name]['center'] for name in names], transform)
    steps = []
    for index in range(1, len(names)):
        before, after = frame_index(names[index - 1]), frame_index(names[index])
        gap = after - before if before is not None and after is not None and after > before else None
        length = float(np.linalg.norm(centers[index] - centers[index - 1]))
        steps.append({'from': names[index - 1], 'to': names[index], 'frame_gap': gap,
                      'length': length, 'length_per_frame': length / gap if gap else None,
                      'length_over_old_camera_extent': length / extent})
    rates = np.asarray([step['length_per_frame'] for step in steps if step['length_per_frame'] is not None])
    threshold, median, mad = None, None, None
    if len(rates):
        median = float(np.median(rates)); mad = float(np.median(np.abs(rates - median)))
        threshold = max(5 * median, median + 6 * 1.4826 * mad, extent * 1e-6)
    flags = []
    for step in steps:
        step['review_jump'] = bool(threshold is not None and step['length_per_frame'] is not None and step['length_per_frame'] > threshold)
        if step['review_jump']:
            flags.append(step)
    return {'prefix': prefix, 'registered_images': len(names),
            'step_lengths': summary([step['length'] for step in steps]),
            'step_lengths_per_extracted_frame': summary(rates),
            'threshold_per_extracted_frame': threshold, 'median_per_frame': median, 'mad_per_frame': mad,
            'threshold_rule': 'max(5*median, median+6*1.4826*MAD, old_camera_extent*1e-6)',
            'registration_gaps': [step for step in steps if step['frame_gap'] and step['frame_gap'] > 1],
            'jump_candidate_count': len(flags), 'jump_candidates': flags, 'steps': steps,
            'interpretation': 'Heuristic review candidates only. Frame gaps are normalized; velocity changes can still be valid. No cameras were removed.'}, names, centers


def make_plot(path, old_images, new_images, old_points, new_points, transform, report, new_prefix):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    old_names = sorted(old_images, key=natural)
    old_centers = np.asarray([old_images[name]['center'] for name in old_names])
    new_names = sorted(new_images, key=natural)
    new_centers = aligned([new_images[name]['center'] for name in new_names], transform)
    center = old_centers.mean(axis=0)
    _, _, vt = np.linalg.svd(old_centers - center, full_matrices=False)
    basis = vt[:2].T
    project = lambda points: (points - center) @ basis
    figure, axes = plt.subplots(1, 2, figsize=(15, 7), dpi=150, constrained_layout=True)
    colors = ['#0086a8', '#d6a02a', '#bf4b5a', '#744ec2']
    for axis in axes:
        if len(old_points):
            cloud = project(old_points); axis.scatter(cloud[:, 0], cloud[:, 1], s=.25, c='#a9b0b8', alpha=.18, rasterized=True)
        if len(new_points):
            cloud = project(aligned(new_points, transform)); axis.scatter(cloud[:, 0], cloud[:, 1], s=.25, c='#718dac', alpha=.16, rasterized=True)
        for number, clip in enumerate(sorted({image['clip'] for image in new_images.values()})):
            subset = [index for index, name in enumerate(new_names) if new_images[name]['clip'] == clip]
            points = project(new_centers[subset]); axis.plot(points[:, 0], points[:, 1], '.-', ms=2, lw=.8, color=colors[number % len(colors)], label=f'Expanded: {clip}')
        reference = project(old_centers); axis.scatter(reference[:, 0], reference[:, 1], s=5, facecolors='none', edgecolors='#192734', linewidths=.4, label='Original camera centres')
        for jump in report['new_clip_path']['jump_candidates']:
            segment = project(aligned([new_images[name]['center'] for name in (jump['from'], jump['to'])], transform))
            axis.plot(segment[:, 0], segment[:, 1], color='#ef2727', lw=2.2)
        axis.set_aspect('equal', adjustable='box'); axis.grid(alpha=.15)
        axis.set_xlabel('Old-camera PCA axis 1 (arbitrary units)'); axis.set_ylabel('Old-camera PCA axis 2 (arbitrary units)')
    axes[0].set_title('Aligned expansion and sampled sparse points')
    shared = report['alignment']['shared_images']
    for name in shared:
        segment = project(np.vstack([old_images[name]['center'], aligned([new_images[name]['center']], transform)[0]]))
        axes[1].plot(segment[:, 0], segment[:, 1], color='#272d33', alpha=.35, lw=.6)
    reference = project(old_centers)
    span = np.maximum(np.ptp(reference, axis=0), report['alignment']['old_camera_extent'] * .05)
    low, high = reference.min(axis=0) - .12 * span, reference.max(axis=0) + .12 * span
    axes[1].set_xlim(low[0], high[0]); axes[1].set_ylim(low[1], high[1])
    axes[1].set_title('Original house camera area and alignment residuals')
    axes[0].legend(loc='best', fontsize=7)
    residual = report['alignment']['residual_fraction_of_old_camera_extent']
    figure.suptitle(f"Camera alignment: median {residual['median']:.3%}, p95 {residual['p95']:.3%}, max {residual['max']:.3%} of old-camera extent | {report['new_clip_path']['jump_candidate_count']} path jump candidates", fontsize=10)
    figure.savefig(path); plt.close(figure)
    return {'path': str(path), 'projection': 'First two PCA axes of original camera centres; not geodetic and not an exact overhead view', 'pca_origin': center.tolist(), 'pca_basis_columns': basis.tolist()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-model', required=True, type=Path)
    parser.add_argument('--new-model', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--new-prefix', required=True, help='Frame prefix for the added capture segment')
    parser.add_argument('--plot', action='store_true')
    parser.add_argument('--point-sample', type=int, default=20000, help='Maximum deterministic point sample per model for the plot')
    args = parser.parse_args()
    if args.point_sample < 0:
        parser.error('--point-sample must not be negative')
    old_path, new_path, output = args.old_model.resolve(), args.new_model.resolve(), args.output.resolve()
    if any(output == model or model in output.parents for model in (old_path, new_path)):
        parser.error('Output must be outside both COLMAP model directories')
    old, old_points, old_details = load_model(old_path, args.point_sample if args.plot else 0)
    new, new_points, new_details = load_model(new_path, args.point_sample if args.plot else 0)
    shared = sorted(old.keys() & new.keys(), key=natural)
    old_centers = np.asarray([image['center'] for image in old.values()])
    extent = float(np.linalg.norm(np.ptp(old_centers, axis=0)))
    if not math.isfinite(extent) or extent <= np.finfo(float).eps:
        raise ValueError('Original camera extent is zero or nonfinite')
    target = np.asarray([old[name]['center'] for name in shared])
    source = np.asarray([new[name]['center'] for name in shared])
    transform = umeyama(source, target)
    residuals = np.linalg.norm(aligned(source, transform) - target, axis=1)
    path_report, _, _ = inspect_steps(new, transform, args.new_prefix, extent)
    scale, rotation, translation = transform
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'inputs_modified': False,
              'old_model': old_details, 'new_model': new_details,
              'alignment': {'method': 'Umeyama least-squares similarity using all shared camera centres; proper rotation, no reflection',
                'direction': 'new model to old model', 'scale': scale, 'rotation': rotation.tolist(), 'translation': translation.tolist(),
                'formula': 'old_xyz = scale * rotation @ new_xyz + translation',
                'shared_camera_count': len(shared), 'shared_images': shared,
                'missing_original_images': sorted(old.keys() - new.keys(), key=natural),
                'old_camera_extent': extent, 'extent_definition': '3D axis-aligned bounding-box diagonal of ALL original registered camera centres; not house dimensions or metres',
                'residuals_old_model_units': summary(residuals), 'residual_fraction_of_old_camera_extent': summary(residuals / extent),
                'worst_residuals': [{'image': shared[index], 'residual': float(residuals[index]), 'fraction_of_old_camera_extent': float(residuals[index] / extent)} for index in np.argsort(residuals)[::-1][:20]],
                'residuals_by_clip': {clip: summary([residuals[index] / extent for index, name in enumerate(shared) if old[name]['clip'] == clip]) for clip in sorted({old[name]['clip'] for name in shared})}},
              'new_clip_path': path_report,
              'limitations': ['Shared camera alignment does not independently prove the new neighborhood geometry.', 'A similarity fit absorbs a global rotation, translation and scale change.', 'Jump flags require visual inspection and are not automatic rejection decisions.', 'Per-clip observed point counts can overlap; they must not be summed as unique scene points.'],
              'plot': None}
    output.mkdir(parents=True, exist_ok=True)
    if args.plot:
        try:
            report['plot'] = make_plot(output / 'expanded_geometry.png', old, new, old_points, new_points, transform, report, args.new_prefix)
        except ImportError as error:
            report['plot'] = {'status': 'unavailable', 'reason': str(error)}
    destination = output / 'expanded_geometry.json'
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(destination)
    print(json.dumps({'report': str(destination), 'shared_cameras': len(shared),
                      'residual_fraction_of_old_camera_extent': report['alignment']['residual_fraction_of_old_camera_extent'],
                      'new_clip_jump_candidates': path_report['jump_candidate_count'], 'plot': report['plot']}, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
