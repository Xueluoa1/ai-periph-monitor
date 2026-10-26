"""Windows mouse DPI adapters. Discovery never writes device settings.

Protocol facts: G-Helper AsusMouse / HarpeAceAimLab; OpenMouse ATK; AM Master AM35.
Only explicitly recognized identities are writable; no brand-name fallback.
"""
from dataclasses import dataclass, field
import time

import hid_win as hw
import am_battery
import am_dpi
import gravastar_k1 as k1
import atk_r1_nk as r1nk
import xiaomi_mouse2 as xm
from device_io import device_session


@dataclass
class Mouse:
    path: str
    name: str
    hardware: str
    protocol: str = ''
    identity: tuple = ()
    reason: str = ''
    interface: object = field(default=None, repr=False)


@dataclass
class DpiState:
    mouse: Mouse
    stages: list
    active: int
    minimum: int
    maximum: int
    step: int
    profile: int = 1
    firmware: str = ''
    colors: list = field(default_factory=list)
    protocol_data: object = field(default=None, repr=False)


ATK_MODELS = {
    (1,8): ('ATK F1 Ultimate 2.0', 'ultra', 10, 42000),
    (2,39): ('ATK X1 Pro Max', 'step50', 50, 36000),
    (2,43): ('ATK X1 Ultra', 'ultra', 10, 42000),
    (2,83): ('ATK A9 Plus NearLink', 'step50', 100, 30000),
    r1nk.IDENTITY: (r1nk.MODEL, 'paw3395se', r1nk.MIN_DPI, r1nk.MAX_DPI),
}


def atk_model(identity, iface):
    # X1 Ultra: official USB identity plus the two shared ATK receivers.
    # A shared receiver PID alone never identifies the mouse behind it.
    if identity == (2,43) and (iface.vid != 0x373B or iface.pid not in (0x102F,0x101A,0x101B)):
        return None
    if identity == r1nk.IDENTITY and not r1nk.receiver(iface):
        return None
    return ATK_MODELS.get(identity)


def exchange(dev, iface, packet, accept):
    if len(packet) > iface.output_len:
        raise ValueError('设备接口长度不匹配')
    dev.drain()
    dev.write_output(packet + bytes(iface.output_len-len(packet)), use_hidd=False)
    deadline = time.monotonic()+0.9
    while time.monotonic() < deadline:
        reply = dev.read_input(timeout_ms=100)
        if reply and accept(reply):
            return reply
    raise TimeoutError('设备未回复，请唤醒鼠标并检查接收器；也可关闭其他设备配置软件后重试')


def rog_query(dev, iface, rid, command):
    prefix = bytes([rid, *command])
    match = prefix[:3] if command[0] in (0x50,0x51) else prefix
    failure=bytes([rid,0xFF,0xAA])
    reply=exchange(dev, iface, prefix, lambda r: r.startswith(match) or r.startswith(failure))
    if reply.startswith(failure):
        raise OSError('设备返回配置访问错误（FF AA），请确认鼠标处于对应接收器的 2.4G 模式且已唤醒')
    return reply


def omni_identity(ifaces):
    result = {}
    management = [d for d in ifaces if (d.vid,d.pid,d.usage_page)==(0x0B05,0x1ACE,0xFF02)]
    if len(management)>1:
        raise ValueError('检测到多个同型号 Omni 接收器，请仅保留要调整的接收器后重试')
    for iface in ifaces:
        if (iface.vid, iface.pid, iface.usage_page) != (0x0B05,0x1ACE,0xFF02):
            continue
        with hw.HidDevice(iface.path, write=True) as dev:
            reply = rog_query(dev, iface, 1, (0xA0,0))
        if len(reply) < 5:
            continue
        count = int.from_bytes(reply[3:5], 'little')
        if count > 8 or len(reply) < 5+count*4:
            raise ValueError('接收器配对信息无效')
        for i in range(count):
            offset = 5+i*4
            if reply[offset+2] == 3:
                result[hw.physical_group_key(iface.path)] = int.from_bytes(reply[offset:offset+2], 'little')
    return result


def atk_frame(command, payload=()):
    body = bytes([command, *payload])
    if len(body) > 15:
        raise ValueError('ATK 数据过长')
    body += bytes(15-len(body))
    return bytes([8])+body+bytes([(0x55-8-sum(body))&255])


def atk_exchange(dev, iface, command, payload=(), predicate=lambda r: True):
    raw = exchange(dev, iface, atk_frame(command, payload),
                   lambda r: len(r)>=17 and r[0]==8 and
                   (sum(r[:17])&255)==0x55 and predicate(r[1:17]))
    return raw[1:17]


def atk_identity(dev, iface):
    # Only the read-only identity query is retried; setting writes are not.
    for attempt in range(3):
        try:
            r = atk_exchange(dev, iface, 0x10,
                             predicate=lambda r: r[0]==0x10 and r[1]==0 and 2<=r[4]<=10)
            return (r[5],r[6])
        except TimeoutError:
            if attempt == 2:
                raise
            time.sleep(.08)


def atk_read(dev, iface, address, count):
    r = atk_exchange(dev, iface, 8, (0,address>>8,address&255,count),
        lambda r:r[0] in (7,8) and r[1]==0 and r[2:4]==address.to_bytes(2,'big') and r[4]>=count)
    return r[5:5+count]


def atk_profile(dev, iface):
    # GetCurrentConfig: the first payload byte is a zero-based bank index.
    # The bank count varies by model; retain the identity without assuming the
    # four-bank R1 limit applies to every ATK sensor/receiver combination.
    r = atk_exchange(dev, iface, 0x0E,
        predicate=lambda r: r[0] == 0x0E and r[1] == 0 and 1 <= r[4] <= 10)
    return r[5] + 1


def atk_axis(value, mode, family):
    if family == 'paw3395se':
        return r1nk.decode_axis(value,mode)
    count = ((mode>>2)&3)*256+value
    base = ((10050+count*50) if mode&2 else (count+1)*10) if family=='ultra' else (count+1)*50
    return base*(2 if mode&1 else 1)


def atk_decode(data, family):
    if len(data)!=4 or sum(data)&255 != 0x55:
        raise ValueError('DPI 数据校验失败')
    return (atk_axis(data[0],data[2]&15,family), atk_axis(data[1],data[2]>>4,family))


def atk_encode(x, y, family):
    def axis(value):
        if family == 'paw3395se':
            return r1nk.encode_axis(value)
        doubled = int(value>30000)
        base = value//2 if doubled else value
        if family=='ultra':
            count = (base-10050)//50 if base>10000 else base//10-1
            mode = ((count>>8)<<2) | (2 if base>10000 else 0) | doubled
        else:
            count = base//50-1
            mode = ((count>>8)<<2) | doubled
        return count&255,mode
    a,ma = axis(x); b,mb = axis(y)
    body = bytes([a,b,ma|(mb<<4)])
    result = body+bytes([(0x55-sum(body))&255])
    if atk_decode(result,family)!=(x,y):
        raise ValueError('该传感器不能精确表示此 DPI，请调整数值')
    return result


def discover():
    with device_session():
        ifaces = hw.enumerate_hid()
        try:
            paired = omni_identity(ifaces)
        except (OSError,TimeoutError,ValueError):
            paired = {}
        found = []
        used = set()
        for d in ifaces:
            group = hw.physical_group_key(d.path)
            if xm.claims(d):
                if xm.matches(d):
                    found.append(Mouse(d.path,xm.NAME,d.vid_pid,'xiaomi2',(d.vid,d.pid),interface=d))
                continue
            if k1.claims(d):
                if k1.matches(d) and d.path not in used:
                    found.append(Mouse(d.path,'GravaStar K1 · 2.4G（待核对型号）',d.vid_pid,
                                       'k1',k1.IDENTITY,interface=d))
                    used.add(d.path)
                continue
            if am_battery.supports_device(d):
                # Feature-only interfaces must not pass through the Output gate.
                if am_battery.matches_interface(d) and d.path not in used:
                    connection = 'USB' if d.pid == 0x0880 else '2.4G'
                    available = 62 <= d.feature_len <= 1024
                    reason = '' if available else '当前固件缺少 DPI 配置通道，请通过官方 AM Master 检查鼠标和接收器固件'
                    found.append(Mouse(d.path, f'{am_battery.MODEL_NAME} · {connection}', d.vid_pid,
                                       'am35' if available else '', (d.vid, d.pid), reason, d))
                    used.add(d.path)
                continue
            rog_omni = (d.vid,d.pid,d.usage_page,d.output_len)==(0x0B05,0x1ACE,0xFF01,64)
            rog_direct = d.vid==0x0B05 and d.pid in (0x1A94,0x1A92) and 'mi_00' in d.path.lower() and d.output_len==65
            atk_config = d.vid==0x373B and (d.usage_page,d.usage)==(0xFF02,2) and d.output_len==17 and d.input_len>=17
            # A VID/PID is a model, not a physical device. Keep distinct config paths.
            selection = d.path if rog_omni or rog_direct or atk_config else group
            if selection in used or not d.output_len:
                continue
            mouse = None
            if rog_omni or rog_direct:
                pid = paired.get(group) if d.pid==0x1ACE else d.pid
                if pid in (0x1A94,0x1A92):
                    mouse = Mouse(d.path,'ROG Harpe Ace Aim Lab Edition',d.vid_pid,'rog',(pid,3 if d.pid==0x1ACE else 0),interface=d)
                else:
                    mouse = Mouse(d.path,d.product or 'ROG 鼠标',d.vid_pid,reason='未确认配对型号或该型号尚未适配 DPI',interface=d)
            elif atk_config:
                try:
                    with hw.HidDevice(d.path,write=True) as dev:
                        identity = atk_identity(dev,d)
                    spec = atk_model(identity,d)
                    mouse = Mouse(d.path,spec[0] if spec else d.product,d.vid_pid,'atk' if spec else '',identity,
                                  '' if spec else f'已识别 CID/MID {identity}，该型号的 DPI 尚未适配',d)
                except (OSError,TimeoutError,ValueError) as exc:
                    mouse = Mouse(d.path,d.product or 'ATK 鼠标',d.vid_pid,reason=str(exc),interface=d)
            elif d.vid in (0x373B,0x3554) and d.usage_page>=0xFF00:
                # Wait until a preferred 17-byte ATK interface has been visited.
                if any(x.vid==0x373B and hw.physical_group_key(x.path)==group and (x.usage_page,x.usage)==(0xFF02,2) and x.output_len==17 for x in ifaces):
                    continue
                mouse = Mouse(d.path,d.product or 'ATK / VXE 鼠标',d.vid_pid,reason='此连接方式或协议版本尚未适配 DPI',interface=d)
            elif d.vid==0x3151 and d.pid in (0x5007,0x402A,0x5006):
                mouse = Mouse(d.path,d.product or '怒喵 AM Infinity',d.vid_pid,reason='已识别怒喵接口；DPI 配置协议待实机适配，本版暂不开放写入',interface=d)
            if mouse:
                used.add(selection);found.append(mouse)
        import peripheral_controls
        found.extend(peripheral_controls.discover(ifaces))
        import dpi_extended
        extended=dpi_extended.discover(ifaces,paired)
        claimed={m.path for m in extended}
        found=[m for m in found if m.protocol or m.path not in claimed]
        found.extend(extended)
        return found


def read(mouse):
    with device_session():
        return _read(mouse)


def _read(mouse):
    if mouse.protocol=='xiaomi2':return xm.read(mouse)
    if mouse.protocol in ('extra','dpi'):
        import dpi_adapters
        return dpi_adapters.read(mouse)
    if not mouse.protocol:
        raise ValueError(mouse.reason or '此型号尚未适配')
    interfaces = hw.enumerate_hid()
    iface = next((d for d in interfaces if d.path==mouse.path),None)
    if iface is None:
        raise OSError('设备已断开，请重新读取设备')
    if mouse.protocol == 'am35' and (iface.vid, iface.pid) != mouse.identity:
        raise ValueError('怒喵设备身份已变化，请重新读取')
    if mouse.protocol=='rog' and iface.pid==0x1ACE:
        if omni_identity(interfaces).get(hw.physical_group_key(iface.path)) != mouse.identity[0]:
            raise ValueError('接收器配对设备已变化，请重新读取')
    with hw.HidDevice(iface.path,write=True) as dev:
        if mouse.protocol=='rog':
            rid = mouse.identity[1]
            profile = rog_query(dev,iface,rid,(0x12,0,0))
            if len(profile)<13 or not 1<=profile[12]<=4 or not 0<=profile[11]<5:
                raise OSError('已识别接收器，但当前无法读取鼠标配置；请确认鼠标已连接对应接收器并处于 2.4G 模式，或重新连接数据线')
            raw = rog_query(dev,iface,rid,(0x12,4,2))
            colors = rog_query(dev,iface,rid,(0x12,4,3))
            if len(raw)<21 or len(profile)<13 or len(colors)<17:
                raise ValueError('鼠标返回的数据不完整')
            stages = [(int.from_bytes(raw[5+i*4:7+i*4],'little')*50+50,
                       int.from_bytes(raw[7+i*4:9+i*4],'little')*50+50) for i in range(4)]
            state = DpiState(mouse,stages,profile[12]-1,50,36000,50,profile[11]+1,
                             colors=[colors[5+i*3:8+i*3] for i in range(4)])
        elif mouse.protocol=='atk':
            identity = atk_identity(dev,iface)
            spec = atk_model(identity,iface)
            if identity != mouse.identity or spec is None:
                raise ValueError('鼠标身份已变化，请重新读取')
            _,family,lo,hi = spec
            profile = atk_profile(dev,iface)
            system = atk_read(dev,iface,0,6)
            if any((system[i]+system[i+1])&255 != 0x55 for i in (0,2,4)):
                raise ValueError('档位配置校验失败')
            count,active = system[2],system[4]
            if not 1<=count<=6:
                raise ValueError('设备返回的档位数无效')
            stages = [atk_decode(atk_read(dev,iface,12+i*4,4),family) for i in range(count)]
            colors = []
            if identity == r1nk.IDENTITY:
                for i in range(count):
                    row = atk_read(dev,iface,0x2C+i*4,4)
                    if len(row)!=4 or sum(row)&255 != 0x55:
                        raise ValueError('DPI 指示色校验失败')
                    colors.append(row[:3])
            if atk_profile(dev,iface) != profile:
                raise ValueError('读取期间 ATK 配置层已变化，请重新读取')
            state = DpiState(mouse,stages,active,lo,hi,10 if family=='ultra' else 50,profile=profile,colors=colors)
        elif mouse.protocol == 'am35':
            config = am_dpi.read(dev, iface)
            state = DpiState(mouse, list(config.stages[:config.count]), config.active,
                             am_dpi.MINIMUM, am_dpi.MAXIMUM, am_dpi.STEP,
                             config.profile+1, protocol_data=config)
        else:
            raise ValueError('未适配的 DPI 协议')
    if not 0<=state.active<len(state.stages) or any(not state.minimum<=v<=state.maximum for row in state.stages for v in row):
        raise ValueError('设备返回的 DPI 超出该型号范围，已停止操作')
    return state


def apply(snapshot, stage, value, activate):
    if snapshot.mouse.protocol=='xiaomi2':return xm.apply(snapshot,stage,value,activate)
    if snapshot.mouse.protocol in ('extra','dpi'):
        import dpi_adapters
        return dpi_adapters.apply(snapshot,stage,value,activate)
    # None switches only the stage, preserving both X/Y DPI values and colors.
    if value is not None and (type(value) is not int or not snapshot.minimum<=value<=snapshot.maximum or value%snapshot.step):
        raise ValueError(f'DPI 必须为 {snapshot.minimum}–{snapshot.maximum}，步进 {snapshot.step}')
    if type(stage) is not int or not 0<=stage<len(snapshot.stages):
        raise ValueError('档位无效')
    if value is not None and snapshot.mouse.protocol=='atk' and snapshot.mouse.identity==r1nk.IDENTITY:
        r1nk.encode_axis(value)  # Reject the nonlinear range before any I/O.
    with device_session():
        current = _read(snapshot.mouse)
        if (current.stages,current.active,current.profile,current.protocol_data,current.colors)!=(snapshot.stages,snapshot.active,snapshot.profile,snapshot.protocol_data,snapshot.colors):
            raise ValueError('设备配置已变化，请重新读取后再应用')
        mouse = current.mouse
        change_dpi=value is not None and current.stages[stage]!=(value,value)
        iface = next(d for d in hw.enumerate_hid() if d.path==mouse.path)
        if mouse.protocol == 'am35':
            return _apply_am(current, iface, stage, value, activate)
        if change_dpi and mouse.protocol=='rog' and current.stages[stage][0]!=current.stages[stage][1]:
            raise ValueError('此档位 X/Y DPI 不同，本版暂不修改，请在原厂工具中调整')
        with hw.HidDevice(mouse.path,write=True) as dev:
            if mouse.protocol=='rog':
                rid = mouse.identity[1]
                if change_dpi:
                    encoded = (value//50-1).to_bytes(2,'little')
                    rog_query(dev,iface,rid,bytes([0x51,0x31,stage,0])+encoded+current.colors[stage])
                if activate and current.active!=stage:
                    rog_query(dev,iface,rid,(0x51,0x31,9,0,stage+1))
                if change_dpi or (activate and current.active!=stage):
                    rog_query(dev,iface,rid,(0x50,3))
            elif mouse.protocol=='atk':
                family = ATK_MODELS[mouse.identity][1]
                if change_dpi:
                    encoded = atk_encode(value,value,family)
                    packet = atk_frame(7,(0,0,12+stage*4,4,*encoded))
                    dev.write_output(packet,use_hidd=False);time.sleep(.03)
                if activate and current.active!=stage:
                    dev.write_output(atk_frame(7,(0,0,4,2,stage,(0x55-stage)&255)),use_hidd=False)
                    time.sleep(.03)
        confirmed = _read(mouse)
        expected = list(current.stages)
        if change_dpi:expected[stage]=(value,value)
        if (confirmed.stages!=expected or confirmed.active!=(stage if activate else current.active)
                or confirmed.profile!=current.profile or confirmed.colors!=current.colors):
            raise RuntimeError('设备读回结果与提交值不一致，请重新读取；部分改动可能已生效')
        return confirmed


def _apply_am(current, iface, stage, value, activate):
    expected = am_dpi.expected(current.protocol_data, stage, value, activate)
    write_error = None
    try:
        with hw.HidDevice(current.mouse.path, write=True) as dev:
            am_dpi.write(dev, iface, current.protocol_data, stage, value, activate)
    except (OSError, ValueError) as exc:
        # A timeout can follow a successful write. Never resend it blindly.
        write_error = exc
    try:
        confirmed = _read(current.mouse)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f'怒喵写后读回失败：{exc}；部分改动可能已生效，请重新读取') from exc
    if write_error is not None:
        x, y = confirmed.stages[stage] if stage < len(confirmed.stages) else ('未知', '未知')
        raise RuntimeError(f'怒喵操作未完整确认：{write_error}；读回档位 {stage+1} 为 X {x} / Y {y}，'
                           f'当前档位 {confirmed.active+1}。未自动重试，请重新读取') from write_error
    if confirmed.protocol_data != expected:
        raise RuntimeError('怒喵读回的 DPI、当前档位或其他配置与预期不一致；部分改动可能已生效，请重新读取')
    return confirmed
