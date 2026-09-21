"""Synthetic inputs and tiny subprocesses only; no reconstruction tools needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline import reconstruct as p


def probe(width=3840, transfer='bt709'):
    return json.dumps({'format': {'duration': '10', 'tags': {'location': 'synthetic-private-tag'}},
        'streams': [{'codec_type': 'video', 'width': width, 'height': 2160,
                     'codec_name': 'h264', 'avg_frame_rate': '30000/1001',
                     'duration': '9.9', 'color_transfer': transfer, 'tags': {'serial': 'synthetic'}}]}).encode()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'clip.mp4').write_bytes(b'synthetic video placeholder')
        self.manifest = self.root/'manifest.json'

    def manifest_for(self, **changes):
        segment = {'path': 'clip.mp4', 'start': 1, 'end': 9, 'label': 'test orbit'}
        segment.update(changes)
        self.manifest.write_text(json.dumps({'segments': [segment]}))
        return self.manifest

    def test_manifest_resolves_relative_paths_and_never_persists_raw_probe_tags(self):
        with patch.object(p.subprocess, 'check_output', return_value=probe()):
            segments, metadata = p.read_manifest(self.manifest_for(), 'unused-ffprobe')
        self.assertEqual(segments[0]['path'], str(self.root/'clip.mp4'))
        self.assertEqual(segments[0]['prefix'], 's001_test_orbit_')
        self.assertEqual(segments[0]['end']-segments[0]['start'], 8)
        self.assertEqual(metadata[segments[0]['path']]['width'], 3840)
        self.assertNotIn('synthetic-private-tag', json.dumps(metadata))
        self.assertNotIn('serial', json.dumps(metadata))

    def test_invalid_segments_hdr_and_missing_video_fail_before_output(self):
        for start, end in [(-1, 2), (3, 3), (4, 2), (0, 12), (float('nan'), 2)]:
            with self.subTest(start=start, end=end), patch.object(p.subprocess, 'check_output', return_value=probe()):
                with self.assertRaises(ValueError):
                    p.read_manifest(self.manifest_for(start=start, end=end), 'probe')
        for data in [probe(transfer='smpte2084'), b'{"format":{"duration":"10"},"streams":[]}']:
            with self.subTest(probe=data), patch.object(p.subprocess, 'check_output', return_value=data):
                with self.assertRaises(ValueError):
                    p.read_manifest(self.manifest_for(), 'probe')
        self.assertFalse((self.root/'runs').exists())

    def test_mixed_dimensions_are_not_silently_used_as_one_camera(self):
        (self.root/'second.mp4').write_bytes(b'second synthetic input')
        self.manifest.write_text(json.dumps([{'path': 'clip.mp4'}, {'path': 'second.mp4'}]))
        with patch.object(p.subprocess, 'check_output', side_effect=[probe(), probe(width=1920)]):
            with self.assertRaisesRegex(ValueError, 'same input dimensions'):
                p.read_manifest(self.manifest, 'probe')

    def test_new_run_and_mount_guards_preserve_existing_files(self):
        run = self.root/'run'; run.mkdir(); marker = run/'keep.txt'; marker.write_text('keep')
        with self.assertRaises(ValueError): p.ensure_new_run(run)
        alias = self.root/'alias'; alias.symlink_to(run, target_is_directory=True)
        with self.assertRaises(ValueError): p.ensure_new_run(alias)
        with self.assertRaises(RuntimeError):
            p.storage_check(self.root/'absent'/'output', 0, self.root/'missing-mount')
        self.assertFalse((self.root/'absent').exists())
        self.assertEqual(marker.read_text(), 'keep')

    def test_resume_rejects_configuration_drift_and_wrong_run_kind(self):
        config = {'sfm': dict(p.DEFAULTS), 'kind': 'base'}
        p.validate_resume(config, {'fps': 1.0})
        with self.assertRaises(ValueError): p.validate_resume(config, {'fps': 2.0})
        with self.assertRaises(ValueError): p.validate_resume({**config, 'kind': 'extension'})
        with self.assertRaises(ValueError): p.validate_settings({**p.DEFAULTS, 'global_stride': 0})

    def test_initialization_is_exclusive_and_cannot_replace_a_config(self):
        run = self.root/'init'
        with p.initialize_run(run):
            with self.assertRaisesRegex(RuntimeError, 'initializing'):
                with p.initialize_run(run): self.fail('second writer acquired initialization')
            p.write_json(run/'run_config.json', {'keep': True})
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            with p.initialize_run(run): self.fail('existing config was accepted')
        self.assertEqual(json.loads((run/'run_config.json').read_text()), {'keep': True})

    def test_checkpoint_reuse_and_changed_or_missing_output_fail_closed(self):
        run = self.root/'run'; run.mkdir(); (run/'logs').mkdir()
        runner = p.Runner(run, dict(p.DEFAULTS))
        self.addCleanup(runner.close)
        output = run/'done.txt'
        command = [sys.executable, '-c', "from pathlib import Path; Path('done.txt').write_text('ok')"]
        runner.stage('synthetic', command, valid=output.is_file)
        self.assertEqual(output.read_text(), 'ok')
        with patch.object(p.subprocess, 'Popen', side_effect=AssertionError('must not execute')):
            runner.stage('synthetic', command, valid=output.is_file)
            with self.assertRaisesRegex(RuntimeError, 'Completed'):
                runner.stage('synthetic', command+['changed'], valid=output.is_file)
            output.unlink()
            with self.assertRaisesRegex(RuntimeError, 'Completed'):
                runner.stage('synthetic', command, valid=output.is_file)

    def test_exclusive_run_lock_rejects_a_second_writer(self):
        run = self.root/'run'; run.mkdir()
        runner = p.Runner(run, dict(p.DEFAULTS))
        try:
            with self.assertRaisesRegex(RuntimeError, 'run lock'):
                p.Runner(run, dict(p.DEFAULTS))
        finally:
            runner.close()
        again = p.Runner(run, dict(p.DEFAULTS))
        again.close()

    def test_training_checks_each_clip_before_locating_brush(self):
        runner = SimpleNamespace(run=self.root, settings=dict(p.DEFAULTS), metrics={
            'stages': {'07_undistort': {'status': 'complete'}},
            'registration_ratio': .95, 'registration_by_segment': {'a': 1.0, 'b': .5}})
        with self.assertRaisesRegex(RuntimeError, 'Registration below'):
            p.run_train(runner, SimpleNamespace(allow_low_registration=False))

    def test_import_and_help_need_no_external_binaries(self):
        env = {**os.environ, 'PATH': '', 'PYTHONDONTWRITEBYTECODE': '1'}
        for arguments in [['-c', 'import pipeline.reconstruct'], ['-m', 'pipeline.reconstruct', '--help']]:
            result = subprocess.run([sys.executable, *arguments], env=env, capture_output=True, text=True,
                                    cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
