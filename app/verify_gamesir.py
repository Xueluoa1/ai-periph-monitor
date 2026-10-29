"""Protocol routing, telemetry validation and bounded read-only sessions."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import gamesir_g7 as g7
import peripheral_protocols as catalog


def interface(**changes):
    values = dict(vid=0x3537, pid=0x10B9, usage_page=0xFFF0, usage=0x40,
                  input_len=64, output_len=64)
    values.update(changes)
    return SimpleNamespace(**values)


def report(level=73, report_id=0x12):
    raw = bytearray(64)
    raw[0] = report_id
    raw[1:5] = bytes((128, 128, 128, 128))
    raw[36] = level
    return bytes(raw)


class FakeDevice:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.writes = []

    def write_output(self, data, **kwargs):
        self.writes.append(data)

    def read_input(self, **kwargs):
        return next(self.replies, b'')


class GameSirTests(unittest.TestCase):
    def test_cyclone_uses_its_own_query_and_model(self):
        d=interface(pid=0x100B);dev=FakeDevice([report(64)])
        self.assertTrue(catalog.matches_interface(d))
        parsed,raw=g7.read(dev,d)
        self.assertEqual(parsed,(64,'unknown',g7.MODELS[0x100B]))
        self.assertEqual(dev.writes,[bytes.fromhex('0f03')+bytes(62)])

    def test_failed_query_does_not_consume_old_telemetry(self):
        dev=FakeDevice([report(100)])
        with patch.object(dev,'write_output',return_value=False), patch.object(dev,'read_input') as read:
            with self.assertRaises(OSError):g7.read(dev,interface(pid=0x100B))
            read.assert_not_called()

    def test_exact_catalog_route(self):
        self.assertTrue(catalog.matches_interface(interface()))
        for changes in (dict(pid=0x109C), dict(vid=0x1234), dict(usage=1),
                        dict(usage_page=1), dict(input_len=65), dict(output_len=65)):
            self.assertFalse(g7.matches(interface(**changes)))

    def test_percentage_boundaries(self):
        for level in (0, 1, 73, 100):
            self.assertEqual(g7.decode(report(level)), (level, 'unknown', g7.MODEL))

    def test_invalid_packets_and_wrong_mode(self):
        for raw in (None, b'', report()[:-1], report()+b'\0', report(101),
                    report(255), report(report_id=0x10), b'\x12'+bytes(63)):
            self.assertIsNone(g7.decode(raw))

    def test_charge_flag_does_not_invent_charging(self):
        raw = bytearray(report()); raw[35] = 1
        self.assertEqual(g7.decode(raw)[1], 'unknown')

    def test_session_ignores_configuration_reply(self):
        dev = FakeDevice([report(report_id=0x10), report(42)])
        parsed, raw = catalog._active_read(dev, interface(), 'gamesir_g7')
        self.assertEqual(parsed[0], 42)
        self.assertEqual(raw, report(42))
        self.assertEqual(dev.writes, [bytes((15, 242))+bytes(62)])

    def test_wrong_interface_never_sends(self):
        dev = FakeDevice([])
        with self.assertRaises(ValueError):
            g7.read(dev, interface(pid=0x109C))
        self.assertEqual(dev.writes, [])

    def test_timeout_bounded_heartbeats(self):
        dev = FakeDevice([])
        with patch.object(g7.time, 'monotonic', side_effect=[0, .01, .01, .51, .51, 1.01]):
            self.assertEqual(g7.read(dev, interface()), (None, b''))
        self.assertEqual(dev.writes, [g7.HEARTBEAT, g7.HEARTBEAT])


if __name__ == '__main__':
    unittest.main()
