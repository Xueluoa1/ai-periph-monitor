"""Shared config persistence regressions; never touch the user's config."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tray_common as tc


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tray_config.json'
        self.config_path = patch.object(tc, 'CONFIG_PATH', str(self.path))
        self.config_path.start()

    def tearDown(self):
        self.config_path.stop()
        self.tmp.cleanup()

    def sample(self):
        cfg = deepcopy(tc.DEFAULT_CONFIG)
        cfg.update(desktop_widget=False, hidden_devices=['test-device'])
        cfg['custom_models']['test-device'] = 'My keyboard'
        return cfg

    def test_defaults_are_independent_between_loads(self):
        defaults = deepcopy(tc.DEFAULT_CONFIG)
        first = tc.load_config()
        first['hidden_devices'].append('changed')
        first['custom_models']['changed'] = 'name'
        first['device_names']['changed'] = 'name'
        first['last_readings']['changed'] = {'level': 12}
        self.assertEqual(tc.DEFAULT_CONFIG, defaults)
        self.assertEqual(tc.load_config(), defaults)

    def test_read_during_save_sees_previous_complete_config(self):
        previous = self.sample()
        tc.save_config(previous)
        changed = deepcopy(previous)
        changed['interval'] = 120
        dump = json.dump

        def interrupted_dump(value, stream, **kwargs):
            stream.write('{')
            stream.flush()
            self.assertEqual(tc.load_config(), previous)
            stream.seek(0)
            stream.truncate()
            dump(value, stream, **kwargs)

        with patch.object(tc.json, 'dump', side_effect=interrupted_dump):
            tc.save_config(changed)
        self.assertEqual(tc.load_config(), changed)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])

    def test_serialization_failure_preserves_existing_file(self):
        previous = self.sample()
        tc.save_config(previous)
        changed = deepcopy(previous)
        changed['custom_models']['bad'] = object()
        with self.assertRaises(TypeError):
            tc.save_config(changed)
        self.assertEqual(tc.load_config(), previous)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])

    def test_replace_failure_preserves_existing_file_and_cleans_temp(self):
        previous = self.sample()
        tc.save_config(previous)
        changed = deepcopy(previous)
        changed['interval'] = 120
        with patch.object(tc.os, 'replace', side_effect=PermissionError), patch.object(tc.time, 'sleep'):
            tc.save_config(changed)
        self.assertEqual(tc.load_config(), previous)
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])

    def test_temporary_reader_lock_is_retried(self):
        previous = self.sample()
        tc.save_config(previous)
        changed = deepcopy(previous)
        changed['interval'] = 120
        replace = tc.os.replace
        attempts = []

        def retry(source, target):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError('reader still open')
            replace(source, target)

        with patch.object(tc.os, 'replace', side_effect=retry), patch.object(tc.time, 'sleep'):
            tc.save_config(changed)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(tc.load_config(), changed)


if __name__ == '__main__':
    unittest.main()
