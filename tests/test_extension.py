"""Small synthetic source runs exercise copy/resume invariants, without SfM."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pipeline import reconstruct as p
from pipeline import extend_reconstruction as e


def image_model(names):
    data = bytearray(struct.pack('<Q', len(names)))
    for index, name in enumerate(names, 1):
        data.extend(struct.pack('<i7di', index, 1, 0, 0, 0, 0, 0, 0, 1))
        data.extend(name.encode()+b'\0'+struct.pack('<Q', 0))
    return bytes(data)


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root/'source'; self.run = self.root/'expanded'
        (self.source/'frames').mkdir(parents=True); model = self.source/'sparse'/'0'; model.mkdir(parents=True)
        names = [f's001_orbit_{i:06d}.jpg' for i in range(1, 4)]
        for name in names: (self.source/'frames'/name).write_bytes(b'synthetic image')
        (self.source/'frames'/'._ignored.jpg').write_bytes(b'not an image')
        (model/'images.bin').write_bytes(image_model(names))
        (model/'cameras.bin').write_bytes(struct.pack('<Q', 0))
        (model/'points3D.bin').write_bytes(struct.pack('<Q', 0))
        (self.source/'database.db').write_bytes(b'synthetic closed database for copy tests')
        p.write_json(self.source/'run_config.json', {'sfm': dict(p.DEFAULTS),
            'segments': [{'prefix': 's001_orbit_', 'path': 'historical-input.mp4'}]})
        p.write_json(self.source/'metrics.json', {'status': 'sfm_complete', 'extracted_frames': 3,
            'registered_frames': 3, 'selected_model': str(model)})
        (self.root/'new.mp4').write_bytes(b'synthetic new video')
        self.config = self.root/'extension.json'
        self.request = {'source_run': 'source', 'output_run': 'expanded',
                        'segments': [{'path': 'new.mp4', 'start': 0, 'end': 4, 'label': 'town'}]}
        self.write_request()
        response = json.dumps({'format': {'duration': '4'}, 'streams': [{'codec_type': 'video',
                              'width': 1600, 'height': 900, 'color_transfer': 'bt709'}]}).encode()
        self.tools = patch.object(p, 'tool', side_effect=lambda name: name)
        self.probe = patch.object(p.subprocess, 'check_output', return_value=response)
        self.tools.start(); self.probe.start()
        self.addCleanup(self.tools.stop); self.addCleanup(self.probe.stop)

    def write_request(self): self.config.write_text(json.dumps(self.request))

    def snapshot(self):
        return {str(x.relative_to(self.source)): hashlib.sha256(x.read_bytes()).hexdigest()
                for x in self.source.rglob('*') if x.is_file()}

    def test_preparation_accepts_arbitrary_source_count_and_copies_without_mutation(self):
        before = self.snapshot(); run, config = e.prepare(self.config, min_free_gib=0)
        self.assertEqual(config['source_frames'], 3)
        self.assertEqual(config['segments'][-1]['prefix'], 's002_town_')
        self.assertEqual(len(list((run/'frames').glob('*.jpg'))), 3)
        self.assertEqual((run/'database.db').read_bytes(), (self.source/'database.db').read_bytes())
        self.assertEqual(self.snapshot(), before)
        copied = run/'frames'/'s001_orbit_000001.jpg'; copied.write_bytes(b'local change')
        self.assertEqual(self.snapshot(), before, 'The copied frames must not be hardlinks to originals')

    def test_resume_is_idempotent_but_configuration_drift_is_rejected(self):
        run, config = e.prepare(self.config, min_free_gib=0)
        marker = run/'logs'/'keep.txt'; marker.write_text('keep')
        self.assertEqual(e.prepare(self.config, min_free_gib=0), (run, config))
        self.request['min_registration'] = .9; self.write_request()
        with self.assertRaisesRegex(ValueError, 'configuration changed'): e.prepare(self.config, min_free_gib=0)
        self.assertEqual(marker.read_text(), 'keep')

    def test_nested_source_and_destination_are_rejected_before_writes(self):
        for destination in ['source', 'source/nested', '.']:
            with self.subTest(destination=destination):
                self.request['output_run'] = destination; self.write_request()
                with self.assertRaisesRegex(ValueError, 'non-nested'): e.prepare(self.config, min_free_gib=0)
        self.assertFalse((self.source/'nested').exists())

    def test_partial_destination_is_not_overwritten(self):
        self.run.mkdir(); marker = self.run/'keep'; marker.write_text('keep')
        with self.assertRaises(ValueError): e.prepare(self.config, min_free_gib=0)
        self.assertEqual(marker.read_text(), 'keep')

    def test_open_source_database_and_incomplete_frames_are_rejected(self):
        sidecar = self.source/'database.db-wal'; sidecar.write_bytes(b'pending')
        with self.assertRaisesRegex(ValueError, 'checkpointed'): e.prepare(self.config, min_free_gib=0)
        sidecar.unlink(); (self.source/'frames'/'s001_orbit_000001.jpg').unlink()
        with self.assertRaisesRegex(ValueError, 'frame count'): e.prepare(self.config, min_free_gib=0)
        self.assertFalse(self.run.exists())

    def test_multiple_new_clips_are_explicitly_rejected(self):
        self.request['segments'] *= 2; self.write_request()
        with self.assertRaisesRegex(ValueError, 'exactly one clip'): e.prepare(self.config, min_free_gib=0)
        self.assertFalse(self.run.exists())

    def test_missing_copied_frame_stops_before_any_external_stage(self):
        run, config = e.prepare(self.config, min_free_gib=0)
        (run/'frames'/'s001_orbit_000001.jpg').unlink()
        runner = SimpleNamespace(run=run)
        with self.assertRaisesRegex(RuntimeError, 'frame set changed'): e.sfm(runner, config)

    def test_extension_import_and_help_are_independent_of_installed_tools(self):
        result = subprocess.run([sys.executable, '-m', 'pipeline.extend_reconstruction', '--help'],
                                capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--config', result.stdout)


if __name__ == '__main__':
    unittest.main()
