"""Synthetic YS-Tech/RACE exchanges, including mandatory routing restoration."""
import binascii
import itertools
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import barracuda_x as b
import receiver_battery as rx
import gravastar_k1 as k


def iface():
    return SimpleNamespace(vid=0x1532,pid=0x0550,usage_page=0xff00,usage=1,
        input_len=64,output_len=64,feature_len=0,path='test',manufacturer='',product='Barracuda X')


def reply(group=6,seq=1,mv=3975):
    f=bytearray(15);f[:2]=b'\x50\x49';f[8]=5;f[10:13]=bytes((group,seq|128,0))
    f[13:15]=mv.to_bytes(2,'little')
    return bytes((2,len(f)))+bytes(f)+bytes(64-2-len(f))


class BarracudaTests(unittest.TestCase):
    def test_exact_receiver_and_no_percentage_in_result(self):
        d=iface();self.assertTrue(rx.matches(d))
        with patch.object(b,'read_voltage',return_value=(3975,b'reply')):
            r=rx.query(Mock(),d)
        self.assertIsNone(r.level);self.assertEqual(r.label,'3.98V');self.assertEqual(r.voltage_mv,3975)
        for attr,value in (('pid',0x552),('usage_page',0xc),('usage',2),('input_len',65)):
            with patch.object(d,attr,value):self.assertFalse(b.matches(d))

    def test_crc_and_command_allowlist(self):
        for group,body in ((6,b'\1\0\x31'),(14,b'\2\xe1\1'),(14,b'\2\xe1\0')):
            frame=b.packet(group,1,body);zeroed=bytearray(frame);zeroed[3:5]=b'\0\0'
            self.assertEqual(int.from_bytes(frame[3:5],'little'),binascii.crc_hqx(zeroed,0))
            self.assertEqual(len(frame),64)
        with self.assertRaises(ValueError):b.packet(6,1,b'\1\0\x32')

    def test_response_correlation_and_malformed_lengths(self):
        self.assertIsNotNone(b.response(reply(),6,1))
        self.assertIsNone(b.response(reply(),6,2))
        self.assertIsNone(b.response(reply(),14,1))
        for offset,value in ((0,3),(1,63),(10,63),(14,1)):
            r=bytearray(reply());r[offset]=value
            self.assertIsNone(b.response(r,6,1))
        notification=bytearray(13);notification[:2]=b'\x50\x49';notification[8]=3
        notification[10:13]=b'\x11\x82\0'
        f=bytes(notification)+reply()[2:17]
        combined=bytes((2,len(f)))+f+bytes(62-len(f))
        self.assertIsNotNone(b.response(combined,6,1))

    def test_control_ack_is_not_interrupt_battery(self):
        dev=Mock();dev.get_input_report.side_effect=[b'\2F'.ljust(64,b'\0'),b'\2O'.ljust(64,b'\0')]
        dev.read_input.side_effect=[reply(seq=2),reply(seq=1)]
        with patch.object(b,'_sequence',itertools.count(0)),patch.object(b.time,'sleep'):
            self.assertIsNotNone(b.exchange(dev,6,b'\1\0\x31'))
        self.assertEqual(dev.write_output.call_count,2)
        self.assertEqual(dev.get_input_report.call_args.args,(2,64))
        dev.set_feature_report.assert_not_called()

    def test_exit_even_if_enter_or_battery_fails(self):
        for responses in ([None,b'exited'],[OSError('enter'),b'exited'],
                          [b'entered',OSError('read'),b'exited']):
            with patch.object(k,'report_ids',return_value={2}),patch.object(b,'exchange',side_effect=responses) as exchange:
                try:b.read_voltage(Mock(),iface())
                except OSError:pass
                self.assertEqual(exchange.call_args.args[1:],(14,b'\2\xe1\0'))
        with patch.object(k,'report_ids',return_value={2}),patch.object(b,'exchange',side_effect=[None,None]),self.assertRaises(OSError):
            b.read_voltage(Mock(),iface())

    def test_voltage_bounds_and_cleanup(self):
        for mv in (2400,3975,4600):
            with patch.object(k,'report_ids',return_value={2}),patch.object(b,'exchange',side_effect=[b'entered',reply(mv=mv)[2:17],b'exited']):
                r=b.read_voltage(Mock(),iface())
            self.assertEqual(r is not None,mv==3975)
