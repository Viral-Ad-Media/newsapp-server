import importlib.util
import json
from pathlib import Path
import tempfile
from django.test import SimpleTestCase


class WorkerBuildTests(SimpleTestCase):
    def test_static_build_needs_no_database_and_emits_no_functions(self):
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location('worker_site', root/'scripts/build-worker-site.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = module.build(directory)
            self.assertEqual([p.name for p in output.iterdir()], ['index.html'])
            self.assertLess((output/'index.html').stat().st_size, 1024)
            self.assertIn('does not indicate worker health', (output/'index.html').read_text())
        cfg = json.loads((root/'vercel.json').read_text())
        self.assertIsNone(cfg['framework'])
        self.assertEqual(cfg['outputDirectory'], 'worker-site')
        self.assertEqual(cfg['daemons']['news-worker']['instances'], {'iad1': 1})
        self.assertNotIn('vercel_build.py', cfg['buildCommand'])
