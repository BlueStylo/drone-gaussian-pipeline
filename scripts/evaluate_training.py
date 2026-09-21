#!/usr/bin/env python3
"""Compare Brush eval render PNGs with matching local undistorted references.

Brush v0.3.0 exports only renders; ._ files on ExFAT are NOT references.
PSNR here is an external PNG-based reference metric, not Brush's internal
floating-point metric. References are resized with Pillow BILINEAR when needed.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

BRUSH_IMAGE_SOURCE = 'https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-dataset/src/scene.rs'
BRUSH_EXPORT_SOURCE = 'https://github.com/ArthurBrussee/brush/blob/v0.3.0/crates/brush-process/src/eval_export.rs'


def natural(value):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', value)]


def clip_name(name):
    return re.sub(r'\d+\.[^.]+$', '', name)


def references_for(eval_dir, supplied):
    if supplied:
        return supplied.resolve()
    for parent in eval_dir.parents:
        candidate = parent / 'dataset/images'
        if candidate.is_dir():
            return candidate
    raise ValueError('Cannot locate dataset/images; supply --reference-dir')


def load_pair(render_path, reference_path):
    with Image.open(render_path) as image:
        if image.mode != 'RGB':
            raise ValueError(f'Render is not opaque RGB: {render_path.name}: {image.mode}')
        render = image.copy()
    with Image.open(reference_path) as image:
        if image.mode != 'RGB':
            raise ValueError(f'Reference is not opaque RGB: {reference_path.name}: {image.mode}')
        reference = image.copy()
    original_size = reference.size
    resized = reference.size != render.size
    if resized:
        # Brush image::resize preserves aspect ratio and rounds positive sizes.
        maximum = max(render.size)
        ratio = min(1.0, maximum / reference.width, maximum / reference.height)
        expected = tuple(max(1, math.floor(size * ratio + .5)) for size in reference.size)
        if expected != render.size:
            raise ValueError(f'Unexpected camera/image size mismatch: {reference_path.name} {original_size} -> {render.size}, expected {expected}; refusing crop/stretch')
        reference = reference.resize(render.size, Image.Resampling.BILINEAR)
    return reference, render, original_size, resized


def statistics(records):
    finite = [item['psnr_db'] for item in records if item['psnr_db'] is not None]
    identical = sum(item['mse_rgb_255'] == 0 for item in records)
    pixels = sum(item['rgb_values'] for item in records)
    pooled_mse = sum(item['mse_rgb_255'] * item['rgb_values'] for item in records) / pixels
    return {'views': len(records), 'psnr_db_mean': float(np.mean(finite)) if finite else None,
            'psnr_db_median': float(np.median(finite)) if finite else None,
            'psnr_db_min': float(min(finite)) if finite else None,
            'psnr_db_max': float(max(finite)) if finite else None,
            'identical_views_with_infinite_psnr': identical,
            'psnr_mean_median_population': 'finite per-view PSNR values; exact matches, if any, separately counted',
            'pooled_mse_rgb_255': pooled_mse,
            'pooled_psnr_db': None if pooled_mse == 0 else float(10 * math.log10(255 ** 2 / pooled_mse))}


def representatives(records):
    groups = defaultdict(list)
    for item in records:
        groups[item['clip']].append(item)
    chosen = []
    # For the three current clips, take two temporal positions from each clip.
    for clip in sorted(groups):
        group = sorted(groups[clip], key=lambda item: natural(item['name']))
        for fraction in (.2, .8):
            item = group[round((len(group) - 1) * fraction)]
            if item not in chosen:
                chosen.append(item)
    if len(chosen) < min(6, len(records)):
        for item in records:
            if item not in chosen:
                chosen.append(item)
            if len(chosen) == min(6, len(records)):
                break
    return chosen[:6]


def font(size):
    for candidate in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def contactsheet(path, chosen, title):
    panel_width, panel_height, margin, gap, row_header = 460, 260, 24, 24, 55
    pair_width = panel_width * 2 + 8
    width = 2 * pair_width + 3 * margin
    rows = math.ceil(len(chosen) / 2)
    top = 96
    sheet = Image.new('RGB', (width, top + rows * (row_header + panel_height + gap) + 18), '#17202a')
    draw = ImageDraw.Draw(sheet)
    draw.text((margin, 17), f'{title} - source / rendered comparisons', font=font(27), fill='#ffffff')
    draw.text((margin, 55), 'Left: local undistorted reference (resized). Right: saved eval PNG. PSNR: external RGB reference metric.', font=font(19), fill='#bac5d0')
    for index, item in enumerate(chosen):
        col, row = index % 2, index // 2
        x, y = margin + col * (pair_width + margin), top + row * (row_header + panel_height + gap)
        reference, render, _, _ = load_pair(Path(item['render']), Path(item['reference']))
        score = f"{item['psnr_db']:.2f} dB" if item['psnr_db'] is not None else 'infinite PSNR'
        draw.text((x, y), f"{item['name']}   {score}", font=font(21), fill='#ffffff')
        draw.text((x, y + 29), 'REFERENCE', font=font(16), fill='#9eb4c9')
        draw.text((x + panel_width + 8, y + 29), 'RENDER', font=font(16), fill='#9eb4c9')
        for offset, image in ((0, reference), (panel_width + 8, render)):
            thumbnail = ImageOps.contain(image, (panel_width, panel_height), Image.Resampling.LANCZOS)
            sheet.paste(thumbnail, (x + offset, y + row_header))
    sheet.save(path, quality=94, subsampling=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--eval-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='Report output directory outside model/eval inputs')
    parser.add_argument('--reference-dir', type=Path, help='Defaults to the enclosing run dataset/images')
    parser.add_argument('--contactsheet', action='store_true')
    args = parser.parse_args()
    eval_dir, output = args.eval_dir.resolve(), args.output.resolve()
    references = references_for(eval_dir, args.reference_dir)
    if any(output == source or source in output.parents for source in (eval_dir, references)):
        parser.error('Output must be outside eval and reference image directories')
    renders = sorted((p for p in eval_dir.glob('*.png') if p.is_file() and not p.name.startswith('.')), key=lambda p: natural(p.name))
    if not renders:
        parser.error('No visible eval render PNGs found')
    source_map = {}
    for path in references.iterdir():
        if path.is_file() and not path.name.startswith('.') and path.suffix.lower() in ('.jpg', '.jpeg', '.png'):
            if path.stem in source_map:
                raise ValueError('Ambiguous source filename stem: ' + path.stem)
            source_map[path.stem] = path
    records = []
    for path in renders:
        reference_path = source_map.get(path.stem)
        if reference_path is None:
            raise ValueError('Missing reference: ' + path.name)
        reference, render, original_size, resized = load_pair(path, reference_path)
        delta = np.asarray(reference, dtype=np.float32) - np.asarray(render, dtype=np.float32)
        mse = float(np.mean(delta * delta, dtype=np.float64))
        records.append({'name': path.name, 'clip': clip_name(path.name), 'render': str(path), 'reference': str(reference_path),
                        'reference_original_size': list(original_size), 'comparison_size': list(render.size), 'reference_resized': resized,
                        'rgb_values': render.width * render.height * 3, 'mse_rgb_255': mse,
                        'psnr_db': None if mse == 0 else float(10 * math.log10(255 ** 2 / mse))})
    groups = defaultdict(list)
    for item in records:
        groups[item['clip']].append(item)
    chosen = representatives(records)
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'inputs_modified': False,
              'eval_dir': str(eval_dir), 'reference_dir': str(references),
              'ignored_hidden_files': len([p for p in eval_dir.iterdir() if p.name.startswith('.')]),
              'method': {'label': 'External approximate PNG PSNR; not Brush internal evaluation PSNR',
                'formula': '10*log10(255^2 / mean((reference_RGB - render_RGB)^2))',
                'units': 'decibels (dB); 3-channel encoded RGB byte values 0..255; no linear-light conversion, crop, mask, or registration adjustment',
                'resampling': 'Reference only: Pillow BILINEAR to the exact render dimensions after validating Brush aspect-ratio rounding. Brush uses image crate Triangle.',
                'caveat': 'Pillow vs Rust JPEG decoding/resampling rounding is not verified bit-identical; saved render PNGs also quantize the float training output. Compare checkpoints using this same evaluator, not against Brush internal metrics.',
                'brush_sources': [BRUSH_IMAGE_SOURCE, BRUSH_EXPORT_SOURCE]},
              'size_distribution': dict(Counter(f"{item['comparison_size'][0]}x{item['comparison_size'][1]}" for item in records)),
              'overall': statistics(records), 'per_clip': {clip: statistics(items) for clip, items in sorted(groups.items())},
              'representatives': [item['name'] for item in chosen], 'per_view': records}
    output.mkdir(parents=True, exist_ok=True)
    stem = eval_dir.name
    if args.contactsheet:
        contact_path = output / f'{stem}_comparison.jpg'
        contactsheet(contact_path, chosen, stem)
        report['contactsheet'] = str(contact_path)
    target = output / f'{stem}_metrics.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(target)
    print(json.dumps({'report': str(target), 'overall': report['overall'], 'per_clip': report['per_clip'],
                      'contactsheet': report.get('contactsheet'), 'metric_label': report['method']['label']}, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
