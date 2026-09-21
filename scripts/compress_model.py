#!/usr/bin/env python3
"""Create a new compressed PLY, preserving vertex count and SH bands.

The original PLY is read only. Existing destination files are never replaced.
Only the already installed, pinned SplatTransform 3.4.2 is used; no downloads.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import uuid

PACKAGE = Path(__file__).resolve().parents[1] / 'node_modules/@playcanvas/splat-transform'
CLI = PACKAGE / 'bin/cli.mjs'
TYPES = {'char': 1, 'uchar': 1, 'short': 2, 'ushort': 2, 'int': 4, 'uint': 4,
         'float': 4, 'double': 8}


def inspect_ply(path):
    elements = []
    format_seen = False
    with path.open('rb') as file:
        if file.readline() != b'ply\n':
            raise ValueError(f'Not a PLY file: {path}')
        while True:
            line = file.readline()
            if not line or file.tell() > 1024 * 1024:
                raise ValueError('Missing or excessive PLY header')
            words = line.decode('ascii').strip().split()
            if not words or words[0] == 'comment':
                continue
            if words[0] == 'format':
                if format_seen or words[1:] != ['binary_little_endian', '1.0']:
                    raise ValueError('Only binary little-endian PLY is supported')
                format_seen = True
            if words[0] == 'element':
                elements.append({'name': words[1], 'count': int(words[2]), 'properties': []})
            elif words[0] == 'property':
                if not elements or len(words) != 3 or words[1] not in TYPES:
                    raise ValueError('Unsupported PLY property')
                elements[-1]['properties'].append((words[1], words[2]))
            elif words[0] == 'end_header':
                offset = file.tell()
                break
    by_name = {e['name']: e for e in elements}
    if not format_seen or len(by_name) != len(elements) or 'vertex' not in by_name or any(e['count'] < 0 for e in elements):
        raise ValueError('Invalid PLY format or element layout')
    count = by_name['vertex']['count']
    if count <= 0:
        raise ValueError('Empty PLY')
    compressed = 'chunk' in by_name
    sh_element = by_name.get('sh') if compressed else by_name['vertex']
    sh_properties = [name for kind, name in sh_element['properties'] if name.startswith('f_rest_')] if sh_element else []
    bands = {0: 0, 9: 1, 24: 2, 45: 3}.get(len(sh_properties))
    # Brush writes the standard PLY columns in lexical order (0, 1, 10, ...).
    expected_sh = [f'f_rest_{i}' for i in range(len(sh_properties))]
    if bands is None or set(sh_properties) != set(expected_sh):
        raise ValueError('Invalid SH property layout')
    if compressed:
        if by_name['chunk']['count'] != (count + 255) // 256:
            raise ValueError('Compressed chunk count mismatch')
        if by_name['vertex']['properties'] != [('uint', 'packed_' + name) for name in ('position', 'rotation', 'scale', 'color')]:
            raise ValueError('Unexpected compressed vertex layout')
        if sh_element and (sh_element['count'] != count or sh_properties != expected_sh or any(kind != 'uchar' for kind, _ in sh_element['properties'])):
            raise ValueError('Compressed SH layout mismatch')
    expected_bytes = offset + sum(e['count'] * sum(TYPES[kind] for kind, _ in e['properties']) for e in elements)
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise ValueError(f'Truncated or inconsistent PLY: expected {expected_bytes}, found {actual_bytes}')
    numeric_checks = {}
    if compressed:
        chunk = by_name['chunk']
        if not chunk['properties'] or any(kind != 'float' for kind, _ in chunk['properties']):
            raise ValueError('Compressed chunk fields must be float32')
        chunk_offset = offset
        for element in elements:
            if element is chunk:
                break
            chunk_offset += element['count'] * sum(TYPES[kind] for kind, _ in element['properties'])
        float_count = chunk['count'] * len(chunk['properties'])
        remaining = float_count * 4
        nonfinite = 0
        with path.open('rb') as file:
            file.seek(chunk_offset)
            while remaining:
                block = file.read(min(remaining, 1024 * 1024))
                if not block or len(block) % 4:
                    raise ValueError('Truncated compressed chunk float data')
                nonfinite += sum(not math.isfinite(value) for value, in struct.iter_unpack('<f', block))
                remaining -= len(block)
        if nonfinite:
            raise ValueError(f'Compressed chunk contains {nonfinite} nonfinite float values')
        numeric_checks = {'nonfinite_float_values': nonfinite, 'float_values_checked': float_count,
                          'float_validation_scope': 'compressed chunk element only'}
    return {'vertices': count, 'sh_bands': bands, 'sh_coefficients_per_vertex': len(sh_properties),
            'compressed': compressed, 'bytes': actual_bytes, 'body_size_valid': True,
            'elements': {e['name']: e['count'] for e in elements}, **numeric_checks}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def publish_without_replace(temporary, output):
    # ExFAT does not support renamex_np(RENAME_EXCL) to a new path. Exclusive
    # creation preserves the no-overwrite guarantee on filesystems without no-replace rename support.
    # Consumers should wait for successful exit and the verification sidecar.
    with temporary.open('rb') as reader, output.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 4 * 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Completed original final PLY, never modified')
    parser.add_argument('--output', type=Path, required=True, help='New .compressed.ply; never overwrites an existing file')
    parser.add_argument('--require-mount', type=Path, help='Optional mounted volume that must contain the output')
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    if not output.name.endswith('.compressed.ply'):
        parser.error('Output must end with .compressed.ply')
    def check_mount():
        if args.require_mount is not None:
            mount = args.require_mount.expanduser().resolve()
            if not mount.is_mount() or not output.is_relative_to(mount):
                raise OSError('The required output volume is not mounted or does not contain the output')
    check_mount()
    report_path = output.with_suffix('.verification.json')
    log_path = output.with_suffix('.conversion.log')
    if any(p.exists() for p in (output, report_path, log_path)) or source == output:
        parser.error('Destination, report, or log already exists; choose a new output filename')
    package = json.loads((PACKAGE / 'package.json').read_text())
    if package['version'] != '3.4.2':
        parser.error('Expected pinned @playcanvas/splat-transform 3.4.2')
    node = shutil.which('node')
    if not node:
        parser.error('Node.js is not available')
    version = subprocess.check_output([node, str(CLI), '--version'], text=True, stderr=subprocess.STDOUT).strip()
    source_info = inspect_ply(source)
    if source_info['compressed']:
        parser.error('Source must be the original uncompressed PLY')
    existing_parent = output.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if shutil.disk_usage(existing_parent).free < source_info['bytes'] + 128 * 1024 * 1024:
        parser.error('Insufficient output filesystem space for safe conversion')
    before = source.stat()
    source_sha = sha256(source)
    check_mount()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name('.' + output.stem + '-' + uuid.uuid4().hex + '.partial.compressed.ply')
    command = [node, str(CLI), '--no-tty', str(source), str(temporary)]
    started = time.monotonic()
    # Leave a clearly named partial file and log for diagnosis if conversion fails.
    with log_path.open('x') as log:
        log.write(json.dumps({'command': command, 'tool': version}, ensure_ascii=False) + '\n')
        log.flush()
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    compressed_info = inspect_ply(temporary)
    if not compressed_info['compressed'] or any(source_info[k] != compressed_info[k] for k in ('vertices', 'sh_bands', 'sh_coefficients_per_vertex')):
        raise ValueError('Vertex count or SH bands changed during compression')
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha256(source) != source_sha:
        raise ValueError('Source changed during conversion')
    report = {'tool': version, 'package': '@playcanvas/splat-transform@3.4.2', 'license': package['license'],
              'source': {'path': str(source), **source_info, 'sha256': source_sha},
              'output': {'path': str(output), **compressed_info, 'sha256': sha256(temporary)},
              'source_unchanged': True, 'quantized_lossy': True, 'downsampling_or_filters': False,
              'coordinate_transform': False, 'elapsed_seconds': time.monotonic() - started,
              'size_reduction_percent': 100 * (1 - compressed_info['bytes'] / source_info['bytes']),
              'visual_qa': 'Not performed by this script; compare browser views separately.'}
    check_mount()
    publish_without_replace(temporary, output)
    with report_path.open('x') as report_file:
        report_file.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Compression failed: {error}', file=sys.stderr)
        sys.exit(1)
