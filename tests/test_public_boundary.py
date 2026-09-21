import importlib.util
from pathlib import Path
import tempfile
import unittest

CHECKER = Path(__file__).resolve().parents[1] / 'scripts' / 'check_project.py'
spec = importlib.util.spec_from_file_location('public_check', CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PublicBoundaryTests(unittest.TestCase):
    def test_model_and_credential_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['model.ply', '.dev.vars']:
                path = root / name
                path.write_text('private runtime payload')
                self.assertTrue(checker.public_errors(path, root))

    def test_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.txt'
            source.write_text('data')
            link = root / 'link.txt'
            link.symlink_to(source)
            self.assertIn('symlink', checker.public_errors(link, root)[0])

    def test_private_host_and_home_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'config.json'
            for value in ['/Users' + '/example/data', 'device.' + 'tail123abc.ts.net']:
                path.write_text(value)
                self.assertTrue(checker.public_errors(path, root))

    def test_broken_markdown_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'README.md'
            path.write_text('[run](missing.md)')
            self.assertTrue(checker.public_errors(path, root))
            (root / 'missing.md').write_text('ready')
            self.assertFalse(checker.public_errors(path, root))


if __name__ == '__main__':
    unittest.main()
