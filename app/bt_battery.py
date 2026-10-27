# -*- coding: utf-8 -*-
"""bt_battery.py —— 读取蓝牙耳机/音箱的电量（Windows 未公开 PnP 属性）

原理
----
Windows 的蓝牙协议栈收到耳机通过 HFP（免提协议）上报的电量后，会把它写进
设备 **Hands-Free / 免提** 节点上的一个未公开 PnP 属性：

    PROPERTYKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2   (DEVPROP_TYPE_BYTE)
    值 = 电量百分比（0-100）

电量可能挂在免提服务节点，连接状态却在物理主节点。
按 Windows ContainerId 合并同一设备，连接状态与电量分别选取。
服务节点未连接时的电量标为系统缓存，不推断上报时间；0 是有效电量。
已连接但没有电量属性的设备也保留，避免从设备列表中消失。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
import re
import uuid
from typing import Optional

from hid_win import GUID, setupapi, kernel32

# 蓝牙电量属性：{104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2
FMTID_BLUETOOTH = GUID(
    0x104EA319, 0x6EE2, 0x4701,
    (ctypes.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5),
)
PID_BATTERY_LEVEL = 2

DIGCF_ALLCLASSES = 0x04
DIGCF_PRESENT_ = 0x02
SPDRP_FRIENDLYNAME = 0x0C
SPDRP_DEVICEDESC = 0x00
ERROR_NOT_FOUND = 1168

# 蓝牙类 {e0cbf06c-cd8b-4647-bb8a-263b43f0f974}
CLASS_BLUETOOTH = GUID(
    0xE0CBF06C, 0xCD8B, 0x4647,
    (ctypes.c_ubyte * 8)(0xBB, 0x8A, 0x26, 0x3B, 0x43, 0xF0, 0xF9, 0x74),
)
# 系统类 {4d36e97d-e325-11ce-bfc1-08002be10318}（HFP 免提节点在这里）
CLASS_SYSTEM = GUID(
    0x4D36E97D, 0xE325, 0x11CE,
    (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18),
)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(PROPERTYKEY),
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]


# 设备连接状态属性 DEVPKEY_Device_IsConnected: {83DA6326-97A6-4088-9453-A1923F573B29}, 15
FMTID_DEVICE_CONNECTED = GUID(
    0x83DA6326, 0x97A6, 0x4088,
    (ctypes.c_ubyte * 8)(0x94, 0x53, 0xA1, 0x92, 0x3F, 0x57, 0x3B, 0x29),
)
PID_DEVICE_CONNECTED = 15
FMTID_CONTAINER = GUID(0x8C7ED206, 0x3F8A, 0x4827,
    (ctypes.c_ubyte * 8)(0xB3,0xAB,0xAE,0x9E,0x1F,0xAE,0xFC,0x6C))


@dataclass
class BluetoothDevice:
    name: str
    instance_id: str
    battery: Optional[int] = None
    has_property: bool = False
    connected: Optional[bool] = None
    reason: str = ""
    container_id: str = ""
    cached: bool = False
    headset: bool = False
    source_instance_id: str = ""

    @property
    def is_handsfree(self) -> bool:
        low = self.name.lower()
        return "hands-free" in low or "免提" in self.name or "handsfree" in low

    @property
    def is_ble(self) -> bool:
        return self.instance_id.upper().startswith("BTHLE\\")


def _device_string(hdev, info, prop: int) -> str:
    buf = ctypes.create_string_buffer(1024)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(info), prop, None, buf, 1024, ctypes.byref(need)):
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0]


def _instance_id(hdev, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceInstanceIdW(
            hdev, ctypes.byref(info), buf, 512, ctypes.byref(need)):
        return ""
    return buf.value


def _read_connected_property(hdev, info) -> Optional[bool]:
    key = PROPERTYKEY(FMTID_DEVICE_CONNECTED, PID_DEVICE_CONNECTED)
    ptype = wintypes.ULONG(0)
    value = ctypes.c_byte(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 1, ctypes.byref(need), 0,
    )
    if not ok:
        return None
    return bool(value.value)


def _read_battery_property(hdev, info) -> tuple[Optional[int], bool, str]:
    """返回 (电量, 属性是否存在, 说明)。"""
    key = PROPERTYKEY(FMTID_BLUETOOTH, PID_BATTERY_LEVEL)
    ptype = wintypes.ULONG(0)
    value = ctypes.c_ubyte(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 1, ctypes.byref(need), 0,
    )
    if not ok:
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None, False, "该设备没有电池属性（可能未连接或不上报电量）"
        return None, False, f"读取失败 err={err}"
    level = int(value.value)
    if ptype.value != 3 or need.value != 1 or level > 100:
        return None, False, "Windows 电量属性类型或数值无效"
    return level, True, "来自 Windows 蓝牙电量属性（HFP/GATT，上报时间未知）"


def _read_container(hdev, info):
    key=PROPERTYKEY(FMTID_CONTAINER,2)
    value=(ctypes.c_ubyte*16)()
    ptype=wintypes.ULONG();need=wintypes.DWORD()
    ok=setupapi.SetupDiGetDevicePropertyW(hdev,ctypes.byref(info),ctypes.byref(key),
        ctypes.byref(ptype),value,16,ctypes.byref(need),0)
    if not ok or ptype.value!=13 or need.value!=16:
        return ''
    ident=uuid.UUID(bytes_le=bytes(value))
    return str(ident) if ident.int else ''


def list_bluetooth_devices() -> list[BluetoothDevice]:
    """列出所有蓝牙相关设备（含经典蓝牙 BTHENUM 与低功耗蓝牙 BTHLE）及其电量。"""
    found: dict[str, BluetoothDevice] = {}
    attempts = (
        (None, None, DIGCF_ALLCLASSES),
        ("BTHENUM", None, 0),
        ("BTHLE", None, 0),
        (None, CLASS_SYSTEM, 0),
        (None, CLASS_BLUETOOTH, 0),
    )
    for enumerator, class_guid, flags in attempts:
        hdev = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(class_guid) if class_guid is not None else None,
            enumerator, None, flags | DIGCF_PRESENT_,
        )
        if not hdev or hdev == ctypes.c_void_p(-1).value:
            if flags & DIGCF_ALLCLASSES:
                raise OSError('无法枚举蓝牙设备连接状态')
            continue
        try:
            index = 0
            while True:
                info = SP_DEVINFO_DATA()
                info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                if not setupapi.SetupDiEnumDeviceInfo(hdev, index, ctypes.byref(info)):
                    if ctypes.get_last_error() != 259:  # ERROR_NO_MORE_ITEMS
                        raise OSError('蓝牙设备枚举未完成')
                    break
                index += 1
                iid = _instance_id(hdev, info)
                up = iid.upper()
                if not (up.startswith("BTHENUM\\") or up.startswith("BTHLE\\")) or iid in found:
                    continue
                name = _device_string(hdev, info, SPDRP_FRIENDLYNAME)
                if not name:
                    name = _device_string(hdev, info, SPDRP_DEVICEDESC)
                level, has, reason = _read_battery_property(hdev, info)
                conn = _read_connected_property(hdev, info)
                if has and conn is False:
                    reason = f"{reason}（当前未连接，显示为系统缓存值）"
                found[iid] = BluetoothDevice(
                    name=name or "(无名称)", instance_id=iid,
                    battery=level, has_property=has, connected=conn, reason=reason,
                    container_id=_read_container(hdev,info),
                )
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return list(found.values())


def find_battery_devices(connected_only: bool = False) -> list[BluetoothDevice]:
    """Return battery-bearing physical devices, after combining their service nodes."""
    devs = [d for d in group_devices(list_bluetooth_devices(),connected_only)
            if d.has_property and d.battery is not None]
    devs.sort(key=lambda d: (
        0 if d.connected is True else 1,
        0 if (d.is_handsfree or d.is_ble) else 1,
        -(d.battery or 0),
    ))
    return devs


def physical_key(dev):
    if dev.container_id:
        return 'container:'+dev.container_id.casefold()
    # Conservative fallback for Windows Bluetooth enumerator instance paths.
    up=dev.instance_id.upper()
    match=re.search(r'(?:DEV_|BLUETOOTHDEVICE_)([0-9A-F]{12})(?:\\|$)',up)
    if not match:
        match=re.search(r'&([0-9A-F]{12})_C[0-9A-F]+$',up)
    if match and int(match[1],16):
        return 'address:'+match[1]
    return 'node:'+up


def group_devices(devices,connected_only=True):
    groups={}
    for dev in devices:
        groups.setdefault(physical_key(dev),[]).append(dev)
    result=[]
    for nodes in groups.values():
        connected=any(d.connected is True for d in nodes)
        if connected_only and not connected:
            continue
        mains=[d for d in nodes if re.match(r'^BTH(?:ENUM|LE)\\DEV_',d.instance_id,re.I)]
        base=next((d for d in mains if d.connected is True),None) or (mains[0] if mains else nodes[0])
        sources=[d for d in nodes if d.has_property and d.battery is not None]
        sources.sort(key=lambda d:(d.connected is not True,not (d.is_handsfree or d.is_ble),d.instance_id))
        source=sources[0] if sources else None
        cached=bool(source and source.connected is not True)
        reason=('Windows 系统缓存电量；设备已连接，电量服务节点未连接，上报时间未知'
                if cached else source.reason if source else '已连接，但 Windows 尚未提供电量')
        headset=any(d.is_handsfree or '{0000110B-' in d.instance_id.upper() or
                    '{0000111E-' in d.instance_id.upper() for d in nodes)
        result.append(replace(base,name=re.sub(r'\s+Hands-Free AG$', '',base.name,flags=re.I),
            connected=True if connected else False if all(d.connected is False for d in nodes) else None,
            battery=source.battery if source else None,has_property=source is not None,
            reason=reason,cached=cached,headset=headset,
            source_instance_id=source.instance_id if source else ''))
    return sorted(result,key=lambda d:(d.name.casefold(),d.instance_id))


def connected_devices(presence=None):
    """Include connected devices without battery data so they do not disappear."""
    devices = group_devices(list_bluetooth_devices(), connected_only=False)
    if presence is not None:
        presence['bt_unknown'] = {f'BT:{d.instance_id}' for d in devices if d.connected is None}
    return [d for d in devices if d.connected is True]


def displayable_devices() -> list[BluetoothDevice]:
    """界面用：只保留“可能有电量”的节点，避免把 AVRCP 之类的杂项全列出来。"""
    keys = ("hands-free", "免提", "handsfree", "ag audio")
    out = []
    for d in list_bluetooth_devices():
        low = d.name.lower()
        if d.has_property or any(k in low for k in keys):
            out.append(d)
    return out


def refresh(dev: BluetoothDevice) -> BluetoothDevice:
    """重新读一次指定设备的电量。"""
    for d in list_bluetooth_devices():
        if d.instance_id == dev.instance_id:
            return d
    dev.battery = None
    dev.has_property = False
    dev.reason = "设备已不存在（已断开）"
    return dev


if __name__ == "__main__":
    print("=== 所有蓝牙 HID/音频节点 ===")
    for d in list_bluetooth_devices():
        tag = "[有电量]" if (d.has_property and d.battery) else "        "
        conn_str = "已连接" if d.connected is True else ("未连接" if d.connected is False else "状态未知")
        print(f" {tag} {d.name}   电量={d.battery}% ({conn_str})  {d.reason}")
        print(f"          {d.instance_id}")

