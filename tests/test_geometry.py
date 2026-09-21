import importlib.util
from pathlib import Path
import unittest
import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_expanded_geometry.py'
spec = importlib.util.spec_from_file_location('geometry', SCRIPT)
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)


class GeometryTests(unittest.TestCase):
    def test_recovers_known_similarity_without_reflection(self):
        source = np.array([[0,0,0], [1,0,0], [0,2,0], [1,1,3], [-2,1,1]], dtype=float)
        rotation = np.array([[0,-1,0], [1,0,0], [0,0,1]], dtype=float)
        target = 2.5*(source @ rotation.T) + np.array([4,-2,7])
        transform = geometry.umeyama(source, target)
        np.testing.assert_allclose(geometry.aligned(source, transform), target, atol=1e-12)
        self.assertAlmostEqual(np.linalg.det(transform[1]), 1.0)
        self.assertAlmostEqual(transform[0], 2.5)

    def test_degenerate_camera_path_does_not_produce_a_transform(self):
        line = np.array([[0,0,0], [1,0,0], [2,0,0]], dtype=float)
        with self.assertRaises(ValueError):
            geometry.umeyama(line, line)

    def test_colmap_world_to_camera_translation_is_inverted(self):
        self.assertEqual(geometry.camera_center([1,0,0,0], [2,3,4]), [-2,-3,-4])
        with self.assertRaises(ValueError):
            geometry.camera_center([0,0,0,0], [1,2,3])


if __name__ == '__main__':
    unittest.main()
