"""One cross-process acquisition, atomic snapshots, and last-good battery values.

Consumers poll the small snapshot, never clear known values during a scan, and
never represent a retained value as a fresh hardware measurement.
"""
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import battery
import device_io
import tray_common as tc

_memory = {}


def snapshot_path():
    return Path(tc.DATA_DIR) / 'battery_snapshot_v1.json'


def empty_snapshot():
    return dict(revision='', scanned_at=0, readings=[])


def due(snapshot=None):
    snapshot = load_snapshot() if snapshot is None else snapshot
    age = time.time() - snapshot['scanned_at']
    interval = max(1, int(tc.load_config().get('interval', 60)))
    return not snapshot['revision'] or age < 0 or age >= interval


def load_snapshot():
    path = snapshot_path()
    try:
        data = json.loads(path.read_text(encoding='utf8'))
        if not isinstance(data, dict) or not isinstance(data.get('readings'), list):
            raise ValueError('Invalid snapshot')
        if not isinstance(data.get('revision'), str) or not isinstance(data.get('scanned_at'), (int, float)):
            raise ValueError('Invalid snapshot metadata')
        # Validate records before replacing the in-process fallback.
        readings_from(data)
        memory = _memory.get(str(path))
        if memory and memory['scanned_at'] > data['scanned_at']:
            return memory
        _memory[str(path)] = data
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return _memory.get(str(path), empty_snapshot())


def readings_from(snapshot):
    known = {f.name for f in fields(battery.DiscoveredReading)}
    result = []
    for item in snapshot['readings']:
        if not isinstance(item, dict):
            raise ValueError('Invalid device record')
        r = battery.DiscoveredReading(**{k:v for k,v in item.items() if k in known})
        if not isinstance(r.key, str) or not r.key:
            raise ValueError('Invalid device key')
        if r.level is not None and (type(r.level) is not int or not 0 <= r.level <= 100):
            raise ValueError('Invalid percentage')
        if r.level_label not in ('', '空', '低', '中', '满',
                                 '0/4 档', '1/4 档', '2/4 档', '3/4 档', '4/4 档'):
            raise ValueError('Invalid battery category')
        if r.updated_at is not None and not isinstance(r.updated_at, (int, float)):
            raise ValueError('Invalid timestamp')
        if r.stale and (r.level is not None or r.level_label):
            stamp = time.strftime('%m-%d %H:%M:%S', time.localtime(r.updated_at)) if r.updated_at else '时间未知'
            r = replace(r, status=f'上次电量 · {stamp}',
                        detail=f'{r.detail} · {r.error or "等待设备更新"}'.strip(' ·'))
        result.append(r)
    return result


def merge_snapshot(previous, incoming, now, error='', presence=None):
    # Keep raw status in storage so repeated failures do not accumulate labels.
    old = {r['key']:dict(r) for r in previous['readings']}
    seen = set()
    for reading in incoming:
        if not reading.key:
            continue
        seen.add(reading.key)
        cached = old.get(reading.key)
        valid = type(reading.level) is int and 0 <= reading.level <= 100
        sleeping = '休眠' in reading.status or reading.stale
        if (valid or reading.level_label) and not sleeping:
            old[reading.key] = asdict(replace(reading, updated_at=now, stale=False))
        elif cached is not None and (cached.get('level') is not None or
                                     cached.get('level_label') and not reading.key.startswith('XINPUT:')):
            cached.update(stale=True, error=reading.error or reading.status or '本次读取未成功')
            # Custom model names can change while a mouse is asleep.
            cached['title'] = reading.title
        elif valid:
            old[reading.key] = asdict(replace(reading, stale=True))
        else:
            old[reading.key] = asdict(replace(reading, level=None, stale=False))
    presence = presence or {}
    for key in old.keys() - seen:
        # A receiver collection still exists even when its paired slot is empty.
        if key in presence.get('excluded_keys', ()):
            del old[key]
            continue
        # Absence after a successful enumeration is a disconnect, not a failed
        # battery read. Keep only devices whose connection is still present or
        # could not be checked reliably in this scan.
        bt = key.startswith('BT:')
        retained = (error or
                    (key.startswith('XINPUT:') and presence.get('xinput_error')) or
                    (bt and (presence.get('bt_error') or key in presence.get('bt_unknown', ()))) or
                    (not bt and (key in presence.get('hid_keys', ()) or
                                 key[:9] in presence.get('hid_unknown', ()))))
        if retained:
            if key.startswith('XINPUT:'):
                # A slot can be reassigned: never retain its old category as live.
                old[key].update(level_label='', error=error or presence.get('xinput_error') or '等待手柄更新')
                continue
            old[key].update(stale=old[key].get('level') is not None or bool(old[key].get('level_label')),
                            error=error or (presence.get('bt_error') if bt else '') or
                                  '设备仍存在或连接状态暂时未知，等待电量更新')
        else:
            del old[key]
    return dict(revision=uuid.uuid4().hex, scanned_at=now, readings=list(old.values()))


def save_snapshot(data):
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A failed disk write must not erase the current process's last-good values.
    _memory[str(path)] = data
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent,
                                         prefix='battery_snapshot_', suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump(data, f, ensure_ascii=False)
        # Windows readers can briefly hold a non-delete-sharing handle while
        # reading JSON. Retry the atomic rename; never truncate the live file.
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.02)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def acquisition_lock():
    kernel = device_io.kernel
    handle = kernel.CreateMutexW(None, False, 'Local\\DeviceBatteryViewer.SharedAcquisitionV1')
    if not handle:
        raise OSError('无法建立共享采集锁')
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        yield acquired
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def collect(force=False):
    requested_at = time.time()
    with acquisition_lock() as acquired:
        previous = load_snapshot()
        if not acquired:
            return previous  # The active collector will publish to every consumer.
        now = time.time()
        interval = max(1, int(tc.load_config().get('interval', 60)))
        age = now - previous['scanned_at']
        if previous['revision'] and ((not force and 0 <= age < interval)
                                     or previous['scanned_at'] > requested_at):
            return previous
        error = ''
        presence = {}
        try:
            incoming = battery.discover_and_read_all(include_bt=True, presence=presence)
        except Exception as exc:
            incoming, error = [], f'采集暂时失败：{exc}'
        data = merge_snapshot(previous, incoming, time.time(), error, presence)
        try:
            save_snapshot(data)
        except OSError:
            pass
        return data
