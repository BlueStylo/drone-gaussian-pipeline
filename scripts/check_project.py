#!/usr/bin/env python3
"""Offline checks: public file boundary, syntax, and available stage tests.

Uses tracked files in a checkout and ignores generated runtime assets in a
staging tree. Never contacts cameras, downloads a model, or runs training.
"""
import ast
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', 'node_modules', '.venv', '__pycache__', '.wrangler', 'runs', 'data'}
GENERATED = {'viewer/playcanvas-2.22.1.mjs', 'viewer/PLAYCANVAS_LICENSE.txt'}
FORBIDDEN_SUFFIXES = {'.ply', '.splat', '.sog', '.mp4', '.mov', '.db', '.log'}
PRIVATE_PATTERNS = [
    re.compile('/' + r'Users/[^/\s]+/'),
    re.compile(r'[\w-]+\.tail[0-9a-f]+\.ts\.net'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{24,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]


def project_files(root=ROOT):
    if (root / '.git').exists():
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
        return [root / name for name in names if name]
    return sorted(path for path in root.rglob('*')
                  if path.is_file() and not IGNORED.intersection(path.relative_to(root).parts)
                  and path.relative_to(root).as_posix() not in GENERATED)


def public_errors(path, root=ROOT):
    relative = path.relative_to(root).as_posix()
    errors = []
    if path.is_symlink():
        return [f'{relative}: symlink would hide an external dependency']
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith(('.env', '.dev.vars')):
        if not path.name.endswith('.example'):
            errors.append(f'{relative}: runtime/input data must stay outside Git')
    if path.stat().st_size > 10 * 1024 * 1024:
        errors.append(f'{relative}: exceeds the public-source file budget')
    try:
        text = path.read_text()
    except UnicodeError:
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.gif'}:
            errors.append(f'{relative}: unexpected binary')
        return errors
    if any(pattern.search(text) for pattern in PRIVATE_PATTERNS):
        errors.append(f'{relative}: contains a private path, host, or credential pattern')
    if path.suffix == '.md':
        for target in re.findall(r'\]\(([^\s)]+)', text):
            target = target.strip('<>').split('#', 1)[0]
            if not target or '://' in target or target.startswith(('mailto:', '#')):
                continue
            if not (path.parent / target).exists():
                errors.append(f'{relative}: missing relative link {target}')
    return errors


def main():
    files = project_files()
    errors = [error for path in files for error in public_errors(path)]
    if errors:
        raise SystemExit('\n'.join(errors))
    for path in files:
        if path.suffix == '.py':
            ast.parse(path.read_text(), filename=str(path))
        elif path.suffix in {'.js', '.mjs'}:
            subprocess.run(['node', '--check', str(path)], check=True, cwd=ROOT)
    if (ROOT / 'tests').exists():
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'], check=True, cwd=ROOT)
    node_tests = [str(path.relative_to(ROOT)) for path in files if path.name.endswith('.test.mjs')]
    if node_tests:
        subprocess.run(['node', '--test', *node_tests], check=True, cwd=ROOT)
    print(f'Public boundary and syntax checked: {len(files)} files. Training and live-service tests were not run.')


if __name__ == '__main__':
    main()
