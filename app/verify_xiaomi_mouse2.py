"""Public-driver protocol fixtures; not claims of physical write validation."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import xiaomi_mouse2 as xm
import mouse_settings as ms
import mouse_controls as mc
import battery as b


def iface(pid=0x5111):
    return SimpleNamespace(path='xiaomi-fixture',vid=0x2717,pid=pid,usage_page=0xFFEF,usage=0,
        input_len=64,output_len=64,feature_len=64,vid_pid=f'2717:{pid:04X}',product='Mi Gaming Mouse 2 Receiver',manufacturer='Xiaomi')


def config():
    rows=tuple(bytes((i,))+v.to_bytes(2,'little')+bytes((255,0,i))+b'\0'+v.to_bytes(2,'little') for i,v in enumerate((400,800,1600,3200,6400)))
    return xm.Config(1,5,1,rows,bytes((1,7,8,0,0,3,0)))


class Device:
    def __init__(self,d):self.d=d;self.config=config();self.writes=[];self.last=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def set_feature_report(self,raw):
        assert len(raw)==64 and raw[:2]==bytes((5,xm.target(self.d)))
        self.last=raw
        cmd=raw[2]
        if cmd in xm.SETS:
            self.writes.append(raw)
            if cmd==0x2E:
                rows=list(self.config.rows);rows[raw[5]]=raw[5:14]
                self.config=replace(self.config,rows=tuple(rows))
            elif cmd==0x2C:self.config=replace(self.config,active=raw[6])
            else:self.config=replace(self.config,rate_data=raw[4:11])
    def get_feature_report(self,rid,length):
        assert (rid,length)==(5,64)
        cmd=self.last[2];c=self.config
        data={0x16:bytes((2,44,30)),0x80:bytes((c.profile,)),0x2B:bytes((c.profile,c.count,c.active)),
              0x25:c.rate_data,0x2C:bytes((c.profile,c.count,c.active)),0x26:c.rate_data}
        if cmd in (0x2D,0x2E):payload=bytes((c.profile,))+c.rows[self.last[5]]
        else:payload=data[cmd]
        return bytes((5,xm.target(self.d),cmd,len(payload)))+payload+bytes(60-len(payload))


class XiaomiTests(unittest.TestCase):
    def setUp(self):
        self.d=iface();self.dev=Device(self.d)
        self.mouse=ms.Mouse(self.d.path,xm.NAME,self.d.vid_pid,'xiaomi2',(self.d.vid,self.d.pid),interface=self.d)
        self.patches=[patch.object(xm.hw,'enumerate_hid',return_value=[self.d]),
                      patch.object(xm.hw,'HidDevice',return_value=self.dev),patch.object(xm.time,'sleep')]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_exact_route_does_not_create_phantom_keyboard(self):
        wrong=SimpleNamespace(**(vars(self.d)|dict(usage_page=1,usage=6,feature_len=0)))
        self.assertFalse(xm.matches(wrong));self.assertFalse(b._is_candidate_interface(wrong,[]))
        with patch.object(ms.hw,'enumerate_hid',return_value=[wrong,self.d]):found=ms.discover()
        self.assertEqual(len(found),1);self.assertEqual(found[0].protocol,'xiaomi2')
        self.assertFalse(xm.claims(iface(0x50FC)))
        self.assertEqual(xm.packet(iface(0x5110),0x16)[:4],b'\x05\0\x16\0')

    def test_read_battery_and_full_configuration(self):
        result=b._read_battery_unlocked(self.d)[0]
        self.assertEqual((result.level,result.status),(44,'charging'))
        state=ms.read(self.mouse)
        self.assertEqual(state.stages,[(v,v) for v in (400,800,1600,3200,6400)])
        self.assertEqual(state.active,1)
        self.assertEqual(mc.read(self.mouse).rate,1000)
        self.assertFalse(self.dev.writes)

    def test_log_generic_reply_is_not_battery_or_configuration(self):
        raw=bytes.fromhex('05512c01').ljust(64,b'\0')
        self.assertIsNone(xm.decode_reply(raw,self.d,0x16))
        self.assertIsNone(xm.decode_reply(raw,self.d,0x2B))
        with self.assertRaises(ValueError):xm.decode_reply(raw,self.d,0x2C)

    def test_dpi_write_preserves_color_rate_and_other_stages(self):
        before=ms.read(self.mouse)
        after=ms.apply(before,1,1200,False)
        self.assertEqual(after.stages[1],(1200,1200))
        self.assertEqual(after.colors,before.colors)
        self.assertEqual(after.protocol_data.rate_data,before.protocol_data.rate_data)
        self.assertEqual(len(self.dev.writes),1)
        self.assertEqual(self.dev.writes[0][:8],bytes.fromhex('05012e070101b004'))

    def test_stage_switch_preserves_rows(self):
        old=ms.read(self.mouse);new=ms.apply(old,4,None,True)
        self.assertEqual(new.active,4);self.assertEqual(new.protocol_data.rows,old.protocol_data.rows)
        self.assertEqual(self.dev.writes[0][:7],bytes.fromhex('05012c03010504'))

    def test_color_write_keeps_independent_axes(self):
        rows=list(self.dev.config.rows);row=bytearray(rows[1]);row[6]=1;row[7:9]=(900).to_bytes(2,'little');rows[1]=bytes(row)
        self.dev.config=replace(self.dev.config,rows=tuple(rows))
        old=ms.read(self.mouse)
        new=xm.apply(old,stage=1,rgb=(12,34,56))
        self.assertEqual(new.stages,old.stages)
        self.assertEqual(new.colors[1],bytes((12,34,56)))
        with self.assertRaises(ValueError):ms.apply(new,1,1200,False)
        self.assertEqual(len(self.dev.writes),1)

    def test_rate_preserves_performance_mode_and_motion_sync(self):
        old=mc.read(self.mouse);new=mc.apply(old,rate=4000)
        self.assertEqual(new.rate,4000)
        self.assertEqual(new.dpi.protocol_data.rows,old.dpi.protocol_data.rows)
        self.assertEqual(self.dev.writes[0][:11],bytes.fromhex('0501260701072000000300'))
        with self.assertRaises(ValueError):mc.apply(new,rate=8000)
        self.assertEqual(len(self.dev.writes),1)

    def test_invalid_and_stale_values_fail_before_write(self):
        old=ms.read(self.mouse)
        for value in (True,99,40001,'800'):
            with self.assertRaises(ValueError):ms.apply(old,0,value,False)
        self.dev.config=replace(self.dev.config,active=3)
        with self.assertRaises(ValueError):ms.apply(old,0,800,False)
        self.assertFalse(self.dev.writes)

    def test_timeout_never_resends_setting(self):
        old=ms.read(self.mouse)
        original=self.dev.get_feature_report
        def reply(rid,length):
            if self.dev.last[2] in xm.SETS:return bytes(64)
            return original(rid,length)
        with patch.object(self.dev,'get_feature_report',side_effect=reply):
            with self.assertRaisesRegex(RuntimeError,'未自动重试'):ms.apply(old,0,800,False)
        self.assertEqual(len(self.dev.writes),1)

    def test_readback_mismatch_is_not_success(self):
        old=ms.read(self.mouse)
        with patch.object(xm,'read',return_value=old):
            with self.assertRaisesRegex(RuntimeError,'读回不一致'):ms.apply(old,0,800,False)

    def test_bad_target_length_profile_or_row_rejected(self):
        original=self.dev.get_feature_report
        for offset,value in ((0,0),(1,0x21),(3,0),(4,2),(5,4),(11,2)):
            def corrupt(rid,length):
                raw=bytearray(original(rid,length))
                if self.dev.last[2]==0x2D:raw[offset]=value
                return raw
            with patch.object(self.dev,'get_feature_report',side_effect=corrupt):
                with self.assertRaises((ValueError,TimeoutError)):ms.read(self.mouse)

    def test_ui_colors_and_mode_limited_rates_do_not_write_on_render(self):
        import tkinter as tk
        import ui_mouse_pages as pages
        root=tk.Tk();root.withdraw()
        try:
            page=pages.MouseSettingsPage(root);state=mc.read(self.mouse)
            page.render(state.dpi);page.render_controls(state)
            def walk(w):
                for child in w.winfo_children():yield child;yield from walk(child)
            combos=[w for w in walk(page.extra) if isinstance(w,pages.ttk.Combobox)]
            self.assertEqual(tuple(map(int,combos[0]['values'])),(125,250,500,1000,2000,4000))
            self.assertEqual(len([w for w in walk(page.extra) if isinstance(w,pages.ColorPicker)]),5)
            self.assertFalse(self.dev.writes)
            page.set_busy(True)
            self.assertTrue(combos[0].instate(['disabled']))
        finally:root.destroy()


if __name__=='__main__':unittest.main()
