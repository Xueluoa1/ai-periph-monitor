"""Synthetic protocol/guard tests, never hardware captures or device writes."""
from dataclasses import replace
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import gravastar_k1 as k
import battery
import mouse_settings as ms


def iface(**kw):
    return SimpleNamespace(**(dict(path='k1-test',vid=0x3554,pid=0xFA09,usage_page=0xFF02,
        usage=2,input_len=20,output_len=20,feature_len=0,vid_pid='3554:FA09',product='Receiver',manufacturer='')|kw))


def snapshot():
    raw=bytearray(128);raw[10]=2;raw[-2:]=b'\x5a\xa5'
    for i in range(18):raw[0x3A+i*2:0x3C+i*2]=b'\x02\x40'
    return k.State(ms.Mouse('k1-test',k.NAME,'3554:FA09','k1',k.IDENTITY),
        bytes.fromhex('03 00 00 00 01 B4'),bytes(raw),bytes((i%256 for i in range(420))))


def replies(command,data):
    count=(len(data)+13)//14
    return [k.frame(command,data[i*14:i*14+14],count,i) for i in range(count)]


class FakeDevice:
    write_access=True
    def __init__(self,queue=()):self.queue=list(queue);self.sent=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def drain(self):pass
    def write_output(self,packet,**kwargs):self.sent.append(packet)
    def read_input(self,**kwargs):return self.queue.pop(0) if self.queue else b''


class K1Tests(unittest.TestCase):
    def test_exact_interface(self):
        self.assertTrue(k.matches(iface()))
        for change in ({'pid':0xF58A},{'usage_page':1},{'usage':1},{'output_len':17},{'input_len':64}):
            self.assertFalse(k.matches(iface(**change)))

    def test_report_guard_precedes_commands(self):
        dev=FakeDevice()
        with patch.object(k,'report_ids',return_value={8}),self.assertRaises(ValueError):k.check_interface(dev,iface())
        self.assertEqual(dev.sent,[])

    def test_known_read_frame(self):
        self.assertEqual(k.frame(0x4A).hex(),'134a01000000000000000000000000000000005e')

    def test_read_reassembles_out_of_order_and_duplicates(self):
        data=snapshot().colors;r=replies(0x49,data)
        dev=FakeDevice([k.frame(0x0A,b'\x02\x01'),r[2],r[2],*r[:2],*r[3:]])
        self.assertEqual(k.query(dev,0x49,420),data)
        self.assertEqual([p[1] for p in dev.sent],[0x49])

    def test_corrupt_checksum(self):
        r=bytearray(k.frame(0x4A,b'\x50\x01'));r[-1]^=1
        with self.assertRaisesRegex(ValueError,'校验'):k.receive(FakeDevice([r]),0x4A,2)

    def test_bad_fragment_lengths_counts_and_flags(self):
        for header in ((2,0,2),(1,1,2),(1,0,15),(129,0,2),(1,0,0x12)):
            r=bytearray(k.frame(0x4A,b'\x50\x01'));r[2:5]=bytes(header);r[-1]=sum(r[:-1])&255
            with self.subTest(header=header),self.assertRaises(ValueError):k.receive(FakeDevice([r]),0x4A,2)

    def test_conflicting_duplicate(self):
        r=replies(0x44,snapshot().raw);bad=bytearray(r[0]);bad[5]^=1;bad[-1]=sum(bad[:-1])&255
        with self.assertRaisesRegex(ValueError,'重复'):k.receive(FakeDevice([r[0],bad]),0x44,128)

    def test_incomplete_response_times_out(self):
        with patch.object(k.time,'monotonic',side_effect=[0,0,4]),self.assertRaises(TimeoutError):
            k.receive(FakeDevice(replies(0x44,snapshot().raw)[:1]),0x44,128)

    def test_wrong_model_or_offline(self):
        for sequence in ([b'\0'],[b'\1',bytes.fromhex('03 00 00 00 00 D6')]):
            with patch.object(k,'query',side_effect=sequence),self.assertRaises((OSError,ValueError)):k.identify(FakeDevice())

    def test_battery_valid_and_empty(self):
        self.assertEqual(k.battery_value(bytes((81,0x10))),(81,True))
        self.assertEqual(k.battery_value(bytes((20,0x11))),(20,False))
        self.assertEqual(k.battery_value(bytes((0,1))),(0,False))
        for data in (b'\0\0',b'\xff\x01',b'\x14\xff',b'\x14'):
            with self.assertRaises(ValueError):k.battery_value(data)

    def test_battery_route_is_terminal(self):
        failed=battery.BatteryResult(ok=False)
        with patch.object(k,'battery_read',return_value=failed) as reader,patch.object(battery.wireless,'read') as other:
            self.assertEqual(battery._read_battery_unlocked(iface(),0),[failed]);other.assert_not_called();reader.assert_called_once()
        self.assertFalse(battery._is_candidate_interface(iface(output_len=17),[]))

    def test_discovery_does_not_claim_keyboard_as_mouse(self):
        with patch.object(ms.hw,'enumerate_hid',return_value=[iface(),iface(path='consumer',usage_page=12,usage=1)]):
            result=ms.discover()
        self.assertEqual([(d.protocol,d.identity) for d in result],[('k1',k.IDENTITY)])

    def test_failed_battery_not_zero(self):
        with patch.object(k.hw,'HidDevice',return_value=FakeDevice()),patch.object(k,'check_interface'),patch.object(k,'identify',return_value=snapshot().signature),patch.object(k,'query',return_value=b'\0\0'):
            r=k.battery_read(iface(),battery.BatteryResult)
        self.assertFalse(r.ok);self.assertIsNone(r.level);self.assertEqual(r.extra['model_name'],k.NAME)

    def test_unrelated_config_and_palette_preserved(self):
        s=snapshot();raw,colors=k.changes(s,2,3,1,(11,22,33))
        self.assertEqual({i for i,(a,b) in enumerate(zip(raw,s.raw)) if a!=b},{0x3C,0x3D})
        self.assertEqual(colors[:42],s.colors[:42]);self.assertEqual(colors[42:45],b'\x0b\x16\x21');self.assertEqual(colors[45:],s.colors[45:])

    def test_reject_invalid_or_unreadable_config(self):
        s=snapshot()
        for raw in (s.raw[:127],bytes(128),s.raw[:10]+b'\xff'+s.raw[11:]):
            with self.assertRaises(ValueError):k.changes(replace(s,raw=raw),1,2,2)
        for args in ((0,2,2),(21,2,2),(2,5,2),(2,2,-1),(2,2.5,1)):
            with self.assertRaises(ValueError):k.changes(s,*args)

    def test_color_restrictions_preserve_nonzero_flag(self):
        s=snapshot();raw=bytearray(s.raw);raw[0x3D]|=7;s=replace(s,raw=bytes(raw))
        updated,_=k.changes(s,2,3,2);self.assertEqual(updated[0x3D]&15,7)
        with self.assertRaises(ValueError):k.changes(s,2,3,2,(1,2,3))
        with self.assertRaises(ValueError):k.changes(replace(snapshot(),colors=b''),2,3,2,(1,2,3))
        with self.assertRaises(ValueError):k.changes(snapshot(),3,3,2,(1,2,3))

    def stack(self):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(k,'interface',return_value=iface()))
        stack.enter_context(patch.object(k.hw,'HidDevice',return_value=FakeDevice()))
        stack.enter_context(patch.object(k,'check_interface'))
        stack.enter_context(patch.object(k,'identify',return_value=snapshot().signature))
        return stack

    def test_stale_snapshot_blocks_all_writes(self):
        s=snapshot();stack=self.stack()
        stack.enter_context(patch.object(k,'read_on',return_value=replace(s,colors=bytes(420))))
        write=stack.enter_context(patch.object(k,'write_blob'))
        with self.assertRaisesRegex(ValueError,'已变化'):k.apply(s,2,3,2)
        write.assert_not_called()

    def test_setting_readback_and_preservation(self):
        s=snapshot();target,colors=k.changes(s,2,3,1);done=replace(s,raw=target)
        stack=self.stack();stack.enter_context(patch.object(k,'read_on',side_effect=[s,done]))
        write=stack.enter_context(patch.object(k,'write_blob'))
        self.assertEqual(k.apply(s,2,3,1),done);self.assertEqual(write.call_args.args[1:],(4,target))

    def test_noop_never_writes(self):
        s=snapshot();stack=self.stack();stack.enter_context(patch.object(k,'read_on',return_value=s))
        write=stack.enter_context(patch.object(k,'write_blob'))
        k.apply(s,2,4,2);write.assert_not_called()

    def test_timeout_does_not_retry_or_claim_success(self):
        s=snapshot();stack=self.stack();stack.enter_context(patch.object(k,'read_on',return_value=s))
        write=stack.enter_context(patch.object(k,'write_blob',side_effect=TimeoutError('timeout')))
        with self.assertRaisesRegex(OSError,'部分生效'):k.apply(s,2,3,1)
        self.assertEqual(write.call_count,1)

    def test_readback_mismatch_not_success(self):
        s=snapshot();stack=self.stack();stack.enter_context(patch.object(k,'read_on',return_value=s))
        stack.enter_context(patch.object(k,'write_blob'))
        with self.assertRaisesRegex(OSError,'读回不一致'):k.apply(s,2,3,1)

    def test_write_packet_order_and_checksum(self):
        dev=FakeDevice();data=snapshot().raw
        with patch.object(k,'receive') as receive,patch.object(k.time,'sleep'):
            k.write_blob(dev,4,data)
        self.assertEqual(len(dev.sent),10)
        self.assertEqual(b''.join(p[5:5+(p[4]&15)] for p in dev.sent),data)
        self.assertTrue(all(p[2]==10 and p[3]==i and (sum(p[:19])&255)==p[19] for i,p in enumerate(dev.sent)))
        self.assertEqual([c.kwargs['ack_index'] for c in receive.call_args_list],list(range(10)))

    def test_partial_color_failure_stops_configuration_write(self):
        s=snapshot();stack=self.stack();stack.enter_context(patch.object(k,'read_on',return_value=s))
        write=stack.enter_context(patch.object(k,'write_blob'))
        stack.enter_context(patch.object(k,'query',return_value=s.colors))
        with self.assertRaisesRegex(OSError,'颜色表读回不一致'):k.apply(s,2,3,1,(11,22,33))
        self.assertEqual([c.args[1] for c in write.call_args_list],[9])

    def test_full_read_color_failure_still_has_config(self):
        s=snapshot()
        with patch.object(k,'identify',return_value=s.signature),patch.object(k,'query',side_effect=[s.raw,TimeoutError('color unavailable')]):
            state=k.read_on(FakeDevice(),s.mouse)
        self.assertEqual(state.raw,s.raw);self.assertEqual(state.colors,b'');self.assertIn('unavailable',state.color_error)

    def test_repairing_during_read_is_rejected(self):
        s=snapshot()
        with patch.object(k,'identify',side_effect=[s.signature,bytes.fromhex('03 00 00 00 03 F5')]),patch.object(k,'query',side_effect=[s.raw,s.colors]),self.assertRaisesRegex(ValueError,'配对型号'):
            k.read_on(FakeDevice(),s.mouse)


if __name__=='__main__':unittest.main()
