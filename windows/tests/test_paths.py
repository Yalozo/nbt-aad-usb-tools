from pathlib import Path
import unittest
from unittest.mock import patch
from nbt_aad_pc.paths import state_directory


class StorageTest(unittest.TestCase):
    def test_mac_uses_application_support_even_if_windows_env_is_set(self):
        with patch('nbt_aad_pc.paths.sys.platform', 'darwin'), \
             patch('nbt_aad_pc.paths.Path.home', return_value=Path('/Users/test')), \
             patch.dict('os.environ', {'LOCALAPPDATA': '/unrelated'}, clear=True):
            self.assertEqual(state_directory(), Path('/Users/test/Library/Application Support/NBT-AAD-PC'))

    def test_windows_retains_existing_storage_and_offline_library(self):
        with patch('nbt_aad_pc.paths.sys.platform', 'win32'), \
             patch.dict('os.environ', {'LOCALAPPDATA': 'user-data'}, clear=True):
            self.assertEqual(state_directory(), Path('user-data/NBT-AAD-PC'))
