"""Protocol fixtures and write guards. These tests never open a physical device."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import extended_controls as ec
import extended_lighting as el
import dpi_adapters as da
import dpi_extended as dx
import peripheral_controls as pc
import mouse_settings as ms


def fixture(f,vid=1,pid=2,raw=None,identity=None):
    m=ms.Mouse('fake',f,'fixture','dpi',identity or (vid,pid))
    base=pc.State(m,f,None,(),1,m.identity)
    return da.Config(base,f,((800,800),(1600,1600)),0,50,30000,50,bytes(140) if raw is None else bytes(raw))


class RateTests(unittest.TestCase):
    def test_model_ranges_and_byte_orders(self):
        cases=[('vxe',0xF58A,0,1,(250,500,1000)),
               ('scyrox',0xF5F7,0,0x40,ec.RATES),
               ('atlantis',0xF510,0,0x10,(500,1000,2000,4000)),
               ('atlantis',0xF50D,0,1,ec.RATES[:4]),
               ('rk',0x1019,1,0x44,ec.RATES),
               ('ajazz',0x2157,9,4,(125,500,1000)),
               ('wl4k',0xA887,11,5,ec.RATES[:6]),
               ('hecate',0x706E,11,4,ec.RATES[:5])]
        for f,pid,offset,code,rates in cases:
            with self.subTest(f=f,pid=pid):
                raw=bytearray(140);raw[offset]=code
                if offset==0:raw[1]=(0x55-code)&255
                s=fixture(f,pid=pid,raw=raw)
                rate,codes,_,error=ec.rate_info(None,SimpleNamespace(pid=pid),s)
                self.assertEqual(tuple(hz for hz,_ in codes),rates)
                self.assertEqual(rate,8000 if f in ('scyrox','rk') else 4000 if f=='wl4k' else 2000 if f=='hecate' else 1000)
                self.assertEqual(error,'')

    def test_rog_limits_and_pulsar_type_ceiling(self):
        for pid,expected in ((0x1A18,(250,500,1000)),(0x1977,(250,500,1000)),(0x1B63,ec.RATES[:4])):
            s=fixture('rog_more',identity=(0xB05,pid,pid));s=replace(s,base=replace(s.base,raw=bytes([3,0,2,0,0,0])))
            rate,codes,_,_=ec.rate_info(None,None,s)
            self.assertEqual(rate,1000);self.assertEqual(tuple(dict(codes)),expected)
        for kind,ceiling in enumerate((1000,4000,1000,8000,2000,8000)):
            s=fixture('pulsar',raw=ec.pair(1)+bytes(8))
            s=replace(s,base=replace(s.base,identity=(0x3710,0x5406,0x57,4,kind,3)))
            self.assertEqual(max(dict(ec.rate_info(None,None,s)[1])),ceiling)

    def test_a5_get_and_set(self):
        s=fixture('a5',pid=0xF019)
        with patch.object(pc,'page',return_value=b'\x01') as page:
            rate,codes,meta,error=ec.rate_info(None,SimpleNamespace(pid=0xF019),s)
            self.assertEqual(tuple(dict(codes)),(125,500,1000));page.assert_called_with(None,2,1,0x80,1)
            state=ec.State(s,rate,codes);result=ec.write_rate(None,None,state,500)
            page.assert_called_with(None,2,1,0,1,(2,));self.assertEqual(result.config,s)

    def test_egg_versions_glass_mode_and_power_block_preservation(self):
        for pid,rates in ((0x1972,(1000,2000,4000)),(0x1984,(125,1000,2000,4000))):
            raw=bytearray(range(140));raw[21]=8;raw[127]=0
            s=fixture('egg',raw=raw);s=replace(s,base=replace(s.base,identity=(0x3367,pid,pid)))
            current,codes,_,_=ec.rate_info(None,None,s)
            self.assertEqual(tuple(dict(codes)),rates)
            with patch.object(ec,'egg_write') as write:
                result=ec.write_rate(None,None,ec.State(s,current,codes),4000)
            self.assertEqual([i for i,(a,b) in enumerate(zip(s.raw,result.config.raw)) if a!=b],[21])
            payload=write.call_args.args[2]
            self.assertEqual(len(payload),11 if pid==0x1984 else 10)
            self.assertEqual(payload[:4],bytes([28,2,22,20]))
            if pid==0x1984:
                raw[127]=1
                self.assertEqual(ec.rate_info(None,None,replace(s,raw=bytes(raw)))[1],())

    def test_high_mode_does_not_get_silently_disabled(self):
        raw=bytearray(117);raw[10]=1;raw[11]=3
        s=fixture('wl4k',raw=raw)
        current,codes,_,note=ec.rate_info(None,None,s)
        self.assertEqual(tuple(dict(codes)),ec.RATES[:4]);self.assertIn('性能模式',note)

    def test_logitech_dynamic_capabilities_connection_and_host_mode(self):
        for fid,fi in ((0x8061,12),(0x8060,13)):
            s=fixture('logitech',identity=(0x46D,0xC539,2,0x2201,7))
            s=replace(s,base=replace(s.base,identity=(*s.base.identity,b'unit',10,2)))
            def call(dev,index,feature,fn,args=()):
                if fid==0x8061:return b'\0\x7f'+bytes(14) if fn==1 else b'\x03'+bytes(15)
                return bytes([0x8B if fn==0 else 1])+bytes(15)
            with patch.object(dx,'feature_index',side_effect=lambda dev,idx,feature:fi if feature==fid else 0),patch.object(dx,'logitech_call',side_effect=call) as query:
                rate,codes,meta,_=ec.rate_info(None,None,s)
                self.assertEqual(rate,1000);self.assertEqual(meta,(fid,fi,1))
                self.assertEqual(tuple(dict(codes)),ec.RATES if fid==0x8061 else (125,250,500,1000))
                if fid==0x8061:self.assertEqual(query.call_args.args[-1],(1,))
                locked=replace(s,base=replace(s.base,identity=(*s.base.identity[:-1],1)))
                self.assertEqual(ec.rate_info(None,None,locked)[1],())
            with patch.object(dx,'logitech_call') as query:
                ec.write_rate(None,None,ec.State(s,rate,codes,meta=meta),500)
                query.assert_called_once_with(None,2,fi,3 if fid==0x8061 else 2,(2,))

    def test_wire_writes_preserve_neighbors_and_checksums(self):
        raw=bytearray(range(42));raw[:2]=ec.pair(1)
        s=fixture('vxe',raw=raw);state=ec.State(s,1000,tuple(ec.SCALAR.items())[:4])
        for pid,length in ((0xF58A,10),(0xF58C,2)):
            with patch.object(dx,'vxe_write_row') as write:
                result=ec.write_rate(None,SimpleNamespace(pid=pid),state,500)
                data=write.call_args.args[3]
                self.assertEqual(len(data),length);self.assertEqual(data[:2],b'\x02\x53')
                self.assertEqual(result.config.raw[2:],s.raw[2:])
        with patch.object(dx,'atlantis_query') as query:
            ec.write_rate(None,None,replace(state,config=replace(s,family='atlantis')),500)
            query.assert_called_once_with(None,None,7,0,b'\x02\x53')

    def test_rk_and_ajazz_write_golden(self):
        dev=Mock();s=fixture('rk',raw=bytes(range(56)));state=ec.State(s,1000,((1000,0),(4000,0x55)))
        with patch.object(ec.time,'sleep'):
            result=ec.write_rate(dev,None,state,4000)
        packet=dev.set_feature_report.call_args.args[0]
        self.assertEqual(packet[2:8],bytes([0x50,1,0x31,0x35,1,0x55]))
        self.assertEqual(packet[1],sum(packet[2:63])&255)
        s=fixture('ajazz',raw=bytes(range(63)))
        with patch.object(ec.time,'sleep'):
            result=ec.write_rate(dev,None,ec.State(s,1000,((500,3),)),500)
        packet=dev.write_output.call_args.args[0]
        self.assertEqual(packet[:5],bytes.fromhex('f00f010a2f'));self.assertEqual(packet[10],3)
        self.assertEqual(packet[11:],s.raw[10:])

    def test_unknown_rate_does_not_become_1000(self):
        s=fixture('rk',raw=bytes([0,0x77])+bytes(54))
        with self.assertRaises(ValueError):ec.rate_info(None,None,s)

    def test_rog_rate_changes_only_rate_in_guard_snapshot(self):
        s=fixture('rog_more',identity=(0xB05,0x1B63,0x1B63))
        s=replace(s,base=replace(s.base,raw=bytes(range(12))+bytes([3,0,2,0,0,0])))
        with patch.object(ms,'rog_query') as q:
            expected=ec.write_rate(None,None,ec.State(s,1000,((125,0),(1000,3))),125)
        self.assertEqual(q.call_args_list[0].args[-1],(0x51,0x31,4,0,0))
        self.assertEqual(expected.config.base.raw[:12],s.base.raw[:12])
        self.assertEqual(expected.config.base.raw[-5:],s.base.raw[-5:])
        self.assertEqual(expected.config.raw,s.raw)

    def test_wl_changed_chunks_and_session_cleanup(self):
        s=fixture('wl4k',raw=bytes(117));state=ec.State(s,125,((125,0),(1000,3)))
        with patch.object(dx,'wl_query') as q:
            result=ec.write_rate(None,None,state,1000)
        self.assertEqual([call.args[1] for call in q.call_args_list],[1,6,2])
        self.assertEqual(q.call_args_list[1].args[2],0)
        self.assertEqual(q.call_args_list[1].args[3],bytes(11)+b'\x03'+bytes(12))
        with patch.object(dx,'wl_query',side_effect=[None,TimeoutError(),None]) as q:
            with self.assertRaises(TimeoutError):ec.write_rate(None,None,state,1000)
        self.assertEqual(q.call_args_list[-1].args,(None,2))

    def test_scyrox_and_pulsar_session_checked_rate_pair(self):
        for f,module,session_name,query_name in [('scyrox',ec.dm,'scyrox_session','scyrox_query'),('pulsar',ec.pu,'session','query')]:
            s=fixture(f,raw=ec.pair(1)+bytes(8));s=replace(s,base=replace(s.base,identity=(1,2,3,4,5,6)))
            with patch.object(module,session_name,return_value=nullcontext()) as session,patch.object(module,query_name) as q:
                result=ec.write_rate(None,None,ec.State(s,1000,tuple(ec.SCALAR.items())),8000)
            session.assert_called_once_with(None,None,(3,4,5,6))
            q.assert_called_once_with(None,None,7,0,b'\x40\x15')
            self.assertEqual(result.config.raw[2:],s.raw[2:])


class LightingTests(unittest.TestCase):
    def test_rog_aggregate_and_per_zone_layouts(self):
        def aggregate(*args):
            r=bytearray(65)
            for i in range(3):r[5+i*5:10+i*5]=bytes([i,4,10+i,20+i,30+i])
            r[21:24]=bytes([1,0,7]);return bytes(r)
        s=fixture('rog_more',identity=(0xB05,0x18E5,0x18E5))
        with patch.object(ms,'rog_query',side_effect=aggregate) as query:
            zones,_=el.read(None,None,s)
            self.assertEqual(len(zones),3);self.assertEqual(query.call_count,1)
            self.assertEqual(zones[2].rgb,(12,22,32));self.assertEqual(zones[0].maximum,4)
        s=fixture('rog_more',identity=(0xB05,0x1B63,0x1B63))
        with patch.object(ms,'rog_query',side_effect=aggregate) as query:
            zones,_=el.read(None,None,s)
            self.assertEqual([z.key for z in zones],[1]);self.assertEqual(query.call_args.args[-1],(0x12,3,0))
        z=replace(zones[0],mode=0,brightness=50,rgb=(255,128,0))
        with patch.object(ms,'rog_query') as query:
            el.write(None,None,ec.State(s,zones=zones),z)
            self.assertEqual(query.call_args_list[0].args[-1],bytes([0x51,0x28,1,0,0,50,255,128,0])+z.aux)
            self.assertEqual(query.call_args_list[1].args[-1],(0x50,3))

    def test_vxe_colors_neighbor_and_effect_preservation(self):
        raw=ec.pair(1)+ec.pair(128)+ec.pair(3)+ec.pair(1)+bytes([255,0,0,0x56,0,255,0,0x56])
        s=fixture('vxe');zones=el.vxe_zones(raw,2);state=ec.State(s,zones=zones,light_raw=raw)
        with patch.object(dx,'vxe_write_row') as write:
            result=el.write(None,None,state,replace(zones[1],rgb=(1,2,3)))
            self.assertEqual(write.call_args.args[2:],(44,bytes([1,2,3,79,0,255,0,86])))
            self.assertEqual(result.light_raw[:8],raw[:8])
            result=el.write(None,None,state,replace(zones[0],mode=2,brightness=0,speed=2))
            self.assertEqual(result.light_raw[:8],ec.pair(2)+ec.pair(128)+ec.pair(5)+ec.pair(1))
            result=el.write(None,None,state,replace(zones[0],mode=0))
            self.assertEqual(result.zones[0].brightness,-1)

    def test_rk_color_keeps_actual_dpi_and_selection(self):
        raw=bytearray(range(56));s=replace(fixture('rk',raw=raw),selector=2)
        zones,_=el.read(None,None,s);dev=Mock()
        with patch.object(el.time,'sleep'):
            result=el.write(dev,None,ec.State(s,zones=zones),replace(zones[0],rgb=(255,0,255)))
        packet=dev.set_feature_report.call_args.args[0]
        self.assertEqual(packet[7:13],bytes([raw[2],raw[15],raw[16],255,0,255]))
        changed=[i for i,(a,b) in enumerate(zip(s.raw,result.config.raw)) if a!=b]
        self.assertEqual(changed,[33,34,35])

    def test_pulsar_switch_preserves_three_colors(self):
        s=fixture('pulsar');s=replace(s,base=replace(s.base,identity=(0x3710,0x5406,0x57,4,3,3)))
        raw=bytes([1,255,0,0,0,255,0,0,0,255])
        with patch.object(el.pu,'session',return_value=nullcontext()),patch.object(el.pu,'query',return_value=raw) as query:
            zones,data=el.read(None,None,s)
            result=el.write(None,None,ec.State(s,zones=zones,light_raw=data),replace(zones[0],mode=0))
            query.assert_called_with(None,None,0x14,data=b'\0'+raw[1:])
            self.assertEqual(result.light_raw[1:],raw[1:])

    def test_hecate_color_and_rate_use_separate_runtime_command(self):
        from verify_dpi_special import HecateTests
        header,raw,calls,query=HecateTests().fixture()
        raw[11]=3;m=ms.Mouse('g3m','','','dpi',(0x320F,0x706E))
        with patch.object(el.he,'query',side_effect=query),patch.object(el.he.time,'sleep'):
            config=el.he.read(None,m);zones,_=el.read(None,None,config)
            state=ec.State(config,1000,tuple((hz,i) for i,hz in enumerate(ec.RATES[:5])),zones)
            expected=el.write(None,None,state,replace(zones[2],rgb=(255,0,128)))
            actual=el.he.read(None,m)
            self.assertEqual(da.fingerprint(expected.config),da.fingerprint(actual))
            self.assertFalse(any(c[0]==0x29 for c in calls));calls.clear()
            result=ec.write_rate(None,None,replace(state,config=actual),2000)
            self.assertIn((0x29,0,b'\x04',None),calls)
            self.assertEqual(result.config.raw,el.he.read(None,m).raw)

    def test_logitech_advertised_modes_and_readback_layout(self):
        s=fixture('logitech',identity=(0x46D,0xC539,1,0x2201,7))
        s=replace(s,base=replace(s.base,identity=(*s.base.identity,b'unit',10,2)))
        def call(dev,index,fi,fn,args=()):
            if fn==0:return bytes([1,0,0,0,1])+bytes(11)
            if fn==1:return bytes([0,0,2,2])+bytes(12)
            if fn==2:return bytes([0,args[1],0,args[1]])+bytes(12)
            if fn==14:return bytes([0,1,255,128,0])+bytes(11)
        with patch.object(dx,'feature_index',side_effect=lambda dev,i,f:12 if f==0x8070 else 0),patch.object(dx,'logitech_call',side_effect=call):
            zones,raw=el.read(None,None,s)
        self.assertEqual(zones[0].rgb,(255,128,0));self.assertEqual(zones[0].modes,((0,'关闭'),(1,'常亮')))
        with patch.object(dx,'logitech_call') as call:
            result=el.write(None,None,ec.State(s,zones=zones,light_raw=raw),replace(zones[0],rgb=(1,2,3)))
            call.assert_called_with(None,1,12,3,bytes([0,1,1,2,3])+bytes(7))
            self.assertEqual(result.light_raw,bytes([1,1,2,3])+bytes(7))

    def test_logitech_write_only_keeps_unknown_and_never_switches_profile_mode(self):
        s=fixture('logitech',identity=(0x46D,0xC539,1,0x2201,7))
        s=replace(s,base=replace(s.base,identity=(*s.base.identity,b'unit',10,2)))
        def call(dev,index,fi,fn,args=()):
            self.assertEqual(fn,0)
            if args==(255,255,0):return bytes([0,0,1,0,0])+bytes(11)
            if args==(0,255,0):return bytes([0,0,5,2])+bytes(12)
            return bytes([0,args[1],0,args[1]])+bytes(12)
        with patch.object(dx,'feature_index',return_value=12),patch.object(dx,'logitech_call',side_effect=call):
            zones,raw=el.read(None,None,s)
        self.assertTrue(zones[0].write_only);self.assertEqual(zones[0].mode,-1);self.assertEqual(zones[0].rgb,(-1,-1,-1))
        state=ec.State(s,zones=zones,light_raw=raw)
        wanted=replace(zones[0],mode=1,rgb=(20,40,60))
        ec.validate_zone(zones[0],wanted)
        with patch.object(dx,'logitech_call') as call:
            result=el.write(None,None,state,wanted)
        self.assertEqual(result,state)
        self.assertEqual([c.args[3] for c in call.call_args_list],[5,1])
        self.assertEqual(call.call_args_list[0].args[-1],(1,3,4))
        self.assertEqual(call.call_args_list[1].args[-1],bytes([0,1,20,40,60,2])+bytes(6)+b'\x01')

    def test_logitech_animation_fields(self):
        s=fixture('logitech',identity=(0x46D,0xC539,1,0x2201,7))
        z=ec.Zone(0,'RGB',1,((1,'常亮'),(3,'颜色循环'),(10,'单色呼吸')),(10,20,30),
                  speed=-1,speeds=el.PERIODS,aux=bytes([12,1,2,3,4,10,5]),channel=0x8070)
        for mode in (3,10):
            wanted=replace(z,mode=mode,speed=3000)
            with patch.object(dx,'logitech_call') as call:
                result=el.write(None,None,ec.State(s,zones=(z,),light_raw=bytes(11)),wanted)
            self.assertEqual(result.zones[0].mode,mode);self.assertEqual(result.zones[0].speed,3000)
            self.assertEqual(call.call_args.args[-1][:2],bytes([0,4 if mode==3 else 5]))
            self.assertEqual(result.zones[0].rgb,(10,20,30) if mode==10 else (-1,-1,-1))

    def test_egg_lift_led_preserves_sensor_and_dpi_block(self):
        raw=bytearray(range(140));raw[24]=1
        s=fixture('egg',raw=raw);s=replace(s,base=replace(s.base,identity=(0x3367,0x1984,0x1984)))
        zones,_=el.read(None,None,s)
        with patch.object(ec,'egg_write') as write:
            result=el.write(None,None,ec.State(s,zones=zones),replace(zones[0],mode=0))
        payload=write.call_args.args[2]
        self.assertEqual(write.call_args.args[1],0x14);self.assertEqual(len(payload),28)
        self.assertEqual(payload[8:],raw[51:71]);self.assertEqual(payload[1],0)
        self.assertEqual([i for i,(a,b) in enumerate(zip(s.raw,result.config.raw)) if a!=b],[24])

    def test_logitech_board_mode_blocks_lighting_before_claim(self):
        s=fixture('logitech');s=replace(s,base=replace(s.base,identity=(1,2,1)))
        with patch.object(dx,'logitech_call') as call:
            with self.assertRaisesRegex(ValueError,'板载模式'):el.read(None,None,s)
            call.assert_not_called()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.state=ec.State(fixture('a5'),1000,((500,2),(1000,1)))
        self.enterContext(patch.object(ec,'device_session',autospec=True,side_effect=lambda: nullcontext()))
        self.enterContext(patch.object(dx,'interface',return_value=SimpleNamespace(path='fake')))
        self.enterContext(patch.object(pc.hw,'HidDevice',return_value=nullcontext(None)))

    def test_invalid_rate_and_zone_rejected_before_io(self):
        with patch.object(ec,'_read') as read:
            for value in (True,8000,500.0):
                with self.assertRaises(ValueError):ec.apply(self.state,rate=value)
            with self.assertRaises(ValueError):ec.apply(self.state,zone=ec.Zone(0,'unknown',0))
            read.assert_not_called()

    def test_public_read_acquires_transaction_lock(self):
        with patch.object(ec,'_read',return_value=self.state) as read:
            self.assertEqual(ec.read(self.state.config.base.mouse),self.state)
        ec.device_session.assert_called_once_with()
        read.assert_called_once_with(self.state.config.base.mouse)

    def test_stale_snapshot_never_writes(self):
        stale=replace(self.state,config=replace(self.state.config,active=1))
        with patch.object(ec,'_read',return_value=stale),patch.object(ec,'write_rate') as write:
            with self.assertRaisesRegex(ValueError,'已变化'):ec.apply(self.state,rate=500)
            write.assert_not_called()

    def test_postwrite_changes_rejected_without_retry(self):
        expected=replace(self.state,rate=500)
        changed=replace(expected,config=replace(expected.config,stages=((3200,3200),(1600,1600))))
        with patch.object(ec,'_read',side_effect=[self.state,changed]),patch.object(ec,'write_rate',return_value=expected) as write:
            with self.assertRaisesRegex(ValueError,'未确认完整配置'):ec.apply(self.state,rate=500)
            write.assert_called_once()

    def test_write_timeout_not_retried(self):
        with patch.object(ec,'_read',return_value=self.state),patch.object(ec,'write_rate',side_effect=TimeoutError) as write:
            with self.assertRaises(TimeoutError):ec.apply(self.state,rate=500)
            write.assert_called_once()

    def test_success_and_noop(self):
        expected=replace(self.state,rate=500)
        with patch.object(ec,'_read',side_effect=[self.state,expected]),patch.object(ec,'write_rate',return_value=expected):
            self.assertEqual(ec.apply(self.state,rate=500),expected)
        with patch.object(ec,'_read',return_value=self.state),patch.object(ec,'write_rate') as write:
            self.assertEqual(ec.apply(self.state,rate=1000),self.state);write.assert_not_called()

    def test_lighting_can_be_written_when_rate_is_unavailable(self):
        old=ec.Zone(0,'Logo',0,((0,'常亮'),(1,'呼吸')),(10,20,30),50,100)
        state=replace(self.state,rate=None,codes=(),zones=(old,))
        wanted=replace(old,rgb=(30,20,10));expected=replace(state,zones=(wanted,))
        with patch.object(ec,'_read',side_effect=[state,expected]),patch.object(el,'write',return_value=expected) as write:
            self.assertEqual(ec.apply(state,zone=wanted),expected)
            write.assert_called_once()

    def test_light_failure_does_not_remove_rate(self):
        with patch.object(dx,'read',return_value=self.state.config),patch.object(ec,'rate_info',return_value=(1000,self.state.codes,(),'')),patch.object(el,'read',side_effect=TimeoutError('灯光超时')):
            result=ec._read(self.state.config.base.mouse)
        self.assertEqual(result.rate,1000);self.assertEqual(result.codes,self.state.codes);self.assertIn('超时',result.light_error)

    def test_zone_validation_blocks_unadvertised_values(self):
        old=ec.Zone(1,'滚轮',0,el.MODES,(1,2,3),4,4)
        for new in (replace(old,mode=3),replace(old,brightness=100),replace(old,rgb=(0,0,256)),replace(old,key=2)):
            with self.assertRaises(ValueError):ec.validate_zone(old,new)
        color=ec.Zone(0,'指示颜色',0,rgb=(0,0,0))
        with self.assertRaises(ValueError):ec.validate_zone(color,replace(color,mode=1))


if __name__=='__main__':unittest.main()
