"""Model/feature-gated DPI channels outside the rate/light catalog.

Protocol references and validation limits are recorded in 适配说明-v31.md.
No defaults are substituted for missing configuration replies.
"""
from dataclasses import replace
import time
import hid_win as hw
import peripheral_controls as pc
import rog_dpi_models as rog
import dpi_more as more
import dpi_hecate as hecate
import dpi_pulsar as pulsar

AJAZZ = {0x2157:('NJ07',12800),0x2158:('NJ08',12800),
         0x2167:('NJ07 MC',24000),0x2168:('NJ08 MC',24000),
         0x2177:('NJ07 MC Pro',24000),0x2178:('NJ08 MC Pro',24000)}
EGG = {0x1972:'OP1w 4K',0x1968:'XM2w 4K',0x1984:'OP1w 4K v2',0x1982:'XM2w 4K v2'}


def family(d):
    if pulsar.matches(d):return 'pulsar'
    if hecate.matches(d):return 'hecate'
    if more.family(d):return more.family(d)
    if d.vid==0x3554 and d.pid in (0xF58A,0xF58C) and (d.usage_page,d.usage,d.output_len)==(0xFF02,2,17) and d.input_len>=17:return 'vxe'
    if d.vid==0x3554 and d.pid in (0xF50F,0xF50D,0xF510,0xF517) and (d.usage_page,d.usage,d.output_len)==(0xFF02,2,17) and d.input_len>=17:return 'atlantis'
    if rog.match(d):return 'rog_more'
    if d.vid==0x2023 and d.pid in (0xF013,0xF015,0xF019) and d.usage_page==0xFFFF and d.feature_len==65:return 'a5'
    if d.vid==0x3367 and d.pid in (*EGG,0x1970) and (d.usage_page,d.usage)==(0xFF01,2) and d.feature_len==1041:return 'egg'
    if d.vid in (0xA8A4,0xA8A5) and d.pid in AJAZZ and (d.usage_page,d.usage,d.output_len)==(0xFF01,0x10,64) and d.input_len>=64:return 'ajazz'
    if (d.vid,d.pid,d.usage_page,d.output_len,d.input_len)==(0x36A7,0xA887,0xFF1C,64,64):return 'wl4k'
    if d.vid==0x046D and d.usage_page>=0xFF00 and d.output_len>=20 and d.input_len>=20:return 'logitech'
    return ''


def discover(ifaces,paired=None):
    from mouse_settings import Mouse
    found=[];seen=set()
    for d in ifaces:
        f=family(d)
        if not f:continue
        if f=='rog_more':
            pid=(paired or {}).get(hw.physical_group_key(d.path)) if d.pid==0x1ACE else d.pid
            if pid not in rog.MODELS:continue
            found.append(Mouse(d.path,rog.MODELS[pid].name,d.vid_pid,'dpi',(d.vid,d.pid,pid),interface=d))
        elif f=='logitech':
            group=hw.physical_group_key(d.path)
            if group in seen:continue
            # Resolve receiver slots independently; a keyboard never becomes a DPI device.
            try:
                with hw.HidDevice(d.path,write=True) as dev:
                    for index in (255,1,2,3,4,5,6):
                        feature,fi=logitech_feature(dev,index)
                        if not fi:continue
                        import battery
                        name=battery._hidpp_get_device_name(dev,0x11,index) or 'Logitech HID++ 鼠标'
                        found.append(Mouse(d.path,name,d.vid_pid,'dpi',(d.vid,d.pid,index,feature,fi),interface=d))
                        if index==255:break
                seen.add(group)
            except OSError:continue
        else:
            name='Pulsar report-8 鼠标' if f=='pulsar' else 'HECATE G3M Pro' if f=='hecate' else 'RK Royal Kludge M3' if f=='rk' else 'Scyrox V8' if f=='scyrox' else 'AJAZZ '+AJAZZ[d.pid][0] if f=='ajazz' else 'Endgame Gear '+EGG.get(d.pid,'4K 接收器') if f=='egg' else 'MCHOSE A5 Pro Max 第一代' if f=='a5' else 'LAMZU Atlantis 协议系列' if f=='atlantis' else 'VXE R1 Pro Max（待读取确认）' if f=='vxe' else 'WLmouse Beast X 4K'
            found.append(Mouse(d.path,name,d.vid_pid,'dpi',(d.vid,d.pid),interface=d))
    return found


def interface(mouse):
    d=next((d for d in hw.enumerate_hid() if d.path==mouse.path),None)
    if d is None:raise OSError('设备已断开')
    if (d.vid,d.pid)!=mouse.identity[:2] or not family(d):raise ValueError('DPI 配置接口已变化')
    return d


def input_exchange(dev,packet,accept):
    dev.drain();dev.write_output(packet,use_hidd=False)
    until=time.monotonic()+1.2
    while time.monotonic()<until:
        raw=dev.read_input(timeout_ms=100)
        if raw and accept(raw):return bytes(raw)
    raise TimeoutError('未收到匹配的 DPI 配置回复')


def ajazz_read(dev):
    r=input_exchange(dev,bytes.fromhex('f00e010b2e').ljust(64,b'\0'),
                     lambda r:len(r)==64 and r[:2]==b'\xf0\x0e')[1:]
    if r[13:16] in (b'\0'*3,b'\xff'*3):raise ValueError('黑爵返回空白配置，不能用预设值代替设备数据')
    if not 1<=r[10]<=6 or not 1<=r[11]<=r[10] or not 1<=r[9]<=4:raise ValueError('黑爵档位配置无效')
    # Only fields in the documented SET_CONFIG layout enter the snapshot.
    normalized=bytearray(63);normalized[8:24]=r[8:24];normalized[47:54]=r[47:54]
    return bytes(normalized)


def atlantis_query(dev,d,cmd,address=0,data=b'',length=None):
    import mouse_settings as ms
    n=len(data) if length is None else length
    p=ms.atk_frame(cmd,(0,address>>8,address&255,n,*data))
    r=input_exchange(dev,p,lambda r:len(r)>=17 and r[:2]==bytes([8,cmd]) and sum(r[:17])&255==0x55
                     and (cmd not in (7,8) or r[3:5]==address.to_bytes(2,'big')))
    if r[2]:raise ValueError('LAMZU 拒绝配置命令')
    if cmd==8 and r[5]!=n:raise ValueError('LAMZU 配置块长度不匹配')
    return bytes(r[6:6+n] if cmd==8 else r[6:16])


def atlantis_read(dev,d,mouse):
    from dpi_adapters import Config,validate
    import mouse_settings as ms
    profile=atlantis_query(dev,d,14)[0]
    if profile>4:raise ValueError('LAMZU 配置层无效')
    header=atlantis_query(dev,d,8,0,length=6)
    if any(sum(header[i:i+2])&255!=0x55 for i in (0,2,4)) or not 1<=header[2]<=8 or header[4]>=header[2]:raise ValueError('LAMZU DPI 表头校验失败')
    rows=[atlantis_query(dev,d,8,12+i*4,length=4) for i in range(header[2])]
    if any(row[2]&0x33 for row in rows):raise ValueError('未知 LAMZU DPI 编码，未套用其他传感器公式')
    pairs=tuple(ms.atk_decode(row,'step50') for row in rows)
    if atlantis_query(dev,d,14)[0]!=profile:raise ValueError('LAMZU 配置层已变化')
    base=pc.State(mouse,mouse.name,None,(),profile+1,mouse.identity)
    return validate(Config(base,'atlantis',pairs,header[4],50,26000,50,header+b''.join(rows),
                           note='此共享硬件标识不能区分 Atlantis、Thorn、Maya、Paro 等同代型号。'))


def vxe_read(dev,d,mouse):
    import mouse_settings as ms
    from dpi_adapters import Config,validate
    ident=ms.atk_identity(dev,d)
    if ident!=(2,27):raise ValueError('当前共享接口没有确认是 VXE R1 Pro Max')
    profile=ms.atk_profile(dev,d)
    header=ms.atk_read(dev,d,0,10)
    if any(sum(header[i:i+2])&255!=0x55 for i in (0,2,4)) or not 1<=header[2]<=8 or header[4]>=header[2]:raise ValueError('VXE DPI 表头校验失败')
    # Read all eight rows so the neighbour in the receiver's paired write is preserved.
    table=b''.join(ms.atk_read(dev,d,12+i*8,8) for i in range(4))
    pairs=tuple(ms.atk_decode(table[i*4:i*4+4],'step50') for i in range(header[2]))
    if ms.atk_read(dev,d,0,10)!=header:raise ValueError('读取期间 VXE 配置已变化')
    if ms.atk_profile(dev,d)!=profile:raise ValueError('读取期间 VXE 配置层已变化')
    base=pc.State(mouse,'VXE R1 Pro Max',None,(),profile,(*mouse.identity,*ident))
    return validate(Config(base,'vxe',pairs,header[4],100,30000,50,header+table))


def vxe_write_row(dev,d,address,data):
    import mouse_settings as ms
    packet=ms.atk_frame(7,(0,address>>8,address&255,len(data),*data))
    if d.pid==0xF58A:
        ms.exchange(dev,d,packet,lambda r:len(r)>=17 and r[:3]==bytes([8,7,0]) and sum(r[:17])&255==0x55
                    and r[3:5]==address.to_bytes(2,'big') and r[5]==len(data) and r[6:6+len(data)]==data)
    else:
        dev.write_output(packet,use_hidd=False);time.sleep(.08)


def crc16(data):
    crc=65535
    for value in data:
        crc^=value
        for _ in range(8):crc=(crc>>1)^0xA001 if crc&1 else crc>>1
    return crc


def egg_command(dev,cmd,header=b'',payload=b''):
    packet=bytearray(64);packet[:2]=bytes([0xA1,cmd]);packet[2:2+len(header)]=header;packet[16:16+len(payload)]=payload
    dev.set_feature_report(bytes(packet));time.sleep(.15)


def egg_read(dev,mouse):
    from dpi_adapters import Config
    pid=mouse.identity[1]
    if pid==0x1970:
        egg_command(dev,14)
        for _ in range(5):
            r=dev.get_feature_report(0xA1,64)
            if len(r)==64 and r[:2]==b'\xa1\x01' and r[16:18]==b'\x67\x33':
                pid=int.from_bytes(r[18:20],'little');break
            time.sleep(.1)
        if pid not in EGG:raise ValueError('未确认 Endgame 接收器配对的鼠标型号')
    egg_command(dev,0x12)
    raw=None
    for _ in range(8):
        for rid in (0xA1,0xA0):
            r=dev.get_feature_report(rid,1041)
            if len(r)==1041 and r[0]==rid and r[1]==1 and 1<=r[30]<=4 and r[21]:
                # Settings begin at wire offset16; header/status and padding are not settings.
                raw=bytes(16)+bytes(r[16:131]);break
        if raw is not None:break
        time.sleep(.08)
    if raw is None:raise ValueError('Endgame 未返回有效的配置块')
    stages=tuple((int.from_bytes(raw[52+i*5:54+i*5],'little'),int.from_bytes(raw[54+i*5:56+i*5],'little')) for i in range(raw[30]))
    v2=pid in (0x1982,0x1984)
    options=tuple(range(10,10001,10))+tuple(range(10050,30001,50)) if v2 else tuple(range(50,26001,50))
    if any(v not in options for pair in stages for v in pair):raise ValueError('Endgame CPI 档位超出传感器范围')
    base=pc.State(mouse,'Endgame Gear '+EGG[pid],None,(),0,(*mouse.identity,pid))
    return Config(base,'egg',stages,-1,min(options),max(options),10 if v2 else 50,raw,
                  note='可修改每个已启用档位；设备没有返回当前档位索引，切换档位请使用鼠标按键。',options=options,can_activate=False)


def wl_query(dev,cmd,address=0,data=b'',length=None):
    n=len(data) if length is None else length
    if not 0<=n<=24 or len(data)>n:raise ValueError('Beast X 4K 数据长度无效')
    p=bytearray(64);p[0]=4;p[3]=cmd;p[4]=n;p[5:7]=address.to_bytes(2,'little');p[8:8+len(data)]=data
    p[1:3]=crc16(p[3:32]).to_bytes(2,'little')
    def valid(r):
        return len(r)==64 and r[0]==4 and r[3]==cmd and r[5:7]==p[5:7] and r[4]<=24 and int.from_bytes(r[1:3],'little')==crc16(r[3:32])
    r=input_exchange(dev,bytes(p),valid)
    if r[7] in (254,255):raise ValueError('Beast X 4K 拒绝配置命令')
    if cmd==5 and r[4]!=n:raise ValueError('Beast X 4K 配置块不完整')
    return r[8:8+r[4]]


def wl_read(dev):
    profile=wl_query(dev,5,length=1)[0]
    if profile>7:raise ValueError('Beast X 4K 配置编号无效')
    raw=b''.join(wl_query(dev,5,(profile<<7)+i,length=min(24,117-i)) for i in range(0,117,24))
    if wl_query(dev,5,length=1)[0]!=profile:raise ValueError('读取期间板载配置已变化')
    if not 1<=raw[9]<=8 or raw[12]>=raw[9]:raise ValueError('Beast X 4K DPI 档位无效')
    return profile,raw


def logitech_call(dev,index,fi,fn,args=(),rid=0x11):
    import battery
    r=battery._hidpp_call(dev,rid,index,fi,fn,list(args),timeout_ms=450)
    if not r:raise TimeoutError('罗技 HID++ 未响应')
    if battery._hidpp_error(r) is not None:raise ValueError('罗技拒绝此 DPI 功能请求')
    if len(r)<7:raise ValueError('罗技回复不完整')
    return bytes(r[4:])


def feature_index(dev,index,fid):
    import battery
    return battery._hidpp_feature_index(dev,0x11,index,fid)


def logitech_feature(dev,index):
    for fid in (0x2202,0x2201):
        fi=feature_index(dev,index,fid)
        if fi:return fid,fi
    return 0,0


def dpi_options(raw):
    values=[];i=0
    while i+1<len(raw):
        v=int.from_bytes(raw[i:i+2],'big');i+=2
        if not v:break
        if v&0xE000==0xE000:
            step=v&0x1FFF
            high=int.from_bytes(raw[i:i+2],'big');i+=2
            if not values or not step or high<=values[-1] or (high-values[-1])%step:raise ValueError('罗技 DPI 范围编码无效')
            values.extend(range(values[-1]+step,high+1,step))
        else:values.append(v)
    if not values or values!=sorted(set(values)):raise ValueError('罗技没有返回有效 DPI 范围')
    return tuple(values)


def logitech_read(dev,mouse):
    from dpi_adapters import Config
    index,fid,fi=mouse.identity[2:]
    if logitech_feature(dev,index)!=(fid,fi):raise ValueError('罗技配对设备或功能表已变化')
    identity_f=feature_index(dev,index,3)
    if not identity_f:raise ValueError('此罗技设备缺少可核验的设备身份功能')
    unit=logitech_call(dev,index,identity_f,0)
    if len(unit)<13 or not any(unit[1:5]):raise ValueError('未能确认罗技设备唯一身份')
    pf=feature_index(dev,index,0x8100)
    mode=logitech_call(dev,index,pf,2)[0] if pf else 0
    if pf and mode not in (1,2):raise ValueError('未知罗技板载模式')
    if fid==0x2201:
        options=dpi_options(logitech_call(dev,index,fi,1,(0,))[1:])
        raw=logitech_call(dev,index,fi,2,(0,))
        if raw[0]!=0:raise ValueError('罗技传感器回复不匹配')
        x=int.from_bytes(raw[1:3],'big');y=x
        raw=raw[:5]
    else:
        parts=[]
        for page in range(32):
            reply=logitech_call(dev,index,fi,2,(0,0,page))
            if len(reply)<16 or reply[0]!=0:raise ValueError('罗技 DPI 列表响应无效')
            part=reply[3:];parts.extend(part)
            if any(parts[i:i+2]==[0,0] for i in range(0,len(parts)-1,2)):break
        else:raise ValueError('罗技 DPI 列表未结束')
        options=dpi_options(bytes(parts))
        raw=logitech_call(dev,index,fi,5)
        if len(raw)<13 or raw[0]!=0:raise ValueError('罗技 DPI 设置响应无效')
        raw=raw[:13];x=int.from_bytes(raw[1:3],'big');y=int.from_bytes(raw[5:7],'big')
    if x not in options or y not in options:raise ValueError('当前 DPI 不在设备上报列表中')
    ident=(*mouse.identity,bytes(unit[:13]),pf or 0,mode)
    base=pc.State(mouse,mouse.name,None,(),0,ident)
    note='只调整当前 DPI，不改写板载档位表；断电后的保留行为由固件决定。'
    if mode==1 and index!=255:note+=' 当前为板载模式；请先在原厂软件切换到软件控制模式后重新读取。'
    return Config(base,'logitech',((x,y),),0,min(options),max(options),1,raw,options=options,note=note,can_activate=False)


def read(mouse):
    from dpi_adapters import Config,validate
    d=interface(mouse);f=family(d)
    with hw.HidDevice(d.path,write=True) as dev:
        if f=='pulsar':return pulsar.read(dev,d,mouse)
        if f=='hecate':return hecate.read(dev,mouse)
        if f=='rk':return more.rk_read(dev,mouse)
        if f=='scyrox':return more.scyrox_read(dev,d,mouse)
        if f=='vxe':return vxe_read(dev,d,mouse)
        if f=='atlantis':return atlantis_read(dev,d,mouse)
        if f=='rog_more':return rog.read(dev,d,mouse)
        if f=='logitech':return validate(logitech_read(dev,mouse))
        if f=='egg':return validate(egg_read(dev,mouse))
        if f=='a5':
            profile=pc._profile(dev,2)
            if profile>3:raise ValueError('未知 A5 板载配置')
            raw=pc.page(dev,2,1,0x81,10,(profile,6),full=True)
            if len(raw)<26 or raw[0]!=profile or not 1<=raw[1]<=6:raise ValueError('A5 DPI 表无效')
            raw=raw[:2+4*raw[1]]
            active=pc.page(dev,2,1,0x82,2,(profile,))
            if len(active)!=2 or active[0]!=profile or pc._profile(dev,2)!=profile:raise ValueError('A5 档位或配置已变化')
            pairs=tuple((int.from_bytes(raw[i:i+2],'big'),int.from_bytes(raw[i+2:i+4],'big')) for i in range(2,len(raw),4))
            base=pc.State(mouse,mouse.name,None,(),profile,mouse.identity)
            return validate(Config(base,f,pairs,active[1]-1,50,26000,50,raw))
        if f=='ajazz':
            raw=ajazz_read(dev);profile=0
            stages=tuple((v,v) for i in range(raw[10]) for v in [int.from_bytes(raw[12+2*i:14+2*i],'little')])
            active=raw[11]-1;maximum=AJAZZ[d.pid][1]
        else:
            profile,raw=wl_read(dev);stages=[]
            for i in range(raw[9]):
                at=14+i*9
                if raw[at+1] not in (0,1):raise ValueError('未知 DPI 分轴标记')
                x=int.from_bytes(raw[at+2:at+4],'little')
                y=int.from_bytes(raw[at+4:at+6],'little') if raw[at+1] else x
                stages.append((x,y))
            active=raw[12];maximum=26000
        base=pc.State(mouse,mouse.name,None,(),profile+1,mouse.identity)
        return validate(Config(base,f,tuple(stages),active,50,maximum,50,raw))


def write(dev,d,s,stage,value,activate):
    if s.family=='pulsar':return pulsar.write(dev,d,s,stage,value,activate)
    if s.family=='hecate':return hecate.write(dev,d,s,stage,value,activate)
    if s.family in ('rk','scyrox'):return more.write(dev,d,s,stage,value,activate)
    if s.family=='rog_more':return rog.write(dev,d,s,stage,value,activate)
    raw=bytearray(s.raw);pairs=list(s.stages);active=stage if activate else s.active
    x,y=pairs[stage]
    if value is not None:pairs[stage]=(value,value if x==y else y)
    if s.family=='vxe':
        import mouse_settings as ms
        if value is not None:
            row=ms.atk_encode(*pairs[stage],'step50');offset=10+stage*4;raw[offset:offset+4]=row
            if d.pid==0xF58A:
                group=stage//2*8;vxe_write_row(dev,d,12+group,bytes(raw[10+group:18+group]))
            else:vxe_write_row(dev,d,12+stage*4,row)
        if active!=s.active:
            raw[4:6]=bytes([active,(0x55-active)&255])
            vxe_write_row(dev,d,0,bytes(raw[:10])) if d.pid==0xF58A else vxe_write_row(dev,d,4,bytes(raw[4:6]))
    elif s.family=='atlantis':
        import mouse_settings as ms
        if value is not None:
            if x!=y:raise ValueError('此 LAMZU 分轴写入尚未核实，请通过原厂软件修改')
            row=ms.atk_encode(value,value,'step50')
            atlantis_query(dev,d,7,12+stage*4,row);raw[6+stage*4:10+stage*4]=row
        if active!=s.active:
            pair=bytes([active,(0x55-active)&255]);atlantis_query(dev,d,7,4,pair);raw[4:6]=pair
    elif s.family=='a5':
        if value is not None:
            raw[2+stage*4:6+stage*4]=pairs[stage][0].to_bytes(2,'big')+pairs[stage][1].to_bytes(2,'big')
            pc.page(dev,2,1,1,len(raw),raw)
        if active!=s.active:pc.page(dev,2,1,2,2,(s.base.profile,active+1))
    elif s.family=='ajazz':
        if value is not None:raw[12+stage*2:14+stage*2]=value.to_bytes(2,'little')
        raw[11]=active+1
        packet=bytearray(raw);packet[:4]=bytes.fromhex('0f010a2f')
        dev.drain();dev.write_output(b'\xf0'+packet,use_hidd=False);time.sleep(.5)
    elif s.family=='wl4k':
        if value is not None:
            at=14+stage*9;raw[at+2:at+4]=value.to_bytes(2,'little')
            if raw[at+1]:pairs[stage]=(value,y)
        raw[12]=active
        wl_query(dev,1)
        try:
            for i in range(0,117,24):
                chunk=bytes(raw[i:i+24])
                if chunk!=s.raw[i:i+24]:wl_query(dev,6,((s.base.profile-1)<<7)+i,chunk)
        finally:wl_query(dev,2)
    elif s.family=='egg':
        raw[52+stage*5:56+stage*5]=pairs[stage][0].to_bytes(2,'little')+pairs[stage][1].to_bytes(2,'little')
        payload=bytes(raw[i] for i in (23,24,25,26,27,17,30,29))+raw[51:71]
        egg_command(dev,0x14,bytes([15,28,0,0,0]),payload)
        for _ in range(8):
            r=dev.get_feature_report(0xA1,64)
            if len(r)==64 and r[:2]==b'\xa1\x01':break
            if len(r)==64 and r[1] not in (0,3):raise ValueError('Endgame 拒绝 CPI 配置')
            time.sleep(.08)
        else:raise TimeoutError('Endgame 未确认 CPI 写入')
    elif s.family=='logitech':
        index,fid,fi=s.base.mouse.identity[2:]
        if s.base.identity[-1]==1 and index!=255:raise ValueError('当前为板载模式，请先在原厂软件切换为软件控制模式')
        if value not in s.options:raise ValueError('该 DPI 不在设备上报的可用值中')
        raw[1:3]=value.to_bytes(2,'big')
        if fid==0x2201:
            logitech_call(dev,index,fi,3,bytes([0])+raw[1:3],rid=0x10 if index==255 else 0x11)
        else:
            raw[5:7]=pairs[stage][1].to_bytes(2,'big')
            logitech_call(dev,index,fi,6,bytes([0])+raw[1:3]+raw[5:7]+raw[9:10])
    return replace(s,stages=tuple(pairs),active=active,raw=bytes(raw))
