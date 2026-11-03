"""fa0303ce descriptor fixtures; battery frames below are synthetic, not captures."""
from contextlib import ExitStack
from dataclasses import replace
import itertools
import unittest
from unittest.mock import Mock, patch

import battery as b
import receiver_battery as rx
import xinput_battery
import shared_readings as shared
import my_devices


def iface(pid, page, inp=64, out=64, usage=1):
    return b.hw.HidInterface(f'fixture-{pid:x}-{page:x}',0x0B05,pid,0,page,usage,
                             inp,out,0,'ROG receiver','ASUSTek','')


MOUSE = iface(0x1AD0,0xFF01)
HEADSET = iface(0x1B84,0xFF00)


def frame(hexadecimal):
    return bytes.fromhex(hexadecimal).ljust(64,b'\0')


class RogWirelessTests(unittest.TestCase):
    def test_captured_interface_selection_rejects_unused_keyboard_slot(self):
        good = (MOUSE, HEADSET)
        bad = (iface(0x1AD0,0xFF00), iface(0x1AD0,0xFF02),
               iface(0x1AD0,0xFFC1,21,0),iface(0x1B84,0xFF07,63,61,0x212),
               iface(0x1B84,0xFF0B,61,61,0x104),iface(0x1ADE,0xFF72,65,65,0xA1),
               replace(HEADSET,output_len=65),replace(MOUSE,usage=2))
        for d in good:
            self.assertTrue(rx.matches(d))
            self.assertTrue(b._is_candidate_interface(d,[]))
        for d in bad:
            self.assertFalse(rx.matches(d))
            dev = Mock()
            with self.assertRaises(ValueError):rx.query(dev,d)
            self.assertEqual(dev.mock_calls,[])

    def test_mouse_and_headset_percent_offsets_are_different(self):
        mouse = rx.decode('rog_harpe2',frame('03120700004b0214630f00'))
        self.assertEqual((mouse.level,mouse.voltage_mv,mouse.status),(75,3939,'discharging'))
        headset = rx.decode('rog_pelta',frame('cc12070000054d1401'))
        self.assertEqual((headset.level,headset.status),(77,'unknown'))
        self.assertNotEqual(headset.level,5)  # 5 is sleep timer.
        self.assertIsNone(rx.decode('rog_harpe2',frame('cc12070000054d1401')))

    def test_empty_echo_nak_truncation_and_out_of_range_are_rejected(self):
        for protocol,rid in (('rog_harpe2','03'),('rog_pelta','cc')):
            bad = (frame(rid+'1207'),frame(rid+'ffaa'),frame(rid+'12070000ffaa'),
                   frame(rid+'120700006565'), frame(rid+'120700004b64')[:10],
                   frame('051201000001'))
            for raw in bad:
                self.assertIsNone(rx.decode(protocol,raw),(protocol,raw.hex()))

    def test_zero_percent_requires_nonempty_status(self):
        self.assertEqual(rx.decode('rog_harpe2',frame('0312070000000214630f00')).level,0)
        self.assertEqual(rx.decode('rog_pelta',frame('cc1207000005001401')).level,0)

    def test_mouse_query_sends_only_rid3_battery_get(self):
        dev = Mock()
        dev.read_input.side_effect = [frame('031207'),frame('031200000004'),frame('03120700004b0214630f01')]
        result = rx.query(dev,MOUSE)
        self.assertEqual((result.level,result.status),(75,'charging'))
        dev.write_output.assert_called_once_with(frame('031207'),use_hidd=False)
        dev.set_feature_report.assert_not_called()

    def test_headset_charge_is_separate_and_failed_status_preserves_battery(self):
        dev = Mock()
        dev.read_input.side_effect = [frame('cc12070000054d1401'),frame('cc1208000001')]
        result = rx.query(dev,HEADSET)
        self.assertEqual((result.level,result.status),(77,'charging'))
        self.assertEqual([c.args[0] for c in dev.write_output.call_args_list],
                         [frame('cc1207'),frame('cc1208')])
        with patch.object(rx,'_input_query',side_effect=[(rx.Reading(77),b'battery'),OSError('busy')]):
            result = rx.query(Mock(),HEADSET)
        self.assertEqual((result.level,result.status,result.raw),(77,'unknown',b'battery'))

    def test_failed_write_and_timeout_never_report_zero(self):
        dev = Mock();dev.write_output.return_value=False
        with self.assertRaises(OSError):rx.query(dev,HEADSET)
        dev.read_input.assert_not_called()
        dev = Mock();dev.read_input.return_value=frame('cc1207')
        with patch.object(rx.time,'monotonic',side_effect=itertools.count()):
            self.assertIsNone(rx.query(dev,HEADSET))

    def test_discovery_only_two_cards_and_home_uses_parsed_values(self):
        devices = [MOUSE,HEADSET,iface(0x1AD0,0xFF00),iface(0x1AD0,0xFF02),
                   iface(0x1B84,0xFF07,63,61,0x212)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(b.hw,'enumerate_hid',return_value=devices))
            stack.enter_context(patch.object(b,'_load_custom_models',return_value={}))
            stack.enter_context(patch.object(xinput_battery,'readings',return_value=[]))
            stack.enter_context(patch.object(rx,'HidDevice'))
            stack.enter_context(patch.object(rx,'query',side_effect=[rx.Reading(75),rx.Reading(77)]))
            legacy=stack.enter_context(patch.object(b,'strategy_asus_rog'))
            presence={}
            rows=b.discover_and_read_all(presence=presence)
        self.assertEqual(len(rows),2)
        legacy.assert_not_called()
        self.assertIn('SPEEDNOVA',rows[0].title)
        self.assertTrue(rows[1].is_headset)
        snap=shared.merge_snapshot(shared.empty_snapshot(),rows,1)
        self.assertEqual([r.level for r in my_devices.display_readings(shared.readings_from(snap))],[75,77])
        phantom=b.DiscoveredReading('0B05:1AD0:FF00','Old keyboard slot','',level=12)
        old=shared.merge_snapshot(shared.empty_snapshot(),[phantom],0)
        updated=shared.merge_snapshot(old,rows,2,presence=presence)
        self.assertEqual(len(updated['readings']),2)
        self.assertNotIn(phantom.key,[r['key'] for r in updated['readings']])


if __name__ == '__main__':unittest.main()
