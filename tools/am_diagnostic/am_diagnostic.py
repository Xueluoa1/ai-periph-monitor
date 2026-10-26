"""Windows Angry Miao diagnostic v1.1. Queries only; no configuration setters.

Protocol reference: official index-94f0d044.js, SHA256
6f2aad885b122db1131e57d14dd936525ec8ee5e9eb1e6fefb26314a0b84136f.
Only exact normal-mode identities may receive the allowlisted commands below.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import zipfile

VERSION = '1.1'
MODELS = {
    (0x0E8D, 0x0880): ('AM Infinity .97', 'am35', 'usb'),
    (0x0E8D, 0x0703): ('AM Infinity .97', 'am35', 'receiver'),
    (0x35A1, 0x0035): ('AM35 compatible identity', 'am35', 'unknown'),
    (0x3151, 0x402A): ('AM Infinity .100', 'am30', 'usb'),
    (0x3151, 0x5007): ('AM Infinity .100', 'am30', 'receiver'),
    (0x0E8D, 0x0881): ('AM Infinity .100R2', 'am35', 'usb'),
}
INVENTORY_IDS = set(MODELS) | {(0x0E8D, 0x0704), (0x3151, 0x5006),
                              (0x3151, 0x402B), (0x3151, 0x402E)}
RACE_READS = frozenset((0x30C9, 0x30C5, 0x30CF))  # profile, DPI, battery
AM30_READS = frozenset((0x85, 0xD4, 0xD6, 0xAD, 0x80, 0xF0, 0xFB))


def emit(event, **fields):
    print(json.dumps(dict(event=event, time=time.time(), **fields),
                     ensure_ascii=False), flush=True)


def token(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def candidate(d):
    name = (d.product + ' ' + d.manufacturer).lower()
    return (d.vid, d.pid) in INVENTORY_IDS or any(
        part in name for part in ('angry miao', 'angrymiao', 'am infinity', '怒喵', 'am35', 'am30'))


def metadata(d):
    spec = MODELS.get((d.vid, d.pid))
    return dict(interface_id=token(d.path), vid=f'{d.vid:04X}', pid=f'{d.pid:04X}',
                product=d.product, manufacturer=d.manufacturer, bcd_device=f'{d.version:04X}',
                usage_page=f'{d.usage_page:04X}', usage=f'{d.usage:04X}',
                input_length=d.input_len, output_length=d.output_len,
                feature_length=d.feature_len, reserved=d.reserved, error=d.error,
                model=spec[0] if spec else 'Inventory only / unknown firmware identity',
                protocol=spec[1] if spec else None, connection=spec[2] if spec else None)


def report_ids(dev, hw, report_type):
    """Read both value and button caps: either can declare a report ID."""
    if ctypes.sizeof(hw.HIDP_BUTTON_CAPS) != 72 or hw.HIDP_BUTTON_CAPS.u.offset != 56:
        raise RuntimeError('HIDP_BUTTON_CAPS layout mismatch; stopping before native call')
    ids = {int(cap.ReportID) for cap in dev.value_caps(report_type)}
    count = ctypes.c_ushort(0)
    hw.hid.HidP_GetButtonCaps(report_type, None, ctypes.byref(count), dev._preparsed)
    if count.value:
        caps = (hw.HIDP_BUTTON_CAPS * count.value)()
        status = hw.hid.HidP_GetButtonCaps(report_type, ctypes.byref(caps),
                                         ctypes.byref(count), dev._preparsed)
        if status == hw.HIDP_STATUS_SUCCESS:
            ids.update(int(cap.ReportID) for cap in caps[:count.value])
    return sorted(ids)


def race_request(command):
    if command not in RACE_READS:
        raise ValueError('Blocked: command is not an approved RACE read')
    return bytes((5, 0x5A, 2, 0, command & 255, command >> 8))


def race_report(command, target, rid, report_length):
    if target not in (0, 0x80) or rid not in (0x14, 6):
        raise ValueError('Unsupported RACE route')
    frame = race_request(command)
    if not 62 <= report_length <= 1024:
        raise ValueError('Unexpected RACE HID report size')
    data = bytes((rid, len(frame), target)) + frame
    return data + bytes(report_length - len(data))


class RaceAssembler:
    """Assemble documented report fragments; never treat request echoes as replies."""
    def __init__(self):
        self.pending = bytearray()
        self.previous = None

    def feed(self, raw, rid, target, input_channel=False):
        if not raw or raw[0] != rid or raw == self.previous:
            return []
        self.previous = raw
        body = raw[1:]
        if len(body) < 2:
            return []
        size = body[0]
        if input_channel and body[1] == 0:
            # Official input path can use a two-byte payload length.
            size = int.from_bytes(body[:2], 'little')
        elif body[1] != target:
            return []
        if not 0 < size <= len(body) - 2:
            return []
        self.pending.extend(body[2:2 + size])
        replies = []
        while len(self.pending) >= 6:
            if self.pending[0] not in (5, 21):
                self.pending.clear()
                break
            size = int.from_bytes(self.pending[2:4], 'little') + 4
            if not 6 <= size <= 4096:
                self.pending.clear()
                break
            if len(self.pending) < size:
                break
            frame = bytes(self.pending[:size])
            del self.pending[:size]
            if frame[1] in (0x5B, 0x5D):
                replies.append((int.from_bytes(frame[4:6], 'little'), frame[6:]))
        return replies


def decode_race_dpi(payload):
    if len(payload) < 3 or payload[0] != 0:
        raise ValueError('Invalid DPI response status/header')
    count, active = payload[1:3]
    if not 1 <= count <= 8 or active >= count:
        raise ValueError('Invalid DPI count/current index')
    slots = 8 if len(payload) >= 35 else count
    if len(payload) < 3 + slots * 4:
        raise ValueError('Truncated DPI arrays')
    stages = []
    for i in range(slots):
        x = int.from_bytes(payload[3 + i*2:5 + i*2], 'little')
        y = int.from_bytes(payload[3 + slots*2 + i*2:5 + slots*2 + i*2], 'little')
        if i < count and (not x or not y):
            raise ValueError('Active DPI slot contains zero; not replacing with defaults')
        stages.append(dict(index=i, stage=i+1, enabled=i < count, x=x, y=y))
    return dict(enabled_count=count, active_index=active, active_stage=active+1, stages=stages)


def am30_request(command, profile=None):
    if command not in AM30_READS:
        raise ValueError('Blocked: command is not an approved AM30 read')
    if profile is not None and (command != 0xD4 or not 0 <= profile <= 7):
        raise ValueError('Invalid DPI profile query')
    header = bytes((command, profile if profile is not None else 0)) + bytes(5)
    return header + bytes((255 - (sum(header) & 255),)) + bytes(56)


def am30_transport(command, argument=None):
    # These establish/read a transient forwarding channel, never change mouse settings.
    if (command, argument) not in ((0xF6, 5), (0xF6, 0), (0xFE, 64),
                                    (0xF7, None), (0xFC, None)):
        raise ValueError('Blocked: unapproved receiver transport command')
    return bytes((command, argument or 0)) + bytes(62)


def decode_am30_dpi(data):
    if len(data) != 64 or data[0] != 0xD4:
        raise ValueError('DPI response length/command mismatch')
    active, count = data[2:4]
    if not 1 <= count <= 8 or active >= count:
        raise ValueError('Invalid DPI count/current index')
    stages = []
    for i in range(8):
        x = int.from_bytes(data[8+i*2:10+i*2], 'little')
        y = int.from_bytes(data[24+i*2:26+i*2], 'little')
        if i < count and (not x or not y):
            raise ValueError('Active DPI slot is zero; retaining raw data for analysis')
        stages.append(dict(index=i, stage=i+1, enabled=i<count, x=x, y=y,
                           color='#' + data[40+i*3:43+i*3].hex()))
    return dict(enabled_count=count, active_index=active, active_stage=active+1,
                header_checksum_ok=(sum(data[:8]) & 255) == 255, stages=stages)


class Reader:
    def __init__(self, dev, iface, ids):
        self.dev, self.iface, self.ids = dev, iface, ids

    def send(self, packet, feature):
        emit('tx', method='feature' if feature else 'output', hex=packet.hex())
        if feature:
            self.dev.set_feature_report(packet)
        else:
            self.dev.write_output(packet, use_hidd=False)

    def receive(self, feature, rid):
        data = (self.dev.get_feature_report(rid, self.iface.feature_len) if feature
                else self.dev.read_input(timeout_ms=100))
        if data:
            emit('rx', method='feature' if feature else 'input', hex=data.hex())
        return data

    def race(self, command, target, feature):
        rid = 0x14 if feature else 6
        length = self.iface.feature_len if feature else self.iface.output_len
        emit('query', protocol='am35', command=f'{command:04X}', target=target)
        assembler = RaceAssembler()
        if not feature:
            # Windows queues input per open handle. Opening after the request can
            # lose an immediate response, so open and drain before transmitting.
            if not self.dev._open_reader():
                raise OSError('Input handle open failed, Windows error=' + str(ctypes.get_last_error()))
            self.dev.drain()
            emit('input_ready', report_id=7, note='Read handle opened before request')
        self.send(race_report(command, target, rid, length), feature)
        deadline = time.monotonic() + 1.8
        while time.monotonic() < deadline:
            time.sleep(0.05)
            raw = self.receive(feature, 0x14 if feature else 7)
            for response_command, payload in assembler.feed(raw, 0x14 if feature else 7,
                                                           target, not feature):
                if response_command == command:
                    if not payload or payload[0] != 0:
                        raise ValueError('Device rejected query, status=' + payload[:1].hex())
                    return payload
        raise TimeoutError('No matching RACE reply; device may be asleep or route unavailable')

    def am30_send(self, data, response=True):
        if len(data) != 64:
            raise ValueError('AM30 payload must be 64 bytes')
        feature = 0 in self.ids['feature'] and self.iface.feature_len == 65
        if not feature:
            raise ValueError('AM30 report 0 feature response interface unavailable')
        self.send(b'\0' + data, feature)
        if not response:
            return None
        time.sleep(0.04)
        raw = self.receive(True, 0)
        if not raw or len(raw) != 65 or raw[0] != 0:
            raise ValueError('AM30 report 0 response length mismatch')
        return raw[1:]

    def am30_status(self, predicate):
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            data = self.am30_send(am30_transport(0xF7))
            if data[4] == 1:
                raise OSError('Receiver reports mouse offline')
            if predicate(data):
                return data
            time.sleep(0.10)
        raise TimeoutError('Receiver forwarding status timed out')

    def am30(self, command, receiver=False, profile=None):
        emit('query', protocol='am30', command=f'{command:02X}', receiver=receiver,
             profile=profile)
        request = am30_request(command, profile)
        if not receiver:
            reply = self.am30_send(request)
        else:
            try:
                self.am30_send(am30_transport(0xF6, 5), False)
                self.am30_status(lambda d: d[5] == 1 or (d[0] == 0 and d[4] == 0 and d[6] == 2))
                self.am30_send(am30_transport(0xFE, 64), False)
                self.am30_send(request, False)
                self.am30_status(lambda d: d[0] in (1, command))
                reply = self.am30_send(am30_transport(0xFC))
            except Exception:
                try:
                    self.am30_send(am30_transport(0xF6, 0), False)
                except Exception as exc:
                    emit('cleanup_error', message=str(exc))
                raise
        if reply[0] != command:
            raise ValueError(f'Response command mismatch: expected {command:02X}, got {reply[0]:02X}')
        return reply


def attempt(label, function):
    try:
        value = function()
        emit('result', label=label, value=value.hex() if isinstance(value, bytes) else value)
        return value
    except Exception as exc:
        emit('query_error', label=label, error_type=type(exc).__name__, message=str(exc))
        return None


def probe(d, hw):
    emit('interface', **metadata(d))
    spec = MODELS.get((d.vid, d.pid))
    if not spec or d.reserved or d.usage_page < 0xFF00 or d.error:
        emit('skipped', reason='Inventory only: unknown, inaccessible or system interface')
        return
    with hw.HidDevice(d.path, write=False) as dev:
        ids = {name: report_ids(dev, hw, kind)
               for name, kind in (('feature', 2), ('output', 1), ('input', 0))}
        emit('report_ids', **ids)
    protocol, connection = spec[1:]
    if protocol == 'am35':
        feature = 0x14 in ids['feature'] and d.feature_len >= 62
        if not feature and not (6 in ids['output'] and 7 in ids['input'] and d.output_len >= 62):
            emit('skipped', reason='No documented RACE report channel')
            return
    else:
        if 0 not in ids['feature'] or d.feature_len != 65:
            emit('skipped', reason='AM30 report 0/64-byte feature payload not available')
            return
    with hw.HidDevice(d.path, write=True) as dev:
        if not dev.write_access:
            raise OSError('Cannot open query transport; close other device configuration software')
        reader = Reader(dev, d, ids)
        if protocol == 'am35':
            receiver_name = any(word in d.product.lower() for word in ('dongle', 'receiver', '2.4'))
            receiver = connection == 'receiver' or receiver_name
            # Documented output/input channel defaults to target 0. Remote 0x80
            # is a separate already-established receiver route, never a link setup.
            targets = ([0x80] if feature else [0, 0x80]) if receiver else [0]
            for target in targets:
                emit('route_policy', target=target, note='Existing route only; no link mode/pairing writes')
                profile = attempt('profile_raw', lambda: reader.race(0x30C9, target, feature))
                data = attempt('dpi_raw', lambda: reader.race(0x30C5, target, feature))
                state = attempt('dpi', lambda: decode_race_dpi(data)) if data else None
                if profile or data:
                    attempt('battery_raw', lambda: reader.race(0x30CF, target, feature))
                if state:
                    break
        else:
            receiver = connection == 'receiver'
            if receiver:
                attempt('receiver_usb_version_raw', lambda: reader.am30(0xF0))
            profile = attempt('profile_raw', lambda: reader.am30(0x85, receiver))
            if profile is None or not 0 <= profile[1] <= 7:
                emit('skipped', reason='No valid current profile; will not guess a profile for DPI read')
                return
            data = attempt('dpi_raw', lambda: reader.am30(0xD4, receiver, profile[1]))
            if data:
                attempt('dpi', lambda: decode_am30_dpi(data))
            attempt('mouse_usb_version_raw', lambda: reader.am30(0xAD, receiver))
            attempt('battery_raw', lambda: reader.am30(0xD6, receiver))


def worker(mode):
    import hid_win as hw
    devices = [d for d in hw.enumerate_hid() if candidate(d)]
    if mode == 'inventory':
        for d in devices:
            emit('inventory', **metadata(d))
        emit('inventory_done', count=len(devices))
        return
    device = next((d for d in devices if token(d.path) == mode), None)
    if device is None:
        raise OSError('Selected interface disappeared; reconnect and rerun')
    probe(device, hw)


def run_child(mode, destination, timeout):
    command = ([sys.executable] if getattr(sys, 'frozen', False)
               else [sys.executable, str(Path(__file__).resolve())])
    with destination.open('w', encoding='utf-8') as log:
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        child = subprocess.Popen(command + ['--worker', mode], stdout=log, stderr=subprocess.STDOUT,
                                 env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            code = -1
            log.write(json.dumps(dict(event='worker_timeout', seconds=timeout)) + '\n')
        if code:
            log.write(json.dumps(dict(event='worker_exit', code=code)) + '\n')
    return load_events(destination)


def load_events(path):
    events = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append(dict(event='worker_text', text=line))
    return events


def main(args):
    print('怒喵鼠标诊断 v' + VERSION)
    print('仅查询配置，不修改 DPI、按键、灯光、配对或固件。')
    print('请关闭 AM Master、官方网页驱动及电量监视器，并唤醒鼠标。')
    print('建议分别用 2.4G 和 USB 数据线各运行一次，保留两份日志。\n')
    if not args.no_pause:
        input('准备好后按回车开始（无需管理员权限）…')
    base = Path(args.output) if args.output else Path(
        sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent / 'logs'
    try:
        base.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=time.strftime('AM诊断_%Y%m%d_%H%M%S_'), dir=base))
    except OSError:
        folder = Path(tempfile.mkdtemp(prefix='AM-diagnostic-'))
    header = dict(version=VERSION, platform=platform.platform(), architecture=platform.machine(),
                  time=time.strftime('%Y-%m-%d %H:%M:%S %z'), inventory_only=args.inventory_only,
                  policy='query-only; no configuration, pairing, firmware or AM35 link-mode writes',
                  source_sha256='6f2aad885b122db1131e57d14dd936525ec8ee5e9eb1e6fefb26314a0b84136f')
    (folder/'run.json').write_text(json.dumps(header, ensure_ascii=False, indent=2), encoding='utf-8')
    print('正在枚举怒喵相关接口…')
    inventory = run_child('inventory', folder/'inventory.jsonl', 25)
    devices = [row for row in inventory if row['event'] == 'inventory']
    completed = any(row['event'] == 'inventory_done' for row in inventory)
    summary = ['怒喵诊断 v' + VERSION, header['time'],
               f'枚举完成：{completed}；候选接口数：{len(devices)}',
               '此工具不包含 DPI/配对/固件写入；原始响应与解析结果均在 JSONL 日志中。']
    successes = 0
    if not args.inventory_only:
        for index, device in enumerate(devices, 1):
            label = f"{device['vid']}:{device['pid']} {device['product']} / {device['usage_page']}:{device['usage']}"
            print(f'[{index}/{len(devices)}] {label}')
            rows = run_child(device['interface_id'], folder/f'interface_{index:02d}.jsonl', 35)
            dpi = next((row['value'] for row in rows if row['event'] == 'result' and row['label'] == 'dpi'), None)
            summary.append('\n' + label)
            if dpi:
                successes += 1
                text = f"读取成功：{dpi['enabled_count']} 档，当前第 {dpi['active_stage']} 档；" + ', '.join(
                    f"{s['stage']}: {s['x']}/{s['y']}" for s in dpi['stages'] if s['enabled'])
            else:
                reasons = [row.get('reason', row.get('message', row['event'])) for row in rows
                           if row['event'] in ('skipped', 'query_error', 'fatal', 'worker_timeout', 'worker_exit')]
                text = '未确认 DPI：' + ('；'.join(reasons) or '请查看原始日志')
            summary.append(text)
            print('  ' + text)
    if not devices:
        summary.append('未找到候选设备。请确认鼠标使用 USB 数据线或对应接收器；蓝牙模式不在本次诊断范围内。'
                       if completed else '枚举未完成，请把错误日志一并提供。')
    summary.append(f'\nDPI 解析成功接口数：{successes}；仅代表读取成功，不代表写入已经适配。')
    (folder/'诊断摘要.txt').write_text('\n'.join(summary), encoding='utf-8-sig')
    archive = folder.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in folder.iterdir():
            if path.is_file():
                z.write(path, path.name)
    print('\n诊断完成。请把这个 ZIP 发回：\n' + str(archive))
    print('日志不会自动上传；不记录鼠标移动、点击、键盘输入或 HID 序列号。')
    if not args.no_pause:
        input('\n按回车退出…')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Angry Miao query-only diagnostic')
    parser.add_argument('--worker', help=argparse.SUPPRESS)
    parser.add_argument('--inventory-only', action='store_true', help='Enumerate only; no HID commands')
    parser.add_argument('--no-pause', action='store_true')
    parser.add_argument('--output', help='Log output directory')
    args = parser.parse_args()
    try:
        if sys.platform != 'win32':
            raise RuntimeError('This diagnostic supports Windows only')
        if args.worker:
            worker(args.worker)
        else:
            sys.exit(main(args))
    except Exception as exc:
        if args.worker:
            emit('fatal', error_type=type(exc).__name__, message=str(exc))
        else:
            print('运行失败：' + str(exc))
            if not args.no_pause:
                input('按回车退出…')
        sys.exit(1)
