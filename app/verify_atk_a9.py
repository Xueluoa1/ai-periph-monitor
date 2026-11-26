"""A9 receiver identity timeout and the HUB's two read-only fallback queries."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mouse_settings as ms


class A9ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(vid=0x373B, pid=0x1279, usage_page=0xFF02,
                                     usage=2, input_len=17, output_len=17)
        self.outgoing = []
        self.log = Mock()

    def identify(self, status, info, second_id=None):
        packets = [None, status, info, second_id]

        def exchange(device, iface, packet, accept):
            self.outgoing.append(packet)
            response = packets[len(self.outgoing)-1]
            if response is None or not accept(response):
                raise TimeoutError
            return response

        with patch.object(ms, 'exchange', side_effect=exchange):
            return ms.atk_identity(Mock(), self.iface, self.log)

    def test_online_connection_info_can_identify_a9(self):
        online = ms.atk_frame(3, (0,0,0,4,1,0xAD,0xCD,0xEF))
        info = ms.atk_frame(1, (0,0,0,8,4,3,2,1,1,53,5,0))
        with patch.object(ms, 'token_bytes', return_value=bytes((1,2,3,4))):
            identity = self.identify(online, info)
        self.assertEqual(identity, (1,53))
        self.assertEqual(ms.atk_model(identity,self.iface)[1], 'master')
        self.assertEqual(self.outgoing, [ms.atk_frame(0x10), ms.atk_frame(3),
                                        ms.atk_frame(1,(0,0,0,8,1,2,3,4))])
        self.log.assert_any_call('receiver_online', {'online':True})

    def test_offline_status_stops_before_requesting_configuration(self):
        with self.assertRaisesRegex(OSError,'配对鼠标离线'):
            self.identify(ms.atk_frame(3,(0,0,0,1,0)),None)
        self.assertEqual([p[1] for p in self.outgoing], [0x10,3])
        self.log.assert_any_call('receiver_online', {'online':False})

    def test_timeout_is_not_offline_or_a_guessed_identity(self):
        with self.assertRaisesRegex(TimeoutError,'均未获有效回复'):
            self.identify(None,None)
        self.log.assert_any_call('receiver_online', {'online':None})

    def test_echo_invalid_checksum_and_wired_type_do_not_enable_a9(self):
        online=ms.atk_frame(3,(0,0,0,1,1))
        info=ms.atk_frame(1,(0,0,0,8,1,2,3,4,1,39,5,0))
        invalid=info[:-1]+bytes([info[-1]^1])
        for reply in (ms.atk_frame(1,(0,0,0,8,1,2,3,4)),invalid):
            self.outgoing.clear()
            with self.assertRaisesRegex(TimeoutError,'在线，但未返回配对型号'):
                self.identify(online,reply)
        self.outgoing.clear()
        wired=ms.atk_frame(1,(0,0,0,8,1,2,3,4,1,39,3,0))
        with self.assertRaisesRegex(ValueError,'未确认是 2.4G'):
            self.identify(online,wired)

    def test_unrelated_paired_identity_remains_read_only(self):
        for mid in (39,99):
            self.outgoing.clear()
            info = ms.atk_frame(1,(0,0,0,8,1,2,3,4,2,mid,5,0))
            with self.assertRaisesRegex(ValueError,'未确认是 A9'):
                self.identify(None,info)


if __name__=='__main__':
    unittest.main()
