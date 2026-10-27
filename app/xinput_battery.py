"""Read XInput battery categories; never turn four categories into percentages.

XInput identifies a user slot, not a USB model or a physical device. Slots can
be reassigned. This module does not read buttons or invoke XInputSetState.
"""
import ctypes
from dataclasses import dataclass
from functools import lru_cache

LEVELS = ('空', '低', '中', '满')
TYPES = {0: '未连接', 1: '有线供电', 2: '碱性电池', 3: '镍氢电池', 255: '未知电池类型'}


class BatteryInformation(ctypes.Structure):
    _fields_ = [('BatteryType', ctypes.c_ubyte), ('BatteryLevel', ctypes.c_ubyte)]


@dataclass(frozen=True)
class ControllerBattery:
    slot: int
    battery_type: int
    battery_level: int
    label: str
    reason: str


def decode(slot, battery_type, battery_level):
    if battery_type == 0:
        return None
    label = LEVELS[battery_level] if battery_type in (2, 3) and battery_level in range(4) else ''
    reason = TYPES.get(battery_type, '未知电池类型')
    if label:
        reason += ' · 系统仅提供四档电量，无百分比'
    elif battery_type != 1:
        reason += ' · 系统未提供有效电量档位'
    return ControllerBattery(slot, battery_type, battery_level, label, reason)


@lru_cache(maxsize=1)
def api():
    for name in ('xinput1_4.dll', 'xinput1_3.dll'):
        try:
            # LOAD_LIBRARY_SEARCH_SYSTEM32: never load a DLL beside the EXE.
            dll = ctypes.WinDLL(name, winmode=0x800)
            fn = dll.XInputGetBatteryInformation
            fn.argtypes = [ctypes.c_uint32, ctypes.c_ubyte, ctypes.POINTER(BatteryInformation)]
            fn.restype = ctypes.c_uint32
            return fn
        except (OSError, AttributeError):
            continue
    return None


def collect(query=None):
    query = api() if query is None else query
    if query is None:
        return []
    result = []
    for slot in range(4):
        info = BatteryInformation()
        code = query(slot, 0, ctypes.byref(info))  # BATTERY_DEVTYPE_GAMEPAD
        if code == 1167:  # ERROR_DEVICE_NOT_CONNECTED
            continue
        if code:
            raise OSError(code, 'XInput 电量读取失败')
        parsed = decode(slot, info.BatteryType, info.BatteryLevel)
        if parsed:
            result.append(parsed)
    return result


def readings(custom_models, presence=None):
    from battery import DiscoveredReading
    result = []
    try:
        for item in collect():
            key = f'XINPUT:{item.slot}'
            result.append(DiscoveredReading(
                key=key, title=custom_models.get(key) or f'XInput 手柄 {item.slot + 1}',
                subtitle='系统手柄槽位 · 不代表固定型号或连接方式',
                level_label=item.label, status='电量档位' if item.label else item.reason,
                detail=item.reason, error='' if item.label else item.reason,
                extra='槽位可能重新分配；与蓝牙列表不自动合并'))
    except OSError as exc:
        if presence is not None:
            presence['xinput_error'] = str(exc)
    return result
