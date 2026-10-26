"""Protocol fixtures and simulated devices. No physical settings are changed."""
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import control_models as models
import peripheral_controls as pc
import razer_controls as rz
import vendor_controls as vc


def iface(vid,pid,**overrides):
    values=dict(path='test#mi_00',vid=vid,pid=pid,usage_page=0xFFFF,usage=0,
                feature_len=65,input_len=0,output_len=0,vid_pid=f'{vid:04X}:{pid:04X}')
    values.update(overrides)
    return SimpleNamespace(**values)


class PageDevice:
    def __init__(self):
        self.profile=1;self.code=0x10;self.light=0;self.writes=[];self.requests=[];self.ignore=False
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def set_feature_report(self,raw):
        self.requests.append(raw);self.request=raw
        target,length,page,cmd=raw[3:7];args=raw[7:]
        if cmd<0x80:
            self.writes.append(raw)
            if not self.ignore:
                if page==1:self.code=args[1]
                elif cmd==4:self.light=args[1]
                else:self.light=args[2]
    def get_feature_report(self,rid,length):
        target,n,page,cmd=self.request[3:7]
        if cmd==0x85:p=bytes([self.profile])
        elif cmd==0x8B:p=bytes.fromhex('00000000a883')
        elif page==1:p=bytes([self.profile,self.code])
        elif cmd in (0x84,4):p=bytes([self.profile,self.light])
        else:p=bytes([1,0,self.light,0,0,0])
        return bytes([0,0xA1,0,target,len(p),page,cmd])+p+bytes(58-len(p))


class MchoseDevice(PageDevice):
    def __init__(self):
        super().__init__();self.model=0x4021
        self.config=bytearray(bytes.fromhex('001220aa4006200320034006800c00190680050a').ljust(63,b'\0'))
        self.config[16]=6;self.config[18]=5;self.config[49]=0xF1
    def set_feature_report(self,raw):
        self.request=raw
        if raw[1]==(0x57^255):
            self.writes.append(raw);self.config[:]=bytes(v^255 for v in raw[2:])
    def get_feature_report(self,rid,length):
        cmd=self.request[1]^255
        if cmd==6:p=bytes.fromhex('3738')+self.model.to_bytes(2,'little')+bytes(4)+bytes([0x30,81,0])
        else:p=bytes(self.config)
        return bytes([rid,cmd^255])+bytes(v^255 for v in p.ljust(63,b'\0'))


class DockDevice(PageDevice):
    def __init__(self):
        super().__init__()
        # Real MagDock fixture: enabled, cycle, 6 effects, speed2, brightness2, red.
        self.light_data=bytearray(bytes.fromhex('010306020200ff0000').ljust(30,b'\0'))
        self.light_data[29]=2
    def drain(self):pass
    def write_output(self,raw,**kwargs):
        self.request=raw
        if raw[2]==39:
            self.writes.append(raw);self.light_data[:9]=raw[7:16];self.light_data[29]=raw[16]
    def read_input(self,**kwargs):
        return bytes.fromhex('00aa070200001e')+bytes(self.light_data)+bytes(27)


class RazerDevice(PageDevice):
    def __init__(self):
        super().__init__();self.divisor=1;self.level=255;self.stale=False;self.corrupt=False
    def set_feature_report(self,raw):
        self.request=raw;body=raw[1:];cls,cmd=body[6:8]
        if cmd<128:
            self.writes.append(raw)
            if cls==0:self.divisor=body[9] if cmd==0x40 else body[8]
            elif (cls,cmd) in ((15,4),(3,3)):self.level=body[10]
    def get_feature_report(self,rid,length):
        body=bytearray(self.request[1:]);cls,cmd=body[6:8]
        body[0]=2
        if cmd==0x82:body[8:30]=b'TEST-SERIAL'.ljust(22,b'\0')
        elif cmd==0x85:body[8]=self.divisor
        elif cmd==0xC0:body[9]=self.divisor
        elif (cls,cmd) in ((15,0x84),(3,0x83)):body[10]=self.level
        if self.stale:body[1]^=1
        body[88]=rz.checksum(body)
        if self.corrupt:body[88]^=1
        return b'\0'+body


class IncottDevice(PageDevice):
    def __init__(self):super().__init__();self.code=0;self.model=14;self.receiver=2;self.request=b'\x09\x8f\x00'
    def set_feature_report(self,raw):
        self.request=raw
        if raw[1]<128:
            self.writes.append(raw)
            if raw[1]==1:self.code=raw[2]
            else:self.light=raw[2]
    def get_feature_report(self,rid,length):
        cmd=self.request[1]
        if cmd==0x8F:p=bytes([9,cmd,1,self.model,self.receiver,0,0xF1,0,0])
        else:p=bytes([9,cmd,self.code if cmd==0x81 else self.light])
        return p.ljust(64,b'\0')


class VaxeeDevice(PageDevice):
    def __init__(self):super().__init__();self.code=2;self.tracking=1;self.paired=0x1012
    def set_feature_report(self,raw):
        self.request=raw
        if raw[3]==2:self.writes.append(raw);self.code=raw[5]
    def get_feature_report(self,rid,length):
        cmd=self.request[2]
        p=self.paired.to_bytes(2,'little') if cmd==10 else bytes([self.tracking,0]) if cmd==8 else bytes([self.code])
        return bytes([14,0xA5,cmd,3,1,len(p)])+p+bytes(58-len(p))


class DareuDevice(PageDevice):
    def __init__(self):
        super().__init__();self.profile=0;self.memory={0:bytes.fromhex('0154'),76:bytes.fromhex('0154'),
            78:bytes.fromhex('7fd6'),80:bytes.fromhex('0253'),82:bytes.fromhex('0154')}
    def drain(self):pass
    def write_output(self,raw,**kwargs):
        self.request=raw
        if raw[1]==7:
            self.writes.append(raw);self.memory[int.from_bytes(raw[3:5],'big')]=raw[6:8]
    def read_input(self,**kwargs):
        b=bytearray(self.request);cmd=b[1]
        if cmd==3:b[6]=1
        elif cmd==14:b[6]=self.profile
        elif cmd==8:
            start=int.from_bytes(b[3:5],'big');n=b[5]
            p=b''.join(self.memory[i] for i in range(start,start+n,2));b[6:6+n]=p
        b[-1]=(0x55-sum(b[:-1]))&255
        return bytes(b)


class VendorControlTests(unittest.TestCase):
    def connect(self,d,dev):
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(pc.hw,'enumerate_hid',return_value=[d]))
        stack.enter_context(patch.object(pc.hw,'HidDevice',return_value=dev))
        stack.enter_context(patch('time.sleep'))
        return pc.discover([d])[0]

    def test_exact_catalog_excludes_bootloaders_foreign_vid_and_wrong_interfaces(self):
        for d in (iface(0x373E,0xB032),iface(0x37B0,2),iface(0x36A7,0xA887),iface(0x1234,0x1E),
                  iface(0x373E,0x1E,usage_page=1),iface(0x3837,0x100A)):
            self.assertEqual(pc.discover([d]),[])
        self.assertEqual(pc.discover([iface(0x37B0,0x10)])[0].name,'LAMZU Inca 8K')

    def test_page_roundtrip_receiver_high_rates_and_exact_single_write(self):
        dev=PageDevice();mouse=self.connect(iface(0x373E,0x1E),dev)
        state=pc.read(mouse);self.assertEqual(state.rate,1000);self.assertFalse(dev.writes)
        result=pc.apply(state,rate=4000);self.assertEqual(result.rate,4000)
        self.assertEqual(dev.writes,[bytes.fromhex('000000020201000140').ljust(65,b'\0')])

    def test_page_read_alias_and_noop_and_wired_ceiling(self):
        dev=PageDevice();mouse=self.connect(iface(0x373E,0x1C),dev)
        state=pc.read(mouse);pc.apply(state,rate=1000);self.assertFalse(dev.writes)
        with self.assertRaises(ValueError):pc.apply(state,rate=8000)
        self.assertFalse(dev.writes)

    def test_page_stale_profile_and_readback_failure(self):
        dev=PageDevice();mouse=self.connect(iface(0x373E,0x1E),dev);state=pc.read(mouse)
        dev.profile=2
        with self.assertRaisesRegex(ValueError,'已变化'):pc.apply(state,rate=2000)
        self.assertFalse(dev.writes);dev.profile=1;dev.ignore=True
        with self.assertRaisesRegex(RuntimeError,'不一致'):pc.apply(state,rate=2000)
        self.assertEqual(len(dev.writes),1)

    def test_page_receiver_lights_have_distinct_addresses_and_no_fake_color(self):
        for pid,mode,expected in ((0x47,1,'000000000202040101'),(0x33,6,'00000000060200010006'),(0x84,11,'0000000106020001000b')):
            with self.subTest(pid=pid):
                dev=PageDevice();mouse=self.connect(iface(0x373E,pid),dev);state=pc.read(mouse)
                self.assertEqual(pc.apply(state,light=pc.Light(mode)).light.mode,mode)
                self.assertEqual(dev.writes[-1],bytes.fromhex(expected).ljust(65,b'\0'))
                with self.assertRaises(ValueError):pc.apply(state,light=pc.Light(mode,rgb=(1,2,3)))

    def test_shared_wlmouse_receiver_reads_paired_identity_and_caps_1k(self):
        dev=PageDevice();mouse=self.connect(iface(0x36A7,0xA882),dev);state=pc.read(mouse)
        self.assertEqual(state.identity,(0x36A7,0xA882,0xA883));self.assertEqual(max(state.rates),1000)

    def test_mchose_rate_preserves_whole_blob_and_other_link_and_dpi(self):
        dev=MchoseDevice();mouse=self.connect(iface(0x3837,0x100B,usage_page=0xFF01,usage=1),dev)
        state=pc.read(mouse);before=bytes(dev.config)
        after=pc.apply(state,rate=4000)
        self.assertEqual(after.rate,4000);self.assertEqual(dev.config[2],0x40)
        self.assertEqual(bytes(dev.config[:2]+dev.config[3:]),before[:2]+before[3:])
        self.assertEqual(len(dev.writes),1);self.assertEqual(dev.writes[0][:2],b'\x12\xa8')

    def test_mchose_repaired_receiver_is_not_the_original_mouse(self):
        dev=MchoseDevice();mouse=self.connect(iface(0x3837,0x100B,usage_page=0xFF01,usage=1),dev)
        state=pc.read(mouse);dev.model=0x4018
        with self.assertRaises(ValueError):pc.apply(state,rate=500)
        self.assertFalse(dev.writes)

    def test_mag_dock_capture_color_speed_brightness_and_reserved_fields(self):
        dev=DockDevice();mouse=self.connect(iface(0x3837,0x1012,usage_page=0xFF00,usage=1,input_len=64,output_len=64),dev)
        state=pc.read(mouse);self.assertEqual(state.light,pc.Light(3,2,2,(255,0,0)))
        after=pc.apply(state,light=pc.Light(0,4,3,(12,34,56)))
        self.assertEqual(after.light,pc.Light(0,4,3,(12,34,56)))
        self.assertEqual(dev.writes[0][:17],bytes.fromhex('00aa270000000a0100060304000c223802'))
        off=pc.apply(after,light=replace(after.light,mode=-1));self.assertEqual(off.raw[0],0)

    def test_razer_extended_rate_commit_and_legacy_variant(self):
        for pid,rate,commands in ((0xC1,4000,[(0,0x40),(0,0x40)]),(0xB7,500,[(0,5)])):
            dev=RazerDevice();mouse=self.connect(iface(0x1532,pid,usage_page=1,usage=2),dev)
            if pid==0xC1:dev.divisor=8
            after=pc.apply(pc.read(mouse),rate=rate)
            self.assertEqual(after.rate,rate)
            self.assertEqual([tuple(r[7:9]) for r in dev.writes],commands)
            if pid==0xC1:self.assertEqual([r[9] for r in dev.writes],[0,1])

    def test_razer_light_ack_is_not_reported_as_read_effect(self):
        dev=RazerDevice();mouse=self.connect(iface(0x1532,0x99,path='test#mi_03',usage_page=1,usage=2),dev)
        state=pc.read(mouse);self.assertEqual(state.light.mode,rz.UNKNOWN_EFFECT)
        after=pc.apply(state,light=pc.Light(1,37,rgb=(255,0,10)))
        self.assertEqual(after.light.mode,rz.UNKNOWN_EFFECT);self.assertEqual(after.light.brightness,37)
        self.assertEqual(after.light.rgb,(0,0,0))
        self.assertEqual(dev.writes[0][6:18],bytes.fromhex('090f02010001000001ff000a'))

    def test_razer_checksum_stale_transaction_and_write_failure_never_retry(self):
        dev=RazerDevice();p=rz.PRODUCTS[0xC0]
        with patch('time.sleep'):
            for attr in ('stale','corrupt'):
                setattr(dev,attr,True)
                with self.assertRaises(TimeoutError):rz.query(dev,p,0,5,1,(2,))
                setattr(dev,attr,False)
        self.assertEqual(len(dev.writes),2)

    def test_incott_identity_wireless_rate_and_receiver_indicator(self):
        dev=IncottDevice();mouse=self.connect(iface(0x093A,0x522C,usage_page=0xFF05,feature_len=9),dev)
        state=pc.read(mouse);self.assertEqual(state.name,'Incott G23V2 Pro')
        after=pc.apply(state,rate=8000,light=pc.Light(2))
        self.assertEqual((after.rate,after.light.mode),(8000,2))
        self.assertEqual(dev.writes,[bytes.fromhex('090104000000000000'),bytes.fromhex('090802000000000000')])

    def test_incott_wired_and_non8k_ceiling_unknown_model_blocked(self):
        dev=IncottDevice();dev.receiver=1;mouse=self.connect(iface(0x093A,0x522C,usage_page=0xFF05,feature_len=9),dev)
        state=pc.read(mouse);self.assertEqual(max(state.rates),1000)
        with self.assertRaises(ValueError):pc.apply(state,rate=8000)
        dev.model=255
        with self.assertRaises(ValueError):pc.read(mouse)
        self.assertFalse(dev.writes)

    def test_vaxee_tracking_mode_caps_rate_and_pairing_is_checked(self):
        dev=VaxeeDevice();mouse=self.connect(iface(0x3057,0x2002,usage_page=0xFF05,usage=1,feature_len=64),dev)
        state=pc.read(mouse);after=pc.apply(state,rate=4000);self.assertEqual(after.rate,4000)
        self.assertEqual(dev.writes[0][:6],bytes.fromhex('0ea507020104'))
        dev.code=2;dev.tracking=0;limited=pc.read(mouse);self.assertEqual(limited.rates,(500,1000))
        with self.assertRaises(ValueError):pc.apply(limited,rate=4000)
        dev.paired=0xFFFF
        with self.assertRaises(ValueError):pc.read(mouse)

    def test_dareu_indicator_memory_checksums_and_off_preserves_stored_settings(self):
        dev=DareuDevice();mouse=self.connect(iface(0x260D,0x1114,usage_page=0xFF02,usage=2,input_len=17,output_len=17),dev)
        state=pc.read(mouse);self.assertEqual(state.light,pc.Light(1,5,2))
        after=pc.apply(state,rate=2000,light=pc.Light(2,10,5));self.assertEqual(after.light,pc.Light(2,10,5))
        self.assertEqual(dev.memory[0],bytes.fromhex('1045'));self.assertEqual(dev.memory[78],bytes.fromhex('ff56'))
        before=len(dev.writes);off=pc.apply(after,light=pc.Light(0,1,1))
        self.assertEqual(off.light,pc.Light(0,10,5));self.assertEqual(len(dev.writes),before+1)

    def test_invalid_controls_and_interface_change_do_not_write(self):
        dev=PageDevice();d=iface(0x373E,0x47);mouse=self.connect(d,dev);state=pc.read(mouse)
        for values in (dict(rate=True),dict(rate=3000),dict(light=pc.Light(5))):
            with self.assertRaises(ValueError):pc.apply(state,**values)
        d.usage_page=1
        with self.assertRaises(ValueError):pc.apply(state,rate=500)
        self.assertFalse(dev.writes)

    def test_atk_1k_controls_require_a_known_cid_mid_not_receiver_name(self):
        import mouse_settings as ms
        import mouse_controls as mc
        d=iface(0x373B,0x101A,usage_page=0xFF02,usage=2,input_len=17,output_len=17)
        for identity in ms.ATK_MODELS:
            mouse=ms.Mouse(d.path,'ATK',d.vid_pid,'atk',identity,interface=d)
            self.assertEqual(mc.supported(mouse),identity != ms.r1nk.IDENTITY)
        mouse.identity=(255,255);self.assertFalse(mc.supported(mouse))

    def test_optional_lighting_failure_leaves_rate_available_but_blocks_light_write(self):
        dev=PageDevice();mouse=self.connect(iface(0x373E,0x47),dev)
        original=pc.page
        def reject(dev,target,page_id,cmd,*args):
            if page_id==2:raise ValueError('unsupported')
            return original(dev,target,page_id,cmd,*args)
        with patch.object(pc,'page',side_effect=reject):
            state=pc.read(mouse);self.assertIsNone(state.light)
            self.assertEqual(pc.apply(state,rate=2000).rate,2000)
            with self.assertRaises(ValueError):pc.apply(state,light=pc.Light(1))

    def test_page_ignores_wrong_target_and_refuses_bad_payload_length(self):
        dev=PageDevice()
        with patch.object(dev,'get_feature_report',return_value=bytes.fromhex('00a100010201800101').ljust(65,b'\0')),patch('time.sleep'):
            with self.assertRaises(TimeoutError):pc.page(dev,2,1,0x80,2,(1,))
        with patch.object(dev,'get_feature_report',return_value=bytes.fromhex('00a100023f0180').ljust(65,b'\0')),patch('time.sleep'):
            with self.assertRaises(ValueError):pc.page(dev,2,1,0x80,2,(1,))

    def test_dareu_corrupted_memory_disables_changes(self):
        dev=DareuDevice();dev.memory[0]=b'\x01\x00'
        mouse=self.connect(iface(0x260D,0x1114,usage_page=0xFF02,usage=2,input_len=17,output_len=17),dev)
        with self.assertRaisesRegex(ValueError,'校验'):pc.read(mouse)
        self.assertFalse(dev.writes)

    def test_dareu_write_ack_does_not_require_read_only_address_echo(self):
        import mouse_settings as ms
        d=iface(0x260D,0x1114,input_len=17,output_len=17)
        dev=DareuDevice()
        with patch.object(dev,'read_input',return_value=ms.atk_frame(7)):
            self.assertEqual(vc.dareu_query(dev,d,7,(0,0,0,2,1,0x54)),ms.atk_frame(7))


class VendorUITests(unittest.TestCase):
    def test_controls_only_page_never_invents_dpi_and_requires_apply(self):
        import tkinter as tk
        from ui_mouse_pages import MouseSettingsPage
        from ui_peripheral_controls import PeripheralEditor
        root=tk.Tk();root.withdraw()
        try:
            mouse=pc.discover([iface(0x3837,0x1012,usage_page=0xFF00,usage=1,input_len=64,output_len=64)])[0]
            state=pc.State(mouse,'MagDock',None,(),0,mouse.identity,pc.Light(0,2,2,(255,0,0)),b'\x00\x06\x00\x02')
            page=MouseSettingsPage(root)
            with patch.object(pc,'apply') as apply:
                editor=PeripheralEditor(page,state);editor.color.set('#00FF00');editor.brightness.set('4')
                apply.assert_not_called();self.assertIsNone(page.snapshot)
                with patch.object(page,'run') as run:
                    editor.apply_light();work=run.call_args.args[0];work()
                self.assertEqual(apply.call_args.kwargs['light'],pc.Light(0,4,2,(0,255,0)))
        finally:root.destroy()


if __name__=='__main__':unittest.main()
