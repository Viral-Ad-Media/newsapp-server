import os
from unittest.mock import patch
from django.test import SimpleTestCase
from .writing_config import openai_key


class WritingConfigurationTests(SimpleTestCase):
    def test_existing_operator_alias_is_supported(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPEN_AI_API': ' alias-fixture-key '}):
            self.assertEqual(openai_key(), 'alias-fixture-key')

    def test_standard_name_takes_priority(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'standard-fixture-key', 'OPEN_AI_API': 'alias-fixture-key'}):
            self.assertEqual(openai_key(), 'standard-fixture-key')

    def test_missing_both_values_stays_unconfigured(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ' ', 'OPEN_AI_API': ''}):
            self.assertEqual(openai_key(), '')
