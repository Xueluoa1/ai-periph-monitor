"""Replay packet layouts and transactional failures without hardware writes."""
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import mouse_settings as ms
import dpi_adapters as da
import dpi_extended as dx
import peripheral_controls as pc
from verify_vendor_controls import iface, PageDevice, MchoseDevice


class DpiPage(PageDevice):
    def __init__(self):
        super().__init__();self.table=bytearray(bytes.fromhex('010303200320064003200c800c80').ljust(26,b'\x36'));self.active=2
    def set_feature_report(self,r):
        self.requests.append(r);self.request=r
        target,n,page,cmd=r[3:7]
        if cmd<128:
            self.writes.append(r)
            if not self.ignore:
                if (page,cmd)==(1,1):self.table[:n]=r[7:7+n]
                if (page,cmd)==(1,2):self.active=r[8]
    def get_feature_report(self,rid,length):
        target,n,page,cmd=self.request[3:7]
        if cmd==0x81:p=bytes(self.table)
        elif cmd==0x82:p=bytes([self.profile,self.active])
        else:return super().get_feature_report(rid,length)
        return bytes([0,0xA1,0,target,len(p),page,cmd])+p+bytes(58-len(p))


class DpiTests(unittest.TestCase):
    def setup_device(self,d,dev,protocol='extra'):
        mouse=ms.Mouse(d.path,'fixture',d.vid_pid,protocol,(d.vid,d.pid),interface=d)
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(pc.hw,'enumerate_hid',return_value=[d]))
        stack.enter_context(patch.object(pc.hw,'HidDevice',return_value=dev))
        stack.enter_context(patch.object(da,'device_session',side_effect=nullcontext))
        stack.enter_context(patch.object(ms,'device_session',side_effect=nullcontext))
        stack.enter_context(patch.object(pc.time,'sleep'))
        return mouse

    def test_page_read_edit_switch_and_unrelated_fields(self):
        dev=DpiPage();m=self.setup_device(iface(0x373E,0x1C),dev)
        s=ms.read(m);self.assertEqual(s.stages,[(800,800),(1600,800),(3200,3200)])
        result=ms.apply(s,1,2400,False)
        self.assertEqual(result.stages[1],(2400,800));self.assertEqual(result.active,1)
        self.assertEqual(dev.writes[0][7:21],bytes.fromhex('010303200320096003200c800c80'))
        self.assertEqual(dev.code,0x10)
        result=ms.apply(result,2,None,True);self.assertEqual(result.active,2)
        self.assertEqual(dev.writes[-1][3:9],bytes([2,2,1,2,1,3]))

    def test_luna_dormant_bytes_preserved(self):
        dev=DpiPage();m=self.setup_device(iface(0x373E,0x54),dev)
        s=ms.read(m);before=dev.table[14:]
        ms.apply(s,0,1000,False)
        self.assertEqual(dev.table[14:],before);self.assertEqual(dev.writes[0][4],26)

    def test_stale_rejected_before_write(self):
        dev=DpiPage();m=self.setup_device(iface(0x373E,0x1C),dev);s=ms.read(m)
        dev.active=1
        with self.assertRaisesRegex(ValueError,'已变化'):ms.apply(s,0,1000,False)
        self.assertFalse(dev.writes)

    def test_noop_no_settings_write(self):
        dev=DpiPage();m=self.setup_device(iface(0x373E,0x1C),dev);s=ms.read(m)
        ms.apply(s,0,800,False);self.assertFalse(dev.writes)

    def test_failed_readback_no_retry(self):
        dev=DpiPage();m=self.setup_device(iface(0x373E,0x1C),dev);s=ms.read(m);dev.ignore=True
        with self.assertRaisesRegex(RuntimeError,'读回不一致'):ms.apply(s,0,1000,False)
        self.assertEqual(len(dev.writes),1)

    def test_invalid_replies_no_write(self):
        for table in (b'\1\0',b'\2\3',bytes.fromhex('010100000000')):
            with self.subTest(table=table):
                dev=DpiPage();dev.table=bytearray(table)
                m=self.setup_device(iface(0x373E,0x1C),dev)
                with self.assertRaises(ValueError):ms.read(m)
                self.assertFalse(dev.writes)

    def test_bounds_checked_before_io(self):
        m=ms.Mouse('x','x','x');b=pc.State(m,'x',None,(),1,())
        c=da.Config(b,'page',((800,800),),0,50,30000,50)
        for v in (0,49,801,30050,True):
            with self.subTest(value=v),patch.object(da,'_read') as read:
                with self.assertRaises(ValueError):da.apply(da.view(m,c),0,v,False)
                read.assert_not_called()

    def test_mchose_whole_config_preserved(self):
        dev=MchoseDevice();d=iface(0x3837,0x1020,usage_page=0xFF01,usage=1)
        m=self.setup_device(d,dev);s=ms.read(m);before=bytes(dev.config)
        result=ms.apply(s,0,1200,False)
        self.assertEqual(result.stages[0],(1200,1200))
        self.assertEqual(bytes(dev.config[:4]+dev.config[6:]),before[:4]+before[6:])
        result=ms.apply(result,4,None,True)
        self.assertEqual(dev.config[2]>>4,before[2]>>4);self.assertEqual(result.active,4)

    def test_exact_interface_and_dock_gates(self):
        d=iface(0x373E,0x1C,usage_page=1);m=self.setup_device(d,DpiPage())
        with self.assertRaises(ValueError):ms.read(m)
        self.assertFalse(da.supported(ms.Mouse('','','',identity=(0x3837,0x1012))))
        self.assertFalse(dx.family(iface(0xA8A5,0x2157,usage_page=0xFF01,usage=16,output_len=17,input_len=64)))

    def test_incott_axes_and_packets(self):
        m=ms.Mouse('','','');b=pc.State(m,'incott',1000,(1000,),1,(0x093A,0x522C,b'\1\2\3\xf1\0'))
        def response(dev,cmd,args,axis=None):
            if cmd==0x83:return bytes([9,cmd,2,1])+bytes(60)
            value=(800,1600)[args[0]] if axis==1 else 800
            return bytes([9,cmd,args[0]])+(value//50-1).to_bytes(2,'little')+bytes([0,0,0,axis])+bytes(55)
        with patch.object(da,'incott_query',side_effect=response),patch.object(da.time,'sleep'):
            c=da.incott_read(None,None,b);self.assertEqual(c.maximum,45000)
            dev=Mock();after=da.write(dev,None,c,1,2400,False)
            self.assertEqual(after.stages[1],(2400,800))
            dev.set_feature_report.assert_called_once_with(bytes.fromhex('0902012f0000000001'))

    def test_dareu_checksum_high_range_and_preservation(self):
        m=ms.Mouse('','','');b=pc.State(m,'dareu',1000,(1000,),1,())
        raw=bytes.fromhex('01540055')+ms.atk_encode(16000,16000,'step50')
        c=da.Config(b,'dareu',((16000,16000),),0,100,26000,50,raw)
        with patch.object(da.vc,'dareu_query') as query:
            after=da.write(None,None,c,0,26000,False)
            p=bytes(query.call_args.args[3][4:]);self.assertEqual(sum(p)&255,0x55)
            self.assertEqual(da.dareu_axis(p[0],p[2]),26000)
            self.assertEqual(after.raw[:4],raw[:4])

    def test_razer_table_preserves_reserved_and_y(self):
        d=iface(0x1532,0xC1);m=ms.Mouse(d.path,'','','extra',(d.vid,d.pid));b=pc.State(m,'rz',1000,(1000,),0,(1,2,b'serial'))
        table=bytearray(38);table[:3]=bytes([1,1,2]);table[3:10]=bytes.fromhex('00032003201234');table[10:17]=bytes.fromhex('01064003205678');table[17:]=bytes([0xA5])*21
        with patch.object(da.rz,'query',side_effect=[bytes.fromhex('01032003200000'),bytes(table)]):c=da.razer_read(None,d,b)
        with patch.object(da.rz,'query') as query:
            after=da.write(None,d,c,1,2000,True)
            sent=query.call_args.args[-1]
            self.assertEqual(sent[11:15],bytes.fromhex('07d00320'));self.assertEqual(sent[15:],table[15:]);self.assertEqual(sent[1],2)
            self.assertEqual(after.stages[1],(2000,800))

    def test_razer_optional_table_current_only(self):
        d=iface(0x1532,0x83);m=ms.Mouse('','','');b=pc.State(m,'rz',1000,(1000,),0,())
        with patch.object(da.rz,'query',side_effect=[bytes.fromhex('01032003200000'),ValueError('unsupported')]):c=da.razer_read(None,d,b)
        self.assertEqual(c.family,'razer_current');self.assertFalse(c.can_activate)

    def test_vaxee_current_only_cannot_fake_switch(self):
        m=ms.Mouse('','','');c=da.Config(pc.State(m,'v',None,(),0,()),'vaxee',((800,800),),0,100,26000,50,bytes.fromhex('032003'),3,can_activate=False)
        with self.assertRaises(ValueError):da.apply(da.view(m,c),0,None,True)
        with patch.object(dx.time,'sleep'),patch.object(da.vc,'vaxee_query') as query:
            da.write(None,None,c,0,1600,False)
            self.assertEqual(query.call_args.args[3],bytes.fromhex('034006'))


class ExtraTests(unittest.TestCase):
    def test_rog_exact_models_read_color_preservation_and_legacy_gate(self):
        import rog_dpi_models as rog
        d=iface(0x0B05,0x1A68,output_len=65);m=ms.Mouse(d.path,'rog','','dpi',(d.vid,d.pid,d.pid))
        profile=bytearray(64);profile[11]=1;profile[12]=2
        raw=bytes.fromhex('0012040200070007000f000f001f001f003f003f00').ljust(64,b'\0')
        settings=bytes.fromhex('0012040000ffffffffffffffff03000200000000').ljust(64,b'\0')
        colors=bytes.fromhex('0012040300ff0000c100ff003dff31ff00').ljust(64,b'\0')
        with patch.object(ms,'rog_query',side_effect=[profile,settings,raw,colors,profile]):c=rog.read(None,d,m)
        self.assertEqual(c.stages,((400,400),(800,800),(1600,1600),(3200,3200)))
        with patch.object(ms,'rog_query') as query:
            after=rog.write(None,d,c,1,1200,False)
            self.assertEqual(query.call_args_list[0].args[3],bytes.fromhex('513101001700c100ff'))
            self.assertEqual(after.base.raw,c.base.raw);self.assertEqual(after.stages[1],(1200,1200))
        self.assertFalse(rog.MODELS[0x1960].activate)
        self.assertNotIn(0x1B6E,rog.MODELS)  # Old name-only IDs must not enable writes.
        self.assertFalse(rog.match(iface(0x0B05,0x1A68,path='x#mi_01',output_len=65)))

    def test_rog_pairing_change_refuses_commands(self):
        import rog_dpi_models as rog
        d=iface(0x0B05,0x1ACE,usage_page=0xFF01,output_len=64);m=ms.Mouse(d.path,'rog','','dpi',(d.vid,d.pid,0x1A68))
        with patch.object(ms,'omni_identity',return_value={}),patch.object(pc.hw,'enumerate_hid',return_value=[d]),patch.object(ms,'rog_query') as q:
            with self.assertRaises(ValueError):rog.read(None,d,m)
            q.assert_not_called()

    def test_vxe_receiver_preserves_adjacent_row_and_system(self):
        d=iface(0x3554,0xF58A);m=ms.Mouse(d.path,'vxe','','dpi',(d.vid,d.pid));base=pc.State(m,'vxe',None,(),0,(*m.identity,2,27))
        header=bytes.fromhex('01540451015400550055');table=b''.join(ms.atk_encode(v,v,'step50') for v in (400,800,1600,3200,6400,12000,18000,24000))
        c=da.Config(base,'vxe',((400,400),(800,800),(1600,1600),(3200,3200)),1,100,30000,50,header+table)
        with patch.object(dx,'vxe_write_row') as write:
            after=dx.write(None,d,c,1,1200,True)
            self.assertEqual(write.call_args.args[2],12)
            self.assertEqual(write.call_args.args[3][:4],table[:4])
            self.assertEqual(after.raw[:14],c.raw[:14]);self.assertEqual(after.raw[18:],c.raw[18:])
        with patch.object(dx,'vxe_write_row') as write:
            after=dx.write(None,d,c,2,None,True)
            self.assertEqual(write.call_args.args[2],0);self.assertEqual(len(write.call_args.args[3]),10)
            self.assertEqual(after.raw[6:],c.raw[6:])

    def test_vxe_wrong_shared_model_is_rejected(self):
        with patch.object(ms,'atk_identity',return_value=(2,32)),patch.object(ms,'atk_read') as read:
            with self.assertRaises(ValueError):dx.vxe_read(None,None,None)
            read.assert_not_called()

    def test_atlantis_packet_and_split_axis_rejection(self):
        def exchange(dev,packet,accept):
            self.assertEqual(packet,ms.atk_frame(8,(0,0,12,4)))
            reply=ms.atk_frame(8,(0,0,12,4,7,7,0,0x47))
            self.assertTrue(accept(reply));self.assertFalse(accept(ms.atk_frame(8,(0,0,16,4,7,7,0,0x47))))
            return reply
        with patch.object(dx,'input_exchange',side_effect=exchange):self.assertEqual(dx.atlantis_query(None,None,8,12,length=4),bytes.fromhex('07070047'))
        m=ms.Mouse('','','','dpi');c=da.Config(pc.State(m,'lamzu',None,(),1,()),'atlantis',((800,1600),),0,50,26000,50,b'')
        with patch.object(dx,'atlantis_query') as q:
            with self.assertRaises(ValueError):dx.write(None,None,c,0,1200,False)
            q.assert_not_called()

    def test_ajazz_roundtrip_preserves_non_dpi_and_dormant(self):
        raw=bytearray(63);raw[8:12]=bytes([2,4,4,2])
        for i,v in enumerate((400,800,1600,3200,6400,12000)):raw[12+i*2:14+i*2]=v.to_bytes(2,'little')
        raw[47:54]=bytes([1,2,3,4,5,6,7])
        reply=b'\xf0\x0e'+bytes(raw[1:])
        with patch.object(dx,'input_exchange',return_value=reply):normalized=dx.ajazz_read(None)
        m=ms.Mouse('','','','dpi');b=pc.State(m,'ajazz',None,(),1,())
        c=da.Config(b,'ajazz',((400,400),(800,800),(1600,1600),(3200,3200)),1,50,24000,50,normalized)
        dev=Mock()
        with patch.object(dx.time,'sleep'):after=dx.write(dev,None,c,2,2400,False)
        sent=dev.write_output.call_args.args[0]
        self.assertEqual(sent[:5],bytes.fromhex('f00f010a2f'))
        self.assertEqual(sent[17:19],(2400).to_bytes(2,'little'))
        self.assertEqual(sent[48:55],raw[47:54]);self.assertEqual(after.active,1)

    def test_ajazz_blank_is_not_default(self):
        with patch.object(dx,'input_exchange',return_value=b'\xf0\x0e'+bytes(62)):
            with self.assertRaises(ValueError):dx.ajazz_read(None)

    def test_wl_crc_and_echo(self):
        self.assertEqual(dx.crc16(b'123456789'),0x4B37)
        def exchange(dev,packet,accept):
            reply=bytearray(packet);reply[8]=3;reply[1:3]=dx.crc16(reply[3:32]).to_bytes(2,'little')
            self.assertTrue(accept(reply));bad=bytearray(reply);bad[6]^=1;self.assertFalse(accept(bad))
            bad=bytearray(reply);bad[8]^=1;self.assertFalse(accept(bad))
            return reply
        with patch.object(dx,'input_exchange',side_effect=exchange):self.assertEqual(dx.wl_query(None,5,length=1),b'\3')

    def test_wl_only_changed_chunk_writes_close_on_error(self):
        m=ms.Mouse('','','','dpi');b=pc.State(m,'wl',None,(),3,())
        raw=bytearray(range(117));raw[15]=1;raw[16:20]=bytes.fromhex('20034006')
        c=da.Config(b,'wl4k',((800,1600),),0,50,26000,50,bytes(raw))
        with patch.object(dx,'wl_query') as q:
            result=dx.write(None,None,c,0,1200,False)
            self.assertEqual([call.args[1] for call in q.call_args_list],[1,6,2])
            self.assertEqual(q.call_args_list[1].args[2],256)
            self.assertEqual(result.raw[18:],c.raw[18:]);self.assertEqual(result.stages[0],(1200,1600))
        with patch.object(dx,'wl_query',side_effect=[b'',OSError('failed'),b'']) as q:
            with self.assertRaises(OSError):dx.write(None,None,c,0,1200,False)
            self.assertEqual(q.call_args_list[-1].args[1],2)

    def test_logitech_options_malformed_range_rejected(self):
        self.assertEqual(dx.dpi_options(bytes.fromhex('0064e03200c80000')),(100,150,200))
        for raw in ('0064e00000c8','e03200c8','0064e0320032','0000','00640064'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):dx.dpi_options(bytes.fromhex(raw))

    def test_logitech_mode_prevents_side_effect_switch(self):
        m=ms.Mouse('','','','dpi',(0x046D,1,1,0x2201,7));b=pc.State(m,'logi',None,(),0,(*m.identity,b'unit',4,1))
        c=da.Config(b,'logitech',((800,800),),0,100,2000,1,bytes.fromhex('0003200320'),options=(800,1600),can_activate=False)
        with patch.object(dx,'logitech_call') as call:
            with self.assertRaisesRegex(ValueError,'板载模式'):dx.write(None,None,c,0,1600,False)
            call.assert_not_called()

    def test_logitech_extended_preserves_y_and_lod(self):
        m=ms.Mouse('','','','dpi',(0x046D,1,1,0x2202,7));b=pc.State(m,'logi',None,(),0,(*m.identity,b'unit',4,2))
        raw=bytearray(13);raw[1:3]=b'\3\x20';raw[5:7]=b'\6\x40';raw[9]=3
        c=da.Config(b,'logitech',((800,1600),),0,100,2000,1,bytes(raw),options=(800,1200,1600),can_activate=False)
        with patch.object(dx,'logitech_call') as call:
            after=dx.write(None,None,c,0,1200,False)
            self.assertEqual(call.call_args.args[4],bytes.fromhex('0004b0064003'))
            self.assertEqual(after.stages,((1200,1600),));self.assertEqual(after.raw[9],3)

    def test_egg_sensor_block_preserves_other_fields(self):
        m=ms.Mouse('','','','dpi');b=pc.State(m,'egg',None,(),0,())
        raw=bytes(range(131));c=da.Config(b,'egg',((800,1600),),-1,50,26000,50,raw,can_activate=False)
        dev=Mock();dev.get_feature_report.return_value=b'\xa1\1'+bytes(62)
        with patch.object(dx,'egg_command') as command:
            after=dx.write(dev,None,c,0,2000,False)
            sent=command.call_args.args[3]
            self.assertEqual(len(sent),28);self.assertEqual(sent[:8],bytes(raw[i] for i in (23,24,25,26,27,17,30,29)))
            self.assertEqual(after.raw[:52],raw[:52]);self.assertEqual(after.raw[56:],raw[56:]);self.assertEqual(after.active,-1)

    def test_a5_exact_identity_limits_and_readback(self):
        dev=DpiPage();d=iface(0x2023,0xF013);m=ms.Mouse(d.path,'A5',d.vid_pid,'dpi',(d.vid,d.pid))
        with patch.object(dx,'interface',return_value=d),patch.object(dx.hw,'HidDevice',return_value=dev),patch.object(dx.time,'sleep'):
            c=dx.read(m);self.assertEqual(c.maximum,26000);self.assertEqual(c.stages[1],(1600,800))
            dx.write(dev,d,c,1,2400,False);self.assertEqual(dx.read(m).stages[1],(2400,800))


if __name__=='__main__':unittest.main(verbosity=2)
