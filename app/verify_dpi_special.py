import unittest
from unittest.mock import Mock, patch
import dpi_hecate as hc
import dpi_pulsar as ps
import dpi_adapters as da
import dpi_extended as dx
import mouse_settings as ms
from verify_dpi_adapters import iface


class HecateTests(unittest.TestCase):
    def fixture(self):
        header = bytearray(34);header[6] = 24;header[9:11] = (13205).to_bytes(2, 'little')
        header[11:13] = bytes([50, 7]);header[14] = 50
        raw = bytearray(range(84));raw[0] = 0;raw[12] = 1
        for i, v in enumerate((400, 800, 1600, 3200, 6400, 12000, 26000)):
            raw[14+i*9] = int(i != 6);raw[16+i*9:18+i*9] = (v//50-1).to_bytes(2, 'little')
        calls = []
        def query(dev, cmd, offset=0, data=b'', length=None):
            calls.append((cmd, offset, data, length))
            if cmd == 3:return bytes(header[offset:offset+length])
            if cmd == 5:return bytes(raw[offset:offset+length])
            if cmd == 6:raw[offset:offset+len(data)] = data
            return b''
        return header, raw, calls, query

    def test_full_roundtrip_preserves_colors_sensor_flags_and_disabled_slots(self):
        header, raw, calls, query = self.fixture()
        mouse = ms.Mouse('g3m', 'HECATE', '', 'dpi', (0x320F, 0x706E))
        with patch.object(hc, 'query', side_effect=query), patch.object(hc.time, 'sleep'):
            before = hc.read(None, mouse)
            wanted = hc.write(None, None, before, 2, 2400, True)
            after = hc.read(None, mouse)
        self.assertEqual(da.fingerprint(wanted), da.fingerprint(after))
        self.assertEqual(after.stages[2], (2400, 2400))
        changed = [i for i, (a, b) in enumerate(zip(before.raw, after.raw)) if a != b]
        self.assertEqual(changed, [12, 34])
        self.assertEqual([c[1] for c in calls if c[0] == 6], [0, 24, 48, 72])
        self.assertIn((0x28, 0, b'\2', None), calls)
        self.assertEqual(calls[-1][0], 2)

    def test_disabled_stage_and_changed_sensor_refuse_writes(self):
        header, raw, calls, query = self.fixture()
        mouse = ms.Mouse('g3m', '', '', 'dpi', (0x320F, 0x706E))
        with patch.object(hc, 'query', side_effect=query), patch.object(hc.time, 'sleep'):
            s = hc.read(None, mouse);calls.clear()
            with self.assertRaises(ValueError):hc.write(None, None, s, 6, None, True)
            self.assertEqual(calls, [])
            header[9] ^= 1
            with self.assertRaises(ValueError):hc.write(None, None, s, 2, 2400, True)
            self.assertFalse(any(c[0] in (6, 0x28) for c in calls))
            self.assertEqual(calls[-1][0], 2)

    def test_packet_address_checksum_and_negative_ack(self):
        def exchange(dev, packet, accept):
            self.assertEqual(packet[1:3], sum(packet[3:]).to_bytes(2, 'little'))
            self.assertEqual(packet[3:7], bytes([5, 4, 24, 0]))
            reply = bytearray(packet);reply[8:12] = b'abcd'
            self.assertTrue(accept(reply));reply[5] = 25;self.assertFalse(accept(reply))
            reply[3] = 255;self.assertTrue(accept(reply))
            return reply
        with patch.object(dx, 'input_exchange', side_effect=exchange):
            with self.assertRaisesRegex(ValueError, '拒绝'):hc.query(None, 5, 24, length=4)


class PulsarTests(unittest.TestCase):
    def test_current_dpi_roundtrip_uses_ultra_codec(self):
        d = iface(0x3710, 0x5406);mouse = ms.Mouse(d.path, 'Pulsar', '', 'dpi', (d.vid, d.pid))
        row = bytearray(ms.atk_encode(800, 800, 'ultra'));events = []
        header = bytes.fromhex('015404510253')
        def query(dev, d, cmd, address=0, data=b'', length=None):
            if cmd == 1:return bytes(4)+bytes([0x57, 4, 1, 0])+bytes(2)
            if cmd == 3:
                self.assertEqual(length, 0);events.append(data[0]);return data+bytes(9)
            if cmd == 14:return bytes(10)
            if cmd == 8:return header.ljust(10, b'\0') if address == 0 else bytes(row).ljust(10, b'\0')
            if cmd == 7:
                self.assertEqual(address, 20);row[:] = data;return bytes(10)
            raise AssertionError(cmd)
        with patch.object(ps, 'query', side_effect=query):
            before = ps.read(None, d, mouse)
            self.assertEqual(before.stages, ((800, 800),))
            wanted = ps.write(None, d, before, 0, 30100, False)
            after = ps.read(None, d, mouse)
        self.assertEqual(da.fingerprint(wanted), da.fingerprint(after))
        self.assertEqual(after.stages, ((30100, 30100),))
        self.assertEqual(events, [1, 0, 1, 0, 1, 0])

    def test_busy_is_bounded_and_wrong_identity_precedes_online(self):
        with patch.object(ps, 'query', return_value=bytes([1, 0, 0, 0, 1])+bytes(5)) as q, patch.object(ps.time, 'sleep'):
            with self.assertRaises(TimeoutError):ps.online(None, None, True)
            self.assertEqual(q.call_count, 5)
        with patch.object(ps, 'query', return_value=bytes(4)+bytes([0x57, 4, 1, 0])+bytes(2)) as q:
            with self.assertRaises(ValueError):
                with ps.session(None, None, (0x57, 99, 1, 0)):pass
            self.assertEqual(q.call_count, 1)

    def test_feature_gate_and_nonrepresentable_steps(self):
        self.assertNotIn(30150, ps.OPTIONS);self.assertNotIn(10010, ps.OPTIONS)
        self.assertTrue(ps.matches(iface(0x3710, 0x5406, usage_page=0xFF02, usage=2, output_len=17, input_len=17)))
        self.assertFalse(ps.matches(iface(0x3554, 0x5406, usage_page=0xFF02, usage=2, output_len=17, input_len=17)))


if __name__ == '__main__':unittest.main(verbosity=2)
