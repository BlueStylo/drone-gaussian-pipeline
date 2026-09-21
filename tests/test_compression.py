import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'compress_model.py'
spec = importlib.util.spec_from_file_location('compression', SCRIPT)
compression = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compression)


def fixture(path):
    names = ['x', 'y', 'z', 'nx', 'ny', 'nz', 'f_dc_0', 'f_dc_1', 'f_dc_2']
    names += [f'f_rest_{i}' for i in range(45)] + ['opacity', 'scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3']
    header = 'ply\nformat binary_little_endian 1.0\nelement vertex 4\n'
    header += ''.join(f'property float {name}\n' for name in names) + 'end_header\n'
    with path.open('wb') as stream:
        stream.write(header.encode())
        for i in range(4):
            values = {name: 0.0 for name in names}
            values.update(x=i*.1, y=i*i*.01, z=(i%2)*.1, opacity=2, rot_0=1,
                          scale_0=-2, scale_1=-2, scale_2=-2)
            stream.write(struct.pack('<' + 'f'*len(names), *(values[name] for name in names)))


class CompressionTests(unittest.TestCase):
    def test_real_pinned_encoder_preserves_count_sh_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root/'source.ply', root/'scene.compressed.ply'
            fixture(source)
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--output', str(output)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(output.with_suffix('.verification.json').read_text())
            self.assertEqual(report['output']['vertices'], 4)
            self.assertEqual(report['output']['sh_bands'], 3)
            self.assertTrue(report['source_unchanged'])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
            retry = subprocess.run([sys.executable, str(SCRIPT), str(source), '--output', str(output)],
                                   cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), report['output']['sha256'])

    def test_truncated_and_undeclared_format_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.ply'
            fixture(path)
            valid = path.read_bytes()
            for payload in [valid[:-1], valid.replace(b'format binary_little_endian 1.0\n', b'')]:
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    compression.inspect_ply(path)

    def test_publication_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            temporary, output = root/'temporary', root/'output'
            temporary.write_bytes(b'new')
            output.write_bytes(b'preserved')
            with self.assertRaises(FileExistsError):
                compression.publish_without_replace(temporary, output)
            self.assertEqual(output.read_bytes(), b'preserved')
            self.assertTrue(temporary.exists())


if __name__ == '__main__':
    unittest.main()
