"""Official protocol shape fixtures and end-to-end simulated .97 hardware."""
from dataclasses import replace
import tkinter as tk
import unittest
from unittest.mock import patch

import am_controls as ac
import am_dpi
import mouse_controls as mc
import mouse_settings as ms
from verify_am_dpi import FakeDevice as DpiDevice, iface


def responses(command,payload,target=128):
    frame=b'\x05\x5b'+(len(payload)+2).to_bytes(2,'little')+command.to_bytes(2,'little')+payload
    return [(bytes((20,len(frame[i:i+59]),target))+frame[i:i+59]).ljust(62,b'\0') for i in range(0,len(frame),59)]


class Device(DpiDevice):
    def __init__(self):
        super().__init__()
        self.buffer=bytearray();self.replies=[];self.commands=[]
        self.rate=1000;self.settings=b'\x01\x00\x06\x55'
        self.pattern=bytes.fromhex('01030010ff6e00')
        self.dongle=bytes.fromhex('010504e36004e36004e3605cb3')
        self.corrupt=False;self.fail=None;self.bad_nv=False

    def set_feature_report(self,raw):
        self.buffer.extend(raw[3:3+raw[1]])
        if len(self.buffer)<6:return
        size=int.from_bytes(self.buffer[2:4],'little')+4
        if len(self.buffer)<size:return
        self.assert_frame(raw,size)
        frame=bytes(self.buffer);self.buffer.clear()
        cmd=int.from_bytes(frame[4:6],'little');data=frame[6:];target=raw[2]
        self.commands.append((cmd,data,target));self.replies=[]
        if cmd in (am_dpi.GET_DPI,am_dpi.GET_PROFILE):
            super().set_feature_report(raw);self.replies=[self.reply];return
        if cmd==ac.GET_RATE:payload=b'\0'+self.rate.to_bytes(2,'little')
        elif cmd==ac.SET_RATE:
            self.rate=int.from_bytes(data,'little');payload=b'\0'
            if self.corrupt:self.config=replace(self.config,active=0)
        elif cmd==ac.READ_NV:
            address=int.from_bytes(data[:2],'little')
            nv=self.settings if address==ac.LIGHT_NV else self.pattern
            payload=b'\0'+data[:2]+len(nv).to_bytes(2,'little')+nv
            if self.bad_nv:payload=b'\x01'
        elif cmd==ac.GET_DONGLE:
            assert target==0;payload=self.dongle
        elif cmd==ac.SET_DONGLE:
            assert target==0;self.dongle=bytes(data);payload=None
        elif cmd==ac.SET_LIGHT:self.settings=data+self.settings[3:];payload=None
        elif cmd==ac.SAVE_RGB:self.pattern=bytes(data[2:]);payload=b'\0' if target==0 else None
        elif cmd==ac.REALTIME:payload=None
        else:raise AssertionError(f'unexpected {cmd:04X}')
        if payload is not None:self.replies=responses(cmd,payload,target)
        if cmd==self.fail:raise TimeoutError('lost completion')

    def assert_frame(self,raw,size):
        assert len(self.buffer)==size
        assert raw[0]==20

    def get_feature_report(self,*args):
        return self.replies.pop(0) if self.replies else bytes(62)


class AmControlsTests(unittest.TestCase):
    def setUp(self):
        self.iface=iface();self.device=Device()
        self.mouse=ms.Mouse(self.iface.path,'AM .97',self.iface.vid_pid,'am35',(self.iface.vid,self.iface.pid),interface=self.iface)
        for p in (patch.object(ms.hw,'enumerate_hid',return_value=[self.iface]),
                  patch.object(ms.hw,'HidDevice',return_value=self.device),
                  patch.object(am_dpi.am,'feature_report_ids',return_value={20}),
                  patch.object(ac.time,'sleep')):
            p.start();self.addCleanup(p.stop)

    def writes(self):
        return [c for c in self.device.commands if c[0] in (ac.SET_RATE,ac.SET_LIGHT,ac.SAVE_RGB,ac.REALTIME,ac.SET_DONGLE)]

    def test_read_rate_mouse_and_local_dongle_without_settings_writes(self):
        state=mc.read(self.mouse)
        self.assertEqual(state.rate,1000)
        self.assertEqual(state.am_state.settings,self.device.settings)
        self.assertEqual(state.am_state.dongle,self.device.dongle)
        self.assertEqual(self.writes(),[])
        for cmd,data,target in self.device.commands:
            self.assertEqual(target,0 if cmd==ac.GET_DONGLE else 128)

    def test_all_seven_rates_and_exact_request(self):
        self.assertEqual(ac.packets(ac.SET_RATE,b'\x40\x1f',128,62)[0][:11].hex(),'140880055a04001030401f')
        state=mc.read(self.mouse)
        for rate in ac.RATES:
            state=mc.apply(state,rate=rate)
            self.assertEqual(state.rate,rate)
            self.assertEqual(state.am_state.dongle,self.device.dongle)
        self.assertTrue(all(c[0]==ac.SET_RATE for c in self.writes()))

    def test_power_switch_preserves_pattern_and_other_fields(self):
        state=mc.read(self.mouse);pattern=state.am_state.pattern
        after=mc.apply(state,am_light=ac.MouseLight(False,0,6))
        self.assertEqual(after.am_state.settings,b'\0\0\x06\x55')
        self.assertEqual(after.am_state.pattern,pattern)
        self.assertEqual(self.writes(),[(ac.SET_LIGHT,b'\0\0\x06',128)])

    def test_solid_color_saves_then_updates_preview(self):
        state=mc.read(self.mouse)
        after=mc.apply(state,am_light=ac.MouseLight(True,0,6,((1,2,3),)))
        self.assertEqual(after.am_state.pattern,bytes.fromhex('01030010010203'))
        self.assertEqual(self.writes(),[(ac.SAVE_RGB,bytes.fromhex('006201030010010203'),128),
                                       (ac.REALTIME,bytes.fromhex('80001001010203'),128)])

    def test_breathing_and_cycle_fragmented_patterns_read_back(self):
        state=mc.read(self.mouse)
        for light in [ac.MouseLight(True,1,1,((255,100,0),)),ac.MouseLight(True,2,1,((255,0,0),(0,0,255)))]:
            state=mc.apply(state,am_light=light)
            self.assertEqual(state.am_state.pattern,ac.make_pattern(light))
            self.assertEqual(len(state.am_state.pattern),754)

    def test_dongle_brightness_preserves_raw_hues_and_sat(self):
        self.assertEqual((ac.DONGLE_MODES[7],ac.DONGLE_MODES[3]),('流动','弧光'))
        state=mc.read(self.mouse);raw=state.am_state.dongle[:-1]+b'\x64'
        after=mc.apply(state,am_dongle=raw)
        self.assertEqual(after.am_state.dongle,raw)
        self.assertEqual(self.writes(),[(ac.SET_DONGLE,raw,0)])

    def test_usb_does_not_read_or_write_receiver_light(self):
        self.iface.pid=0x0880;self.mouse.identity=(0x0E8D,0x0880)
        state=mc.read(self.mouse)
        self.assertIsNone(state.am_state.dongle)
        mc.apply(state,am_light=ac.MouseLight(True,0,6,((11,12,13),)))
        self.assertTrue(all(target==0 for _,_,target in self.device.commands))
        self.assertFalse(any(c[0] in (ac.GET_DONGLE,ac.SET_DONGLE) for c in self.device.commands))

    def test_optional_lighting_failure_keeps_rate_available(self):
        self.device.bad_nv=True
        state=mc.read(self.mouse)
        self.assertIsNone(state.am_state.settings)
        self.assertEqual(len(state.am_state.errors),2)
        self.assertEqual(mc.apply(state,rate=2000).rate,2000)

    def test_invalid_and_unknown_inputs_do_not_touch_hardware(self):
        state=mc.read(self.mouse);self.device.commands.clear()
        for changes in [dict(rate=16000),dict(rate=True),dict(am_light=ac.MouseLight(True,4,6)),dict(am_light=ac.MouseLight(True,1,6)),dict(am_dongle=b'\0'*13)]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):mc.apply(state,**changes)
        self.assertEqual(self.device.commands,[])
        for cmd in (0x30CE,0x30CB,0x1001):
            with self.assertRaises(ValueError):ac.packets(cmd,b'',0,62)

    def test_stale_snapshot_prevents_write(self):
        state=mc.read(self.mouse);self.device.rate=500
        with self.assertRaisesRegex(ValueError,'已变化'):mc.apply(state,rate=2000)
        self.assertEqual(self.writes(),[])

    def test_changed_dpi_during_write_fails_verification(self):
        state=mc.read(self.mouse);self.device.corrupt=True
        with self.assertRaisesRegex(RuntimeError,'不一致'):mc.apply(state,rate=2000)

    def test_write_failure_reads_back_without_retry(self):
        state=mc.read(self.mouse);self.device.fail=ac.SET_RATE
        with self.assertRaisesRegex(RuntimeError,'未自动重试'):mc.apply(state,rate=4000)
        self.assertEqual(len(self.writes()),1)
        self.assertEqual(self.device.commands[-1][0],am_dpi.GET_PROFILE)

    def test_noop_sends_no_writes(self):
        state=mc.read(self.mouse)
        mc.apply(state,rate=1000,am_light=ac.MouseLight(True,0,6),am_dongle=state.am_state.dongle)
        self.assertEqual(self.writes(),[])

    def test_response_wrong_target_nv_and_repeated_fragments(self):
        parser=ac.Response(128,ac.READ_NV,ac.PATTERN_NV)
        raw=b'\0\0\x62'+(754).to_bytes(2,'little')+b'\x01\xee\x02\x10'+b'\xff'*750
        self.assertIsNone(parser.feed(responses(ac.READ_NV,b'\0\x0a\x60\x03\0\x01\0\x06')[0]))
        self.assertIsNone(parser.feed(responses(ac.READ_NV,raw,0)[0]))
        result=None
        for part in responses(ac.READ_NV,raw):result=parser.feed(part)
        self.assertEqual(result,raw)
        self.assertEqual(ac.Response(0,ac.GET_DONGLE).feed(responses(ac.GET_DONGLE,self.device.dongle,0)[0]),self.device.dongle)

    def test_invalid_nv_length_and_unknown_layout_disabled(self):
        with patch.object(ac,'exchange',return_value=b'\0\x0a\x60\x04\0\x01\0\x06'):
            with self.assertRaises(ValueError):ac.read_nv(self.device,self.iface,ac.LIGHT_NV)
        for raw in (b'\0',b'\x02\x03\0\x10\xff\0\0'):
            with self.assertRaises(ValueError):ac.validate_pattern(raw)

    def test_ui_renders_am_specific_controls_and_preserves_dongle_bytes(self):
        import ui_mouse_pages as pages
        state=mc.read(self.mouse)
        root=tk.Tk();root.withdraw()
        try:
            page=pages.MouseSettingsPage(root);page.render(state.dpi);page.render_controls(state)
            editor=page.am_editor
            with patch.object(page,'apply_controls') as apply:
                editor.apply_dongle();self.assertEqual(apply.call_args.kwargs['am_dongle'],state.am_state.dongle)
                apply.reset_mock();editor.mouse_fields['speed'].set('99');editor.apply_mouse();apply.assert_not_called()
                editor.mouse_fields['speed'].set('6');editor.apply_mouse();apply.assert_called_once()
            page.set_busy(True)
            self.assertTrue(all(w.instate(['disabled']) for row in page.extra.winfo_children() for w in row.winfo_children() if isinstance(w,pages.ttk.Entry)))
        finally:root.destroy()


if __name__=='__main__':unittest.main()
