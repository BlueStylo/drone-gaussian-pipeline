import importlib.util
from pathlib import Path
import tempfile
import unittest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'evaluate_training.py'
spec = importlib.util.spec_from_file_location('evaluation', SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def test_mismatched_aspect_ratio_is_rejected_instead_of_stretched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (16,9)).save(root/'reference.png')
            Image.new('RGB', (8,8)).save(root/'render.png')
            with self.assertRaises(ValueError):
                evaluation.load_pair(root/'render.png', root/'reference.png')

    def test_identical_view_keeps_infinite_psnr_out_of_json_numbers(self):
        result = evaluation.statistics([{'psnr_db':None,'mse_rgb_255':0,'rgb_values':12}])
        self.assertEqual(result['identical_views_with_infinite_psnr'], 1)
        self.assertIsNone(result['pooled_psnr_db'])
        self.assertEqual(result['pooled_mse_rgb_255'], 0)


if __name__ == '__main__':
    unittest.main()
