import unittest
from unittest.mock import Mock, patch
from dataclasses import replace
import dpi_more as more
import dpi_adapters as da
import mouse_settings as ms
from verify_dpi_adapters import iface


class ExactDpiTests(unittest.TestCase):
    def rk_fixture(self):
        raw = bytearray(56)
        raw[1:10] = bytes([0x11, 0x24, 2, 4, 0x60, 3, 7, 12, 4])
        for i, v in enumerate((400, 800, 1600, 3200, 6400, 12000)):
            raw[11+i*2:13+i*2] = (v//50-1).to_bytes(2, 'little')
        raw[27:45] = bytes(range(18))
        raw[54:] = b'\xa5\x5a'
        return bytes(raw)

    def test_rk_full_read_and_current_edit_preserves_inactive_colors(self):
        raw = self.rk_fixture()
        mouse = ms.Mouse('rk', 'RK', '372E:1019', 'dpi', (0x372E, 0x1019))
        def block(dev, offset):return raw.ljust(60, b'\0')[(offset-64)*10:(offset-63)*10]
        with patch.object(more, 'rk_block', side_effect=block):config = more.rk_read(None, mouse)
        self.assertEqual(config.stages, ((800, 800),))
        self.assertEqual(config.selector, 1)
        self.assertFalse(config.can_activate)
        dev = Mock()
        with patch.object(more.time, 'sleep'):
            wanted = more.write(dev, None, config, 0, 1200, False)
        sent = dev.set_feature_report.call_args.args[0]
        self.assertEqual(sent[:1], b'\x03')
        self.assertEqual(sent[1], sum(sent[2:63]) & 255)
        self.assertEqual(sent[2:13], bytes.fromhex('5006503a00241700030405'))
        self.assertEqual(wanted.raw[:13], raw[:13])
        self.assertEqual(wanted.raw[15:], raw[15:])
        self.assertEqual(wanted.stages, ((1200, 1200),))

    def test_rk_bad_marker_and_unknown_range_rejected(self):
        mouse = ms.Mouse('rk', '', '')
        for offset, data in ((54, b'\0\0'), (13, (50000).to_bytes(2, 'little'))):
            raw = bytearray(self.rk_fixture());raw[offset:offset+2] = data
            with patch.object(more, 'rk_block', side_effect=lambda dev, n: bytes(raw).ljust(60, b'\0')[(n-64)*10:(n-63)*10]):
                with self.assertRaises(ValueError):more.rk_read(None, mouse)

    def test_rk_ignores_old_sequence_and_wrong_address(self):
        dev = Mock()
        sent = []
        dev.set_feature_report.side_effect = lambda p: sent.append(p)
        counter = [0]
        def reply(rid, size):
            counter[0] += 1
            r = bytearray(64);r[:2] = b'\x03\x50';r[4:6] = bytes([sent[-1][4], 2])
            if counter[0] == 1:r[4] = (r[4]+1) & 255
            if counter[0] == 2:r[5] = 3
            r[6:16] = bytes(range(10))
            return bytes(r)
        dev.get_feature_report.side_effect = reply
        with patch.object(more.time, 'sleep'):
            self.assertEqual(more.rk_block(dev, 0x42), bytes(range(10)))
        self.assertEqual(counter[0], 3)

    def test_exact_interface_gates(self):
        self.assertEqual(more.family(iface(0x372E, 0x1019, usage_page=0xFF00, feature_len=64)), 'rk')
        self.assertFalse(more.family(iface(0x372E, 0x1014, usage_page=0xFF00, feature_len=64)))
        self.assertEqual(more.family(iface(0x3554, 0xF5F7, usage_page=0xFF02, usage=2, output_len=17, input_len=17)), 'scyrox')
        self.assertFalse(more.family(iface(0x3554, 0xF5F7, usage_page=0xFF02, usage=2, output_len=64, input_len=64)))

    def test_scyrox_read_and_edit_roundtrip_preserves_unrelated_bytes(self):
        d = iface(0x3554, 0xF5F7);mouse = ms.Mouse(d.path, 'Scyrox', d.vid_pid, 'dpi', (d.vid, d.pid))
        memory = bytearray(44);memory[:6] = bytes.fromhex('015404510154')
        for i, v in enumerate((400, 800, 1600, 3200)):
            memory[12+i*4:16+i*4] = ms.atk_encode(v, v, 'step50')
        sessions = []
        def query(dev, iface, cmd, address=0, data=b'', length=None):
            if cmd == 2:sessions.append(data[0]);return bytes(10)
            if cmd == 1:return bytes(4)+bytes([2, 68, 1])+bytes(3)
            if cmd == 14:return bytes(10)
            if cmd == 8:return bytes(memory[address:address+length]).ljust(10, b'\0')
            if cmd == 7:memory[address:address+len(data)] = data;return bytes(10)
            raise AssertionError(cmd)
        with patch.object(more, 'scyrox_query', side_effect=query):
            config = more.scyrox_read(None, d, mouse)
            wanted = more.write(None, d, config, 2, 2400, True)
            after = more.scyrox_read(None, d, mouse)
        self.assertEqual(da.fingerprint(after), da.fingerprint(wanted))
        self.assertEqual(after.stages[2], (2400, 2400))
        self.assertEqual(after.raw[:4], config.raw[:4])
        self.assertEqual(sessions, [1, 0, 1, 0, 1, 0])

    def test_scyrox_session_closes_on_wrong_identity_and_failure(self):
        calls = []
        def query(dev, d, cmd, **kw):
            calls.append((cmd, kw.get('data')))
            return bytes(4)+bytes([2, 68, 1])+bytes(3)
        with patch.object(more, 'scyrox_query', side_effect=query):
            with self.assertRaises(ValueError):
                with more.scyrox_session(None, None, (2, 99, 1)):pass
            self.assertEqual(calls[-1], (2, b'\0'))
            with self.assertRaises(OSError):
                with more.scyrox_session(None, None):raise OSError('write failed')
            self.assertEqual(calls[-1], (2, b'\0'))


if __name__ == '__main__':unittest.main(verbosity=2)
