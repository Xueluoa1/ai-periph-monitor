"""Mock WinAPI tests; no handles are opened and no hardware is accessed."""
import ctypes
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hid_win


def load_copies():
    yield hid_win
    root = Path(__file__).resolve().parents[1]
    for name in ('peripheral_diagnostic', 'am_diagnostic'):
        spec = importlib.util.spec_from_file_location('lifecycle_' + name,
                                                     root / 'tools' / name / 'hid_win.py')
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module


class HidLifecycleTests(unittest.TestCase):
    def device(self, module):
        dev = object.__new__(module.HidDevice)
        dev._caps = SimpleNamespace(InputReportByteLength=17)
        dev._read_handle, dev._read_event = 101, 102
        return dev

    def test_cancel_waits_for_completion_in_all_copies(self):
        for module in load_copies():
            with self.subTest(module=module.__name__):
                api = Mock()
                with patch.object(module, 'kernel32', api):
                    self.device(module)._cancel_read(module.OVERLAPPED())
                self.assertEqual([c[0] for c in api.mock_calls],
                                 ['CancelIoEx', 'GetOverlappedResult'])
                self.assertIs(api.GetOverlappedResult.call_args.args[-1], True)

    def test_pending_read_cleanup_on_stop_exception_and_normal_stop(self):
        for module in load_copies():
            for raising in (False, True):
                with self.subTest(module=module.__name__, raising=raising):
                    dev = self.device(module)
                    api = Mock(ReadFile=Mock(return_value=False))
                    stop = Mock(side_effect=ValueError('stop failed')) if raising else lambda: True
                    with patch.object(module, 'kernel32', api), \
                            patch.object(ctypes, 'get_last_error', return_value=module.ERROR_IO_PENDING):
                        if raising:
                            with self.assertRaisesRegex(ValueError, 'stop failed'):
                                dev.read_input(stop_flag=stop)
                        else:
                            self.assertIsNone(dev.read_input(stop_flag=stop))
                    api.CancelIoEx.assert_called_once()
                    self.assertIs(api.GetOverlappedResult.call_args.args[-1], True)

    def test_synchronous_read_does_not_cancel(self):
        for module in load_copies():
            with self.subTest(module=module.__name__):
                api = Mock(ReadFile=Mock(return_value=True))
                with patch.object(module, 'kernel32', api):
                    self.assertEqual(self.device(module).read_input(), b'')
                api.CancelIoEx.assert_not_called()

    def test_pending_timeout_cleans_up(self):
        for module in load_copies():
            with self.subTest(module=module.__name__):
                api = Mock(ReadFile=Mock(return_value=False))
                with patch.object(module, 'kernel32', api), \
                        patch.object(ctypes, 'get_last_error', return_value=module.ERROR_IO_PENDING), \
                        patch.object(module.time, 'monotonic', side_effect=[0, 2]):
                    self.assertIsNone(self.device(module).read_input(timeout_ms=1))
                api.CancelIoEx.assert_called_once()
                self.assertIs(api.GetOverlappedResult.call_args.args[-1], True)


if __name__ == '__main__':
    unittest.main()
