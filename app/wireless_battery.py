"""Battery-only transports audited in v21. No setting or pairing commands.

Protocol provenance and hardware limitations are recorded in 设备支持-v21.md.
Windows report lengths include the report ID. A reply must match its query;
failed known routes never fall through to another vendor's heuristic decoder.
"""
import time

import hid_win as hw
import atk_r1_nk as r1nk
import vxe_r1_pro_max as vxe


PAGE_MODELS = {
    (0x373E, 0x001C): 'LAMZU Maya X',
    (0x373E, 0x001D): 'LAMZU Maya X',
    (0x373E, 0x001E): 'LAMZU Maya X',
    (0x2023, 0xF013): 'MCHOSE A5 Pro Max',
    (0x2023, 0xF015): 'MCHOSE A5 Pro Max',
    (0x2023, 0xF019): 'MCHOSE A5 Pro Max',
}
for _pid, _name in ((0xA868, 'Beast Mini Pro'), (0xA869, 'Beast Mini Pro'),
                    (0xA870, 'Beast X Pro'), (0xA871, 'Beast X Pro'),
                    (0xA880, 'Beast Max'), (0xA881, 'Beast Max'),
                    (0xA882, '1K Receiver'), (0xA883, 'Beast X'), (0xA884, 'Beast X'),
                    (0xA885, 'Beast Mini'), (0xA886, 'Beast Mini')):
    PAGE_MODELS[0x36A7, _pid] = 'WLmouse ' + _name

VAXEE_IDS = {0x1001, 0x1002, 0x1003, 0x1004, 0x1005, 0x1006, 0x1007,
             0x1008, 0x1009, 0x1010, 0x1011, 0x1012, 0x1013, 0x1014,
             0x1015, 0x0005, 0x2001, 0x2002}
ENDGAME_IDS = {0x1970, 0x1968, 0x1972, 0x1982, 0x1984}
# Shared CompX IDs are deliberately not assigned a marketing model name.
PULSAR_IDS = {0x5406, 0x3414}
MANAGED_VIDS = {0x373E, 0x342D, 0x36A7, 0x5253, 0x3837, 0x3057, 0x3367,
                0x373B, 0x3554, 0x24AE, 0x1915}


def claims(d):
    return (d.vid in MANAGED_VIDS or (d.vid, d.pid) in PAGE_MODELS
            or (d.vid, d.pid) == (0xA8A5, 0x2255)
            or (d.vid in (0x373B, 0x3554) and (d.usage_page, d.usage) == (0xFF02, 2)
                and d.output_len in (17, 64) and d.input_len >= 17)
            or (d.vid == 0x3710 and d.pid in PULSAR_IDS))


def route(d):
    identity = (d.vid, d.pid)
    if identity == (0x36A7, 0xA887) and d.usage_page == 0xFF1C and d.output_len == 64 and d.input_len == 64:
        return 'beast4k'
    if identity in PAGE_MODELS and d.usage_page == 0xFFFF and d.feature_len in (64, 65):
        return 'page'
    if d.vid in (0x5253, 0x3837) and d.usage_page == 0xFF01 and d.feature_len >= 21:
        return 'mchose'
    if identity == (0xA8A5, 0x2255) and (d.usage_page, d.usage) == (0xFF01, 0x10) and d.output_len == 65:
        return 'g7'
    if (d.vid in (0x373B, 0x3554) or (d.vid == 0x3710 and d.pid in PULSAR_IDS)) and (d.usage_page, d.usage) == (0xFF02, 2) and d.output_len in (17, 64) and d.input_len >= 17:
        return 'compx'
    if d.vid == 0x3057 and d.pid in VAXEE_IDS and (d.usage_page, d.usage) == (0xFF05, 1) and d.feature_len >= 64:
        return 'vaxee'
    if d.vid == 0x3367 and d.pid in ENDGAME_IDS and (d.usage_page, d.usage) == (0xFF01, 2) and d.feature_len >= 64:
        return 'endgame'
    return None


def candidate(d):
    return route(d) is not None


def percent(value):
    if not 0 <= value <= 100:
        raise ValueError('设备返回的电量不在 0–100 范围')
    return value


def charge(value):
    if value not in (0, 1):
        raise ValueError('设备返回的充电状态无效')
    return 'charging' if value else 'discharging'


def decode_compx(raw):
    r = bytes(raw)
    if len(r) < 17 or r[:2] != b'\x08\x04':
        return None
    if sum(r[:17]) & 255 != 0x55 or r[2] != 0 or not 2 <= r[5] <= 10:
        return None
    level, status = percent(r[6]), charge(r[7])
    mv = int.from_bytes(r[8:10], 'big') if r[5] >= 4 else None
    return level, status, mv if mv and 2500 <= mv <= 5000 else None


def decode_page(raw):
    r = bytes(raw)
    for offset in (0, 1):
        if offset and (not r or r[0] != 0):
            continue
        if len(r) < offset+8:
            continue
        s = r[offset:]
        if s[0] != 0xA1 or s[2:6] != b'\x02\x02\x00\x83':
            continue
        return percent(s[7]), charge(s[6]), None
    return None


def decode_mchose(raw, vid):
    if len(raw) < 13 or raw[0] not in (0x11, 0x12) or raw[1] != 0xF9:
        return None
    data = bytes(value ^ 255 for value in raw[2:])
    if int.from_bytes(data[:2], 'little') != vid:
        return None  # Empty/sleep responses and request echoes are not 0%.
    return percent(data[9]), charge(data[10]), None


def decode_g7(raw):
    r = bytes(raw)
    if r[:1] == b'\0':
        r = r[1:]
    if len(r) < 10 or r[:3] != b'\xaa\x30\xa5':
        return None
    return percent(r[8]), charge(r[9]), None


def decode_vaxee(raw, command):
    r = bytes(raw)
    # WebHID strips report ID; HidD_GetFeature returns it on Windows.
    if r[:1] == b'\x0e':
        r = r[1:]
    if len(r) < 6 or r[:4] != bytes((0xA5, command, 3, 1)) or r[4] < 1 or r[4] > len(r)-5:
        return None
    if command == 0x0B and r[5] > 20:
        raise ValueError('VAXEE 电量档位超出 0–20 范围')
    return r[5]


def decode_endgame(raw):
    if len(raw) < 19 or raw[0] != 0xA1 or raw[1] != 1:
        return None
    # Bytes 17/18 are not a voltage; charging is not documented by this query.
    return percent(raw[16]), 'unknown', None


def crc16(data):
    value = 0xFFFF
    for octet in data:
        value ^= octet
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def beast4k_request():
    packet = bytearray(64)
    packet[0], packet[3], packet[4] = 4, 0x1A, 6
    packet[1:3] = crc16(packet[3:32]).to_bytes(2, 'little')
    return bytes(packet)


def decode_beast4k(raw):
    if len(raw) != 64 or raw[0] != 4 or raw[3:5] != b'\x1a\x06':
        return None
    # Published battery query confirms percentage only, not charging layout.
    return percent(raw[8]), 'unknown', None


def _input(dev, packet, decoder, seconds=.7):
    dev.drain()  # Opens the reader before a fast response can arrive.
    dev.write_output(packet, use_hidd=False)
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        raw = dev.read_input(timeout_ms=100)
        if raw and raw != packet:
            parsed = decoder(raw)
            if parsed is not None:
                return parsed, raw
    return None


def _feature(dev, packet, decoder, repeats=8, pause=.05):
    dev.set_feature_report(packet)
    for _ in range(repeats):
        time.sleep(pause)
        raw = dev.get_feature_report(packet[0], len(packet))
        if raw and raw != packet:
            parsed = decoder(raw)
            if parsed is not None:
                return parsed, raw
    return None


def query(dev, d, kind):
    if kind == 'beast4k':
        return _input(dev, beast4k_request(), decode_beast4k, seconds=1.2)
    elif kind == 'compx':
        packet = bytes.fromhex('0804000000000000000000000000000049').ljust(d.output_len, b'\0')
        for _ in range(3):
            result = _input(dev, packet, decode_compx)
            if result:
                return result
    elif kind == 'page':
        # 64-byte body + report ID 0 on the usual Windows descriptor.
        packet = bytes.fromhex('00000002020083').ljust(d.feature_len, b'\0')
        return _feature(dev, packet, decode_page)
    elif kind == 'mchose':
        for rid, length in ((0x11, 21), (0x12, 65)):
            if length > d.feature_len:
                continue
            packet = bytes((rid, 0xF9)) + b'\xff'*(length-2)
            # Firmware returns echoes between real answers; resend the read only.
            for _ in range(3):
                try:
                    result = _feature(dev, packet, lambda r: decode_mchose(r, d.vid), repeats=1, pause=.08)
                except OSError:
                    break  # Some firmware exposes only the long report channel.
                if result:
                    return result
    elif kind == 'g7':
        return _input(dev, bytes.fromhex('005530a50b2e010101').ljust(65, b'\0'), decode_g7)
    elif kind == 'vaxee':
        def get(command):
            return _feature(dev, bytes((0x0E, 0xA5, command, 1, 1)).ljust(64, b'\0'),
                            lambda r: decode_vaxee(r, command), repeats=4)
        battery = get(0x0B)
        if battery:
            status = 'unknown'
            try:
                charging = get(0x10)
                if charging: status = charge(charging[0])
            except (OSError, ValueError):
                pass
            return (battery[0]*5, status, None), battery[1]
    elif kind == 'endgame':
        return _feature(dev, bytes((0xA1, 0xB4)).ljust(64, b'\0'),
                        decode_endgame, repeats=5, pause=.15)
    return None


def read(d, result_type):
    kind = route(d)
    if not kind:
        return result_type(ok=False, source='型号专用电量通道', detail='当前硬件 ID 或接口尚未核实，未发送猜测查询')
    source = {'page':'页面式电量协议', 'mchose':'迈从 Feature 电量协议',
              'g7':'迈从 G7 电量协议', 'compx':'ATK / Compx 电量协议',
              'vaxee':'VAXEE 电量协议', 'endgame':'Endgame Gear 电量协议',
              'beast4k':'WLmouse Beast X 4K 电量协议'}[kind]
    extra = {}
    if (d.vid, d.pid) in PAGE_MODELS:
        extra['model_name'] = PAGE_MODELS[d.vid, d.pid]
    if kind == 'beast4k':
        extra['model_name'] = 'WLmouse Beast X 4K'
    try:
        with hw.HidDevice(d.path, write=True) as dev:
            response = query(dev, d, kind)
            if response and kind == 'compx' and (r1nk.receiver(d) or vxe.matches(d)):
                try:
                    identity = _input(dev,bytes.fromhex('081000000000000000000000000000003d'),r1nk.decode_identity)
                    if identity and identity[0] == r1nk.IDENTITY and r1nk.receiver(d):
                        extra['model_name'] = r1nk.MODEL
                    elif identity and identity[0] == vxe.IDENTITY and vxe.matches(d):
                        extra['model_name'] = vxe.MODEL
                except (OSError,ValueError):
                    pass  # A missing model reply must not discard valid battery data.
        if response:
            (level, status, mv), raw = response
            if kind == 'mchose':
                payload = bytes(value ^ 255 for value in raw[2:])
                model_id = int.from_bytes(payload[2:4], 'little')
                extra['device_model_id'] = f'{model_id:04X}'
                if d.vid == 0x5253 and model_id == 0x0031:
                    extra['model_name'] = 'MCHOSE M7 Ultra'
            elif kind == 'g7':
                extra['model_name'] = 'MCHOSE G7'
            return result_type(ok=True, level=level, raw_level=level, status=status,
                               voltage_mv=mv, raw=raw, source=source, extra=extra)
        return result_type(ok=False, source=source, extra=extra,
                           detail='未收到有效电量回复，请唤醒鼠标并关闭厂商驱动后重试')
    except (OSError, ValueError) as exc:
        return result_type(ok=False, source=source, extra=extra, detail=f'电量读取失败：{exc}')
