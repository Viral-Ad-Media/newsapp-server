import json
import os
import subprocess
import sys
from django.test import SimpleTestCase


class DeploymentHostTests(SimpleTestCase):
    def hosts(self, platform):
        env = {**os.environ, 'DEBUG': 'True', 'ALLOWED_HOSTS': 'api.example.test', 'VERCEL': platform,
               'VERCEL_URL': 'news-server-unique.vercel.app', 'VERCEL_BRANCH_URL': 'news-server-git-main.vercel.app',
               'VERCEL_PROJECT_PRODUCTION_URL': 'news-server.vercel.app'}
        result = subprocess.run([sys.executable, '-c', 'import json; from newsapp.settings import ALLOWED_HOSTS; print(json.dumps(ALLOWED_HOSTS))'], env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_platform_hosts_are_exact_without_wildcard_trust(self):
        hosts = self.hosts('1')
        self.assertIn('api.example.test', hosts)
        self.assertIn('news-server-unique.vercel.app', hosts)
        self.assertIn('news-server-git-main.vercel.app', hosts)
        self.assertIn('news-server.vercel.app', hosts)
        self.assertNotIn('*', hosts)
        self.assertNotIn('.vercel.app', hosts)

    def test_platform_headers_do_not_change_non_vercel_configuration(self):
        self.assertEqual(self.hosts('0'), ['api.example.test'])
