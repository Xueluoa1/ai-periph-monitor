"""Battery queries for explicitly identified wireless receivers.

No cable IDs, settings, pairing or firmware commands. Protocol facts and source
links are recorded in 适配说明-无线电量扩展.md. Hardware validation is pending.
"""
from dataclasses import dataclass, replace
import re
import time

from hid_win import HidDevice
import wireless_battery as wireless
import sony_inzone_battery as sony


@dataclass(frozen=True)
class Profile:
    name: str
    protocol: str


CATALOG = {}


def _add(vid, protocol, models):
    for pid, name in models.items():
        CATALOG[vid, pid] = Profile(name, protocol)


_add(0x1038, 'nova7_percent', {
    0x22A1: '赛睿 Arctis Nova 7 耳机',
    0x227E: '赛睿 Arctis Nova 7 Gen 2 耳机',
    0x2258: '赛睿 Arctis Nova 7X 耳机',
    0x229E: '赛睿 Arctis Nova 7X 耳机',
    0x22AD: '赛睿 Arctis Nova 7X 耳机',
    0x22A5: '赛睿 Arctis Nova 7X 耳机',
    0x22A9: '赛睿 Arctis Nova 7 Diablo IV 耳机',
    0x22A7: '赛睿 Arctis Nova 7P 耳机',
    0x2298: '赛睿 Arctis Nova 7P 耳机',
})
_add(0x1038, 'nova7_steps', {
    0x2202: '赛睿 Arctis Nova 7 耳机',
    0x2206: '赛睿 Arctis Nova 7X 耳机',
    0x22A4: '赛睿 Arctis Nova 7X 耳机',
    0x223A: '赛睿 Arctis Nova 7 Diablo IV 耳机',
    0x227A: '赛睿 Arctis Nova 7 WoW 耳机',
    0x220A: '赛睿 Arctis Nova 7P 耳机',
})
_add(0x1038, 'nova5', {
    0x2232: '赛睿 Arctis Nova 5 耳机',
    0x2253: '赛睿 Arctis Nova 5X 耳机',
})
_add(0x1038, 'arctis7_steps', {
    0x220E: '赛睿 Arctis 7+ 耳机',
    0x2212: '赛睿 Arctis 7+ PS5 耳机',
    0x2216: '赛睿 Arctis 7+ Xbox 耳机',
    0x2236: '赛睿 Arctis 7+ Destiny 耳机',
})
_add(0x03F0, 'hyperx2', {
    0x0696: 'HyperX Cloud II Wireless 耳机',
    0x018B: 'HyperX Cloud II Wireless 耳机',
})
_add(0x03F0, 'hyperx3', {
    0x05B7: 'HyperX Cloud III Wireless 耳机',
    0x0C9D: 'HyperX Cloud III Wireless 耳机',
    0x0995: 'HyperX Cloud II Core Wireless 耳机',
    0x0795: 'HyperX Cloud II Core Wireless 耳机',
})
_add(0x1E71, 'nzxt', {0x2101: 'NZXT Lift Elite Wireless 鼠标'})
_add(0x3434, 'keychron', {0xD028: 'Keychron Ultra-Link 8K 接收器鼠标'})
_add(0x3770, 'hitscan', {0x0200: 'Hitscan Hyperlight 鼠标'})
_add(0x20BC, 'betop_kp20', {0x5191: '北通鲲鹏20 手柄'})
# Plain-byte gen-4 only. Encrypted siblings and 20DD update/sleep identities
# are deliberately absent. Names use hardware SKUs, not guessed retail aliases.
BETOP_MODELS = {
    0x5159: 'KP20D', 0x5170: 'KP20DL', 0x515C: 'KP40D',
    0x5166: 'KP40MCF', 0x5164: 'KP40MCK', 0x514A: 'KP50C',
    0x5151: 'KP50D', 0x5153: 'KP50E', 0x5134: 'KP50B',
    0x5130: 'KP70A', 0x516E: 'KP70A1', 0x5160: 'KP70B',
    0x5174: 'KP70C', 0x518B: 'KP70C1', 0x518D: 'KP70C2',
}
_add(0x20BC, 'betop_kp_plain', {pid: f'北通 {sku} 无线手柄' for pid,sku in BETOP_MODELS.items()})
_add(0x2DC8, 'eightbitdo_bt', {
    0x6000: '八位堂 SF30 Pro 蓝牙手柄', 0x6100: '八位堂 SF30 Pro 蓝牙手柄',
    0x6001: '八位堂 SN30 Pro 蓝牙手柄', 0x6101: '八位堂 SN30 Pro 蓝牙手柄',
    0x6003: '八位堂 Pro 2 蓝牙手柄', 0x6006: '八位堂 Pro 2 蓝牙手柄',
})
_add(0x2DC8, 'eightbitdo_pro3', {0x6009: '八位堂 Pro 3 蓝牙手柄'})
_add(0x03F0, 'hyperx_alpha2', {0x08BE: 'HyperX Cloud Alpha 2 Wireless 耳机'})
_add(0x1038, 'nova5', {0x2269: '赛睿 Arctis Nova 3P Wireless 耳机',
                      0x226D: '赛睿 Arctis Nova 3X Wireless 耳机'})
_add(0x1038, 'arctis_gamebuds', {0x230A: '赛睿 Arctis GameBuds 耳机'})
_add(0x054C, 'sony_inzone', {0x0EBF: '索尼 INZONE H5 耳机',
                            0x0FA8: '索尼 INZONE H9 II 耳机'})
_add(0x37D7, 'flydigi_apex5', {0x2501: '飞智八爪鱼5 手柄'})
_add(0x37D7, 'flydigi_vader5', {0x2401: '飞智黑武士5 Pro 手柄'})
_add(0x1038, 'steel_mouse', {
    0x1838: '赛睿 Aerox 3 Wireless 鼠标',
    0x1878: '赛睿 Aerox 3 Wireless CS2 Dragon Lore 鼠标',
    0x1852: '赛睿 Aerox 5 Wireless 鼠标',
    0x185C: '赛睿 Aerox 5 Wireless Destiny 2 鼠标',
    0x1860: '赛睿 Aerox 5 Wireless Diablo IV 鼠标',
    0x1858: '赛睿 Aerox 9 Wireless 鼠标',
    0x1874: '赛睿 Aerox 9 Wireless WoW 鼠标',
    0x1840: '赛睿 Prime Wireless 鼠标',
    0x1848: '赛睿 Prime Mini Wireless 鼠标',
})
_add(0x33E4, 'gw_new', {
    0x3817: 'G-Wolves HTM Plus 鼠标',
    0x6817: 'G-Wolves HSK Pro 2.0 鼠标',
    0x5617: 'G-Wolves HTXU 鼠标',
    0x3617: 'G-Wolves Fenrir Pro 鼠标',
    # Shared receiver: do not invent a paired model from its PID.
    0x3854: 'G-Wolves 8K 接收器鼠标（共享型号）',
})
_add(0x33E4, 'gw_old', {
    0x3917: 'G-Wolves VUK 鼠标', 0x7913: 'G-Wolves HT-S2 鼠标',
    0x3717: 'G-Wolves Fenrir Max 鼠标', 0x5317: 'G-Wolves HTS Ultra 鼠标',
    0x7713: 'G-Wolves HTR 鼠标', 0x3517: 'G-Wolves Fenrir 鼠标',
    0x7917: 'G-Wolves HT-S2 Pro 鼠标', 0x5817: 'G-Wolves HSK Pro 鼠标',
    0x5807: 'G-Wolves HSK Pro 鼠标', 0x2717: 'G-Wolves HTX Mini 鼠标',
    0x5417: 'G-Wolves HTS Plus 鼠标', 0x5407: 'G-Wolves HTS Plus 鼠标',
    0x5717: 'G-Wolves HTX 鼠标', 0x5707: 'G-Wolves HTX 鼠标',
    0x5917: 'G-Wolves HSK Plus 鼠标', 0x5907: 'G-Wolves HSK Plus 鼠标',
    0x7203: 'G-Wolves HSK Lite 鼠标', 0x7717: 'G-Wolves HTR Pro 鼠标',
    0x5803: 'G-Wolves HSK Pro ACE 鼠标', 0x5403: 'G-Wolves HTS Plus ACE 鼠标',
    0x5703: 'G-Wolves HTX ACE 鼠标', 0x5903: 'G-Wolves HSK Plus ACE 鼠标',
})


@dataclass(frozen=True)
class Reading:
    level: int | None
    status: str = 'unknown'
    label: str = ''
    raw_level: int | None = None
    voltage_mv: int | None = None
    raw: bytes = b''


def profile_for(iface):
    return CATALOG.get((iface.vid, iface.pid))


def matches(iface):
    p = profile_for(iface)
    if p is None:
        return False
    page, usage = iface.usage_page, iface.usage
    inp, out, feature = iface.input_len, iface.output_len, iface.feature_len
    if p.protocol.startswith('eightbitdo'):
        path = iface.path.lower()
        bluetooth = 'vid&' in path or '{00001124-0000-1000-8000-00805f9b34fb}' in path or 'bthle' in path
        return bluetooth and page == 1 and usage in (4, 5) and 15 <= inp <= 128
    if p.protocol == 'betop_kp_plain':
        return page in (0xFF00, 0x00FF) and usage == 3 and inp == out == 64
    if p.protocol == 'hyperx_alpha2':
        return (page, usage) == (0xFF13, 0xFF00) and inp == out == 64
    if p.protocol == 'sony_inzone':
        return (page, usage) == (0xFF04, 2 if iface.pid == 0x0EBF else 1) and inp == out == 64
    if p.protocol.startswith(('nova', 'arctis')):
        return (page, usage) == (0xFFC0, 1) and out in (64, 65) and 16 <= inp <= 129
    if p.protocol == 'hyperx2':
        return (page, usage) == (0xFF90, 0x0303) and out == 52 and 20 <= inp <= 64
    if p.protocol == 'hyperx3':
        return (page, usage) == (0xFF13, 1) and out == 62 and inp == 62
    if p.protocol == 'nzxt':
        return (page, usage) == (0xFFCA, 1) and inp == out == 64
    if p.protocol == 'keychron':
        return (page >= 0xFF00 and inp == feature == 64
                and bool(re.search(r'&mi_04(?:&|#)', iface.path.lower())))
    if p.protocol == 'hitscan':
        return (page, usage) == (0xFF02, 2) and out in (17, 64) and inp in (17, 64)
    if p.protocol in ('gw_new', 'gw_old'):
        return page >= 0xFF00 and feature == 65
    if p.protocol == 'betop_kp20':
        return (page in (0xFF00, 0x00FF) and usage == 3
                and out in (16, 32, 64, 65) and 8 <= inp <= 65
                and 'BTP-KP20' in iface.product.upper())
    if p.protocol == 'flydigi_apex5':
        return page == 0xFFA0 and out == 32 and inp in (32, 64)
    if p.protocol == 'flydigi_vader5':
        # Unnumbered 32-byte reports have an extra zero report ID on Windows.
        return page == 0xFFA0 and out == 33 and inp in (33, 65)
    if p.protocol == 'steel_mouse':
        return (page, usage) == (0xFFC0, 1) and out in (64, 65) and inp in (64, 65)
    return False


def _percentage(value, status='unknown'):
    return Reading(value, status, raw_level=value) if 0 <= value <= 100 else None


def _steps(value, status):
    if 0 <= value <= 4:
        return Reading(None, status, f'{value}/4 档', value)
    return None


def decode(protocol, raw):
    """Reject unrelated, truncated, offline, invalid and request-echo reports."""
    r = bytes(raw or b'')
    if protocol in ('betop_kp20', 'betop_kp_plain'):
        # Empty status echoes are not a live 0% battery. A real zero-level
        # status still contains the controller state bytes (public capture).
        if len(r) >= 8 and r[:2] == b'\x02\x15' and any(r[3:7]):
            status = {0: 'discharging', 1: 'charging'}.get(r[4] & 15, 'unknown')
            return _percentage(r[2], status)
        return None
    if protocol.startswith('eightbitdo'):
        rid = 4 if protocol == 'eightbitdo_pro3' else 1
        if len(r) >= 15 and r[0] == rid and 1 <= (r[14] & 127) <= 100:
            return _percentage(r[14] & 127, 'charging' if r[14] & 128 else 'discharging')
        return None  # Ordinary input reports can be zero-padded to this size.
    if protocol == 'hyperx_alpha2':
        if len(r) == 64 and r[:2] == b'\x51\x02':
            return _percentage(r[2])  # Charge bit not sufficiently validated.
        return None
    if protocol.startswith('flydigi_'):
        report_id = 4 if protocol == 'flydigi_apex5' else 0
        if len(r) < 20 or r[:4] != bytes((report_id, 0x5A, 0xA5, 1)):
            return None
        # Replies are either length+payload or count+index+payload. Only the
        # first fragment carries identity and battery; never parse later ones.
        if r[5] < r[4]:
            if r[5] != 0:
                return None
            offset = 6
        else:
            if r[4] < 16 or r[4] > len(r)-3:
                return None
            offset = 5
        models = ({128, 129, 133, 134, 135, 136} if protocol == 'flydigi_apex5'
                  else {130, 144, 145})
        if r[offset] not in models or r[offset+1] != 2:
            return None  # Unknown model, cable connection or sleeping receiver.
        value = r[offset+6]
        steps, charging = value & 15, value >> 4
        if steps <= 5 and charging in (0, 1):
            return Reading(None, 'charging' if charging else 'discharging',
                           f'{steps}/5 档', steps)
        return None
    if protocol == 'steel_mouse':
        if r[:2] == b'\0\xd2':
            r = r[1:]
        if len(r) < 2 or r[0] != 0xD2:
            return None
        code = r[1] & 0x7F
        if not 1 <= code <= 21:
            return None
        level = (code - 1) * 5
        return Reading(level, 'charging' if r[1] & 0x80 else 'discharging',
                       f'约 {level}%', code)
    if protocol.startswith(('nova', 'arctis')):
        if r[:2] == b'\0\xb0':
            r = r[1:]
        if len(r) < 4 or r[0] != 0xB0:
            return None
        if protocol == 'arctis_gamebuds':
            if len(r) < 7:
                return None
            levels = [r[i+2] for i in (3, 4) if r[i] == 3]
            if levels and all(v <= 100 for v in levels):
                return _percentage(min(levels))
            return None
        if protocol.startswith('nova7'):
            if r[1] != 3 or r[3] not in (1, 2, 3):
                return None
            status = 'charging' if r[3] in (1, 2) else 'discharging'
            return (_steps if protocol.endswith('steps') else _percentage)(r[2], status)
        if protocol == 'nova5':
            if len(r) < 16 or r[1] == 2 or not any(r[1:]) or r[4] not in (0, 1):
                return None
            return _percentage(r[3], 'charging' if r[4] else 'discharging')
        if protocol == 'arctis7_steps':
            if r[1] == 1 or not any(r[1:]) or r[3] not in (0, 1):
                return None
            return _steps(r[2], 'charging' if r[3] else 'discharging')
    elif protocol == 'hyperx2':
        if len(r) >= 20 and r[:4] == b'\x06\xff\xbb\x02' and any(r[4:]):
            return _percentage(r[7])
    elif protocol == 'hyperx3':
        if len(r) == 62 and r[0] == 0x66 and r[1] in (0x89, 0x0D) and any(r[2:4]):
            return _percentage(r[4])
    elif protocol == 'nzxt':
        if len(r) == 64 and r[:3] == b'\x4e\x02\x97':
            result = _percentage(int.from_bytes(r[17:19], 'little'),
                                 'charging' if r[22] & 1 else 'discharging')
            mv = int.from_bytes(r[8:10], 'big')
            if result and 2500 <= mv <= 5000:
                return replace(result, voltage_mv=mv)
    elif protocol == 'keychron':
        if len(r) == 64 and r[:2] == b'\xb4\x06':
            return _percentage(r[20])
    elif protocol == 'hitscan':
        try:
            result = wireless.decode_compx(r)
        except ValueError:
            return None
        if result:
            level, status, mv = result
            return Reading(level, status, raw_level=level, voltage_mv=mv)
    elif protocol in ('gw_old', 'gw_new'):
        if len(r) not in (64, 65):
            return None
        if r[0] == 0:
            r = r[1:]
        if protocol == 'gw_new':
            if r[:6] != b'\xa1\0\x02\x02\0\x83':
                return None
            status, value = r[6:8]
        else:
            if r[:3] != b'\xa1\x02\x8f':
                return None
            status, value = r[4:6]
        if status in (0, 1):
            return _percentage(value, 'charging' if status else 'discharging')
    return None


def _input_query(dev, iface, packet, parse, *, feature=False):
    dev.drain()
    if feature:
        dev.set_feature_report(packet)
    else:
        # Diagnostic wrappers return False; the native transport raises instead.
        if dev.write_output(packet, use_hidd=False) is False:
            raise OSError('接收器拒绝电量查询')
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=iface.input_len)
        if raw and bytes(raw) != packet:
            result = parse(raw)
            if result is not None:
                return result, bytes(raw)
    return None, b''


def _charging(protocol, raw):
    r = bytes(raw or b'')
    if protocol == 'hyperx2' and len(r) >= 20 and r[:4] == b'\x06\xff\xbb\x03':
        code = r[4]
    elif protocol == 'hyperx3' and len(r) == 62 and r[0] == 0x66 and r[1] in (0x8A, 0x0C):
        code = r[2]
    else:
        return None
    return {0: 'discharging', 1: 'charging', 2: 'full'}.get(code)


def query(dev, iface):
    """Caller owns the device session. Same queries are used by diagnostics."""
    if not matches(iface):
        raise ValueError('不是已核实的无线接收器电量接口')
    protocol = profile_for(iface).protocol
    parse = lambda raw: decode(protocol, raw)
    if protocol.startswith('eightbitdo'):
        # Never GET_FEATURE 06: that READ switches input mode on older pads.
        deadline = time.monotonic() + 0.6
        for _ in range(16):
            if time.monotonic() >= deadline:
                break
            raw = dev.read_input(timeout_ms=50, length=iface.input_len)
            result = parse(raw)
            if result is not None:
                return replace(result, raw=bytes(raw))
        return None
    if protocol == 'sony_inzone':
        packet, tid = sony.request()
        def parse_sony(raw):
            result = sony.decode(raw, tid)
            return _percentage(*result) if result else None
        result, raw = _input_query(dev, iface, packet, parse_sony)
        return replace(result, raw=raw) if result else None
    if protocol in ('gw_new', 'gw_old'):
        body = bytes.fromhex('000002020083') if protocol == 'gw_new' else bytes.fromhex('00028f01')
        packet = b'\0' + body.ljust(64, b'\0')
        dev.set_feature_report(packet)
        for _ in range(10):
            time.sleep(0.05)
            raw = dev.get_feature_report(0, 65)
            result = parse(raw)
            if result:
                return replace(result, raw=bytes(raw))
        return None
    if protocol.startswith(('nova', 'arctis')):
        prefix = b'\0\xb0'
    else:
        prefix = {'hyperx2': b'\x06\xff\xbb\x02', 'hyperx3': b'\x66\x89',
                  'nzxt': b'\x4e\x02\x81\0\xb0', 'keychron': b'\xb3\x06',
                  'betop_kp20': b'\x02\x15', 'steel_mouse': b'\0\xd2',
                  'betop_kp_plain': b'\x02\x15', 'hyperx_alpha2': b'\x50\x02',
                  'flydigi_apex5': bytes.fromhex('035aa5010203'),
                  'flydigi_vader5': bytes.fromhex('005aa5010203'),
                  'hitscan': bytes.fromhex('0804000000000000000000000000000049')}[protocol]
    packet = prefix.ljust(iface.feature_len if protocol == 'keychron' else iface.output_len, b'\0')
    result, raw = _input_query(dev, iface, packet, parse, feature=protocol == 'keychron')
    if result is None:
        return None
    if protocol in ('hyperx2', 'hyperx3'):
        # Keep the valid battery if the independent charging query fails.
        charge_prefix = b'\x06\xff\xbb\x03' if protocol == 'hyperx2' else b'\x66\x8a'
        try:
            status, _ = _input_query(dev, iface, charge_prefix.ljust(iface.output_len, b'\0'),
                                     lambda r: _charging(protocol, r))
            if status:
                result = replace(result, status=status)
        except OSError:
            pass
    return replace(result, raw=raw)


def read(iface, result_type):
    profile = profile_for(iface)
    if profile is None:
        return None
    extra = {'model_name': profile.name, 'connection': '2.4G', 'hardware_verified': False}
    passive = profile.protocol.startswith('eightbitdo')
    if passive:
        extra['connection'] = '蓝牙'
    source = f'{profile.protocol} 无线电量协议'
    if not matches(iface):
        return result_type(ok=False, source=source, extra=extra, detail='该接口不是已核实的电量通道')
    try:
        with HidDevice(iface.path, write=not passive) as dev:
            result = query(dev, iface)
    except OSError as exc:
        return result_type(ok=False, source=source, extra=extra, detail=f'电量查询失败：{exc}')
    if result is None:
        return result_type(ok=False, source=source, extra=extra,
                           detail=('未收到增强报告中的电量；不会切换手柄模式，请使用系统电量或在游戏已启用增强报告时刷新'
                                   if passive else '未收到有效电量，请连接并唤醒无线设备后刷新'))
    extra['level_label'] = result.label
    return result_type(ok=True, level=result.level, raw_level=result.raw_level,
                       status=result.status, voltage_mv=result.voltage_mv, raw=result.raw,
                       source=source, extra=extra)
