"""Model-specific, battery-only HID exchanges. See 设备支持-v5.md for provenance.

Never select a query by a marketing brand or by VID alone. All offsets below
include Windows' report-ID byte. No configuration/firmware commands are used.
"""
from dataclasses import dataclass
import itertools
import threading
import time

from hid_win import HidDevice
import weisheng_keyboard as wk
import darmoshark_m3pro as dm


@dataclass(frozen=True)
class Profile:
    protocol: str
    model: str


CATALOG = {
    (0x1915, 0x0723): Profile('darmoshark_m3pro', '达摩鲨 Nordic 接收器（待读取配对型号）'),
    (0x320F, 0x5088): Profile('weisheng_keyboard', wk.MODEL),
    (0x260D, 0x1114): Profile('dareu', '达尔优 DAREU A950 PRO Mg 鼠标'),
    (0x24AE, 0x1205): Profile('rapoo', '雷柏 Rapoo VT9 Pro 鼠标'),
    (0x093A, 0x522C): Profile('incott', '因科特 Incott G23 V2 Pro / 8K 鼠标'),
    (0x1D57, 0xFA60): Profile('delux', '2.4G 无线鼠标（等待型号上报）'),
    (0x3151, 0x5038): Profile('akko', 'Akko / MonsGeek 2.4G 键盘'),
    (0x388D, 0x0025): Profile('lofree', '洛斐 Lofree Hyzen 键盘'),
    (0x372E, 0x1019): Profile('rk', 'RK Royal Kludge M3 鼠标'),
    (0x320F, 0x706E): Profile('hecate', '漫步者电竞 HECATE G3M Pro 鼠标'),
}
for _pid, _model in ((0x2157, 'NJ07'), (0x2158, 'NJ08'), (0x2167, 'NJ07 MC'),
                     (0x2168, 'NJ08 MC'), (0x2177, 'NJ07 MC Pro'), (0x2178, 'NJ08 MC Pro')):
    CATALOG[0xA8A5, _pid] = Profile('ajazz', f'黑爵 AJAZZ {_model} 鼠标')
for _pid in (0xFEF8, 0xFEF9, 0xFEFA, 0xFEFC, 0xFEFD, 0xFEFE):
    _model = {0xFEFC: '黑爵 AJAZZ MK87 Pro 键盘', 0xFEFE: '狼蛛 AULA F87 Pro V2D 键盘'}.get(
        _pid, 'AULA / AJAZZ 2.4G 键盘')
    CATALOG[0x0C45, _pid] = Profile('aula', _model)


def profile_for(iface):
    return CATALOG.get((iface.vid, iface.pid))


def matches_interface(iface):
    p = profile_for(iface)
    if not p:
        return False
    page, usage = iface.usage_page, iface.usage
    if p.protocol == 'weisheng_keyboard':return wk.matches(iface)
    if p.protocol == 'darmoshark_m3pro':return dm.matches(iface)
    if p.protocol == 'dareu':
        return (page, usage) == (0xFF02, 2) and iface.output_len == 17 and iface.input_len == 17
    if p.protocol == 'rapoo':
        return (page, usage) == (0xFF0B, 0x104) and iface.feature_len >= 10
    if p.protocol == 'ajazz':
        return (page, usage) == (0xFF01, 0x10) and iface.output_len == 64 and iface.input_len >= 10
    if p.protocol == 'aula':
        return (page >= 0xFF00 and iface.output_len in (33, 65) and iface.input_len >= 28
                and (iface.pid != 0xFEFC or (page, usage) == (0xFF80, 1)))
    if p.protocol == 'akko':
        return page == 0xFFFF and iface.feature_len == 65
    if p.protocol in ('lofree', 'hecate'):
        size = 32 if p.protocol == 'lofree' else 64
        return (page, usage) == (0xFF1C, 0x92) and iface.output_len == size and iface.input_len >= size
    if p.protocol == 'rk':
        return page == 0xFF00 and iface.feature_len == 64
    if p.protocol == 'incott':
        return page == 0xFF05 and iface.input_len >= 8
    if p.protocol == 'delux':
        # The battery notification also appears on consumer collections.
        return (page == 0x0C or page >= 0xFF00) and iface.input_len >= 5
    return False


def decode(protocol, raw, *, command=0x10, sequence=0, descriptor=''):
    """Return (percentage, status, model override), or None for unrelated data."""
    r = bytes(raw or b'')
    if protocol == 'weisheng_keyboard':return wk.decode(r)
    value, status, model = None, 'unknown', ''
    if protocol == 'dareu' and len(r) == 17 and r[:2] == b'\x08\x04':
        value = r[6]
    elif protocol == 'rapoo' and len(r) >= 10 and r[:2] == b'\x2a\x01':
        value = r[9]
    elif protocol == 'ajazz' and len(r) >= 10 and r[:2] == b'\xf0\x30':
        value, status = r[8], 'charging' if r[9] else 'discharging'
    elif protocol == 'aula':
        start = 1 if r[:2] == b'\x00\x55' else 0
        if len(r) >= start + 27 and r[start:start+2] == bytes((0x55, command)):
            payload = r[start+8:]
            if payload[16] != 0:  # 0 = wired/offline; never manufacture a 0% reading.
                value, status = payload[17], 'charging' if payload[18] else 'discharging'
    elif protocol == 'akko' and len(r) == 65 and r[0] in (0, 5):
        if r[2] == 0 and r[5:7] == b'\x01\x01' and r[1] > 0:
            value = r[1]  # This firmware uses zero for an unavailable reading.
    elif protocol == 'rk' and len(r) >= 16 and r[:3] == b'\x03\x50\x00':
        if r[4] == sequence and r[5] == 1:
            value, status = r[6] & 0x7F, 'charging' if r[6] & 0x80 else 'discharging'
    elif protocol == 'hecate' and len(r) >= 64 and r[0] == 4 and r[3] == 0x1A:
        if r[7] not in (0xFE, 0xFF):
            value, status = r[8], {0: 'discharging', 2: 'charging'}.get(r[9], 'unknown')
    elif protocol == 'incott' and len(r) >= 8 and r[0] == 9:
        # Input reports only: never apply this decoder to feature reply 09 8E.
        value = r[1] - 128 if r[1] > 100 else r[1]
        status = 'charging' if r[1] > 100 else 'discharging'
    elif protocol == 'delux' and len(r) >= 5 and r[0] == 3 and r[2:4] == b'\x40\x01':
        if r[1] == 0x20:
            value, model = r[4], '多彩 DELUX M600 Pro 鼠标'
        elif r[1] == 0x55 and ('delux' in descriptor.casefold() or 'm800' in descriptor.casefold()):
            value, model = r[4], '多彩 DELUX M800 Mini 鼠标'
    if value is not None and 0 <= value <= 100:
        return value, status, model
    return None


def _wait(dev, iface, accept, seconds=0.7):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=iface.input_len)
        if raw and accept(raw):
            return raw
    return None


def _lofree_command(dev, iface, command):
    def send(cmd):
        packet = bytearray(32)
        packet[0], packet[3] = 4, cmd
        dev.write_output(bytes(packet), use_hidd=False)
    def ack(cmd):
        return lambda r: len(r) >= 4 and r[0] == 4 and r[3] == cmd
    dev.drain()
    send(1)
    if not _wait(dev, iface, ack(1)):
        return None
    try:
        send(command)
        return _wait(dev, iface, lambda r: len(r) >= 9 and r[0] == 4
                     and r[3] in (0, command) and r[5:7] == b'\x00\x00', 2.0)
    finally:
        send(2)
        _wait(dev, iface, ack(2), 0.3)


_sequence = itertools.count(1)
_io_lock = threading.Lock()


def _active_read(dev, iface, protocol):
    if protocol == 'darmoshark_m3pro':return dm.read(dev,iface)
    if protocol == 'weisheng_keyboard':return wk.read(dev,iface)
    if protocol == 'lofree':
        online = _lofree_command(dev, iface, 0xAA)
        if not online or online[8] == 0:
            return None, b''
        raw = _lofree_command(dev, iface, 0x1A)
        return ((raw[8], 'unknown', '') if raw and raw[8] <= 100 else None), raw or b''
    if protocol == 'rapoo':
        raw = dev.get_feature_report(0x2A, iface.feature_len)
        return decode(protocol, raw), raw
    if protocol == 'akko':
        packet = bytearray(65)
        packet[1], packet[8] = 0xFC, 3
        for _ in range(3):
            dev.set_feature_report(bytes(packet))
            time.sleep(0.01)
            dev.get_feature_report(5, 65)
        packet[1], packet[8] = 0xF7, 8
        dev.set_feature_report(bytes(packet))
        time.sleep(0.08)
        for attempt in range(8):
            raw = dev.get_feature_report(5, 65)
            parsed = decode(protocol, raw)
            if parsed:
                return parsed, raw
            packet[1], packet[8] = 0xFC, 3  # Advance response queue (NOP).
            dev.set_feature_report(bytes(packet))
            time.sleep(0.03)
        return None, b''
    if protocol == 'rk':
        sequence = next(_sequence) % 200 + 1
        packet = bytearray(64)
        packet[0], packet[2], packet[4], packet[5], packet[6] = 3, 0x50, sequence, 0x4F, 0x81
        packet[1] = sum(packet[2:63]) & 0xFF
        dev.set_feature_report(bytes(packet))
        for _ in range(12):
            time.sleep(0.025)
            raw = dev.get_feature_report(3, 64)
            parsed = decode(protocol, raw, sequence=sequence)
            if parsed:
                return parsed, raw
        return None, b''
    dev.drain()
    if protocol == 'aula':
        for command, total, tail in ((0x10, 56, True), (0x10, 48, True), (0xA0, 64, False)):
            packet = bytearray(iface.output_len)
            size = min(total, iface.output_len - 9)
            packet[1:4] = bytes((0xAA, command, size))
            packet[7] = int(tail and size == total)
            dev.write_output(bytes(packet), use_hidd=False)
            raw = _wait(dev, iface, lambda r: decode(protocol, r, command=command) is not None)
            if raw:
                return decode(protocol, raw, command=command), raw
        return None, b''
    packet = bytearray(iface.output_len)
    if protocol == 'dareu':
        packet[0], packet[1], packet[-1] = 8, 4, 0x49
    elif protocol == 'ajazz':
        packet[:3] = b'\xf0\x30\x01'
    elif protocol == 'hecate':
        packet[0], packet[1], packet[3], packet[4] = 4, 0x20, 0x1A, 6
    else:
        return None, b''
    dev.write_output(bytes(packet), use_hidd=False)
    raw = _wait(dev, iface, lambda r: r != bytes(packet) and decode(protocol, r) is not None, 1.2)
    return decode(protocol, raw), raw or b''


class _Listener:
    """Keep unsolicited events between UI refreshes; expire disconnected paths."""
    def __init__(self, iface, protocol):
        self.iface, self.protocol = iface, protocol
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.latest = None
        self.thread = threading.Thread(target=self.run, daemon=True, name='battery-events')
        self.thread.start()

    def run(self):
        try:
            with HidDevice(self.iface.path) as dev:
                descriptor = f'{self.iface.product} {self.iface.manufacturer}'
                while not self.stop.is_set():
                    started = time.monotonic()
                    raw = dev.read_input(timeout_ms=300, length=self.iface.input_len,
                                         stop_flag=self.stop.is_set)
                    parsed = decode(self.protocol, raw, descriptor=descriptor)
                    if parsed:
                        self.latest = (parsed, raw, time.monotonic())
                        self.ready.set()
                    # An unplugged handle may return immediately. Avoid a spin.
                    if time.monotonic() - started < 0.02:
                        self.stop.wait(0.1)
        except OSError:
            pass


_listeners = {}
_listeners_lock = threading.Lock()


def prune_listeners(ifaces):
    present = {d.path for d in ifaces}
    with _listeners_lock:
        for path in list(_listeners):
            if path not in present:
                _listeners.pop(path).stop.set()


def read(iface, result_type):
    profile = profile_for(iface)
    if not profile:
        return None
    extra = {'model_name': profile.model}
    descriptor = f'{iface.manufacturer or ""} {iface.product or ""}'.strip()
    if (' / ' in profile.model and profile.protocol in ('aula', 'akko')
            and any(brand in descriptor.casefold() for brand in ('aula', 'ajazz', 'akko', 'monsgeek', '狼蛛', '黑爵', '艾酷'))):
        extra['model_name'] = descriptor + ' 键盘'
    source = f'{profile.protocol.upper()} 2.4G 电量协议'
    failure = result_type(ok=False, source=source, extra=extra,
                          detail='尚未收到有效电量，请唤醒设备后刷新')
    if not matches_interface(iface):
        failure.detail = '该接口不是已核实的电量通道'
        return failure
    try:
        if profile.protocol in ('incott', 'delux'):
            with _listeners_lock:
                listener = _listeners.get(iface.path)
                if listener is None or not listener.thread.is_alive():
                    listener = _listeners[iface.path] = _Listener(iface, profile.protocol)
            listener.ready.wait(0.4)
            latest = listener.latest
            if latest is None or time.monotonic() - latest[2] > 120:
                failure.detail = '等待接收器主动上报电量，请移动或唤醒鼠标'
                return failure
            parsed, raw, _ = latest
        else:
            with _io_lock, HidDevice(iface.path, write=True) as dev:
                parsed, raw = _active_read(dev, iface, profile.protocol)
        if parsed is None:
            return failure
        level, status, model = parsed
        if model:
            extra['model_name'] = model
        return result_type(ok=True, level=level, raw_level=level, status=status,
                           source=source, raw=raw, extra=extra)
    except OSError as error:
        failure.detail = f'无法读取设备：{error}'
        return failure
