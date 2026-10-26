"""Exercise debugger queue dispatch without creating windows or reading HID."""
import queue
import unittest
from unittest.mock import Mock, patch

import app


class DebuggerQueueTests(unittest.TestCase):
    def setUp(self):
        self.jobs = queue.Queue()
        self.errors = Mock()

    def test_input_lines_preserve_each_captured_value(self):
        append = Mock()
        target = object()
        for line in ('[1] 01 02', '[2] 03 04'):
            app.queue_ui_callback(self.jobs, append, target, line)
        app.drain_ui_queue(self.jobs, self.errors)
        self.assertEqual([c.args for c in append.call_args_list],
                         [(target, '[1] 01 02'), (target, '[2] 03 04')])
        self.errors.assert_not_called()

    def test_monitor_finished_can_call_keyword_only_action(self):
        button = Mock()
        app.queue_ui_callback(self.jobs, button.configure, text='Start monitoring')
        app.drain_ui_queue(self.jobs, self.errors)
        button.configure.assert_called_once_with(text='Start monitoring')
        self.errors.assert_not_called()

    def test_callback_failure_does_not_drop_next_result(self):
        bad = Mock(side_effect=ValueError('bad callback'))
        good = Mock()
        self.jobs.put(('cb', bad, 'first', None))
        self.jobs.put(('cb', good, 'second', None))
        app.drain_ui_queue(self.jobs, self.errors)
        good.assert_called_once_with('second')
        self.assertEqual(len(self.errors.call_args_list), 1)
        self.assertIsInstance(self.errors.call_args.args[0], ValueError)

    def test_worker_error_does_not_drop_next_result(self):
        error = RuntimeError('device disconnected')
        good = Mock()
        self.jobs.put(('err', None, error, 'trace'))
        self.jobs.put(('cb', good, 123, None))
        app.drain_ui_queue(self.jobs, self.errors)
        self.errors.assert_called_once_with(error, 'trace')
        good.assert_called_once_with(123)

    def test_failed_error_rendering_does_not_drop_next_result(self):
        good = Mock()
        self.jobs.put(('cb', Mock(side_effect=ValueError('bad')), None, None))
        self.jobs.put(('cb', good, 123, None))
        with patch.object(app.traceback, 'print_exc'):
            app.drain_ui_queue(self.jobs, Mock(side_effect=RuntimeError('widget gone')))
        good.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
