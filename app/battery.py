# -*- coding: utf-8 -*-
"""
battery.py —— 电量读取核心

对每个 HID 接口依次尝试多条策略，第一条成功的即可用：

  A. 标准 HID 电量字段（Usage Page 0x0006 / Usage 0x0020 Battery Strength，
     以及数字化仪页 0x000D/0x003B、电池系统页 0x0085 等）—— 读 Feature Report
  B. 已收录的厂商协议（Endgame Gear / VAXEE / Nordic nRF ...）
  C. 罗技 HID++ 2.0（Feature 0x1000 / 0x1004 / 0x1001）
  D. 用户自定义协议（protocols_user.json，由界面上的“手动标定”生成）
  E. 监听 Input Report 里上报的电量字段
  F. 原始 Feature Report 全量转储（给人看的，配合手动标定）
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import hid_win as hw
import darkrim_proto as drp
import peripheral_protocols as peripheral
import jbl_smart_tx as jbl
import am_battery
import gravastar_k1 as k1
import wireless_battery as wireless
import receiver_battery as receivers
from hid_win import HidDevice, HidError, HidInterface, HidP_Feature, HidP_Input


def _app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        d = os.path.join(local_appdata, "Programs", "DeviceBatteryViewer")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d
    return os.path.dirname(os.path.abspath(__file__))


USER_PROTOCOL_FILE = os.path.join(_app_base_dir(), "protocols_user.json")


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------
STATUS_TEXT = {
    "discharging": "放电中",
    "charging": "充电中",
    "full": "已充满",
    "unknown": "未知",
}


@dataclass
class BatteryResult:
    ok: bool
    level: Optional[int] = None           # 0-100
    raw_level: Optional[int] = None       # 原始数值
    status: str = "unknown"
    voltage_mv: Optional[int] = None
    source: str = ""                      # 命中的策略
    detail: str = ""
    raw: bytes = b""
    extra: dict = field(default_factory=dict)

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status or "未知")


def _scale(value: int, logical_max: int = 100) -> int:
    """把原始数值换算成 0-100。"""
    if value is None:
        return None
    if logical_max and logical_max > 0 and logical_max != 100:
        if logical_max == 0xFF and value > 100:
            return max(0, min(100, round(value * 100 / 255)))
        return max(0, min(100, round(value * 100 / logical_max)))
    if value > 100:
        return max(0, min(100, round(value * 100 / 255)))
    return max(0, value)


# --------------------------------------------------------------------------
# 厂商协议表（均来自公开的协议文档与开源逆向实现）
# --------------------------------------------------------------------------
@dataclass
class ProtocolSpec:
    name: str
    vid: int
    usage_page: int
    report_id: int
    payload: list                       # 紧跟 report id 之后的请求字节
    length: int                         # 报文总长度（含 report id）
    level_offset: int                   # 从报文第 0 字节算起的电量字节下标
    wait_ms: int = 120
    scale: int = 1                      # level = raw * scale
    charging_offset: Optional[int] = None
    charging_nonzero: bool = True
    voltage_offset: Optional[int] = None  # 16 位电压起始字节下标（单位 mV）
    voltage_be: bool = False              # True=大端序 16 位，False=小端序 16 位
    level_max: Optional[int] = None       # 超过该值视为无效
    level_min: int = 0                    # 低于该值视为无效/休眠
    mode: str = ""                        # "feature" / "output"，留空则自动判断
    expect: tuple = ()                    # ((字节下标, 期望值), ...) 用于校验回复
    retries: int = 2                      # 重试次数（第一次常被当作唤醒）
    notes: str = ""

    @property
    def kind(self) -> str:
        return self.mode or "feature"


BUILTIN_PROTOCOLS: list[ProtocolSpec] = [
    # ---- 1. ATK / VXE / VGN 无线鼠标（VID 0x373B 为 ATK，0x3554 为 VXE/VGN/Compx）----
    # 协议 2A：64 字节 Output Report，Report ID 0x08，命令 0x72；Nordic 54L15
    ProtocolSpec(
        name="ATK / VXE 无线鼠标（Nordic 54L15 协议 2）",
        vid=0x373B, usage_page=0xFF05, report_id=0x08,
        payload=[0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01],
        length=64, level_offset=7, wait_ms=100, scale=1, level_max=100,
        mode="output", expect=((1, 0x72), (5, 0x07)), retries=3,
        notes="电量在回复第 7 字节；第 1 字节固定 0x72 为有效回复标志",
    ),
    # 协议 1：17 字节 Output Report，Report ID 0x08，命令 0x04；Nordic 52840 + Compx
    # 注意：报文总长 17 字节 = [0x08, 0x04, 0x00*14, 0x49]，末字节校验和 0x55-(0x08+0x04)=0x49
    ProtocolSpec(
        name="ATK / VXE / VGN 无线鼠标（Nordic 52840 17B）",
        vid=0x373B, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=17, level_offset=6, wait_ms=100, scale=1, level_max=100,
        charging_offset=7, voltage_offset=8, voltage_be=True,
        mode="output", expect=((1, 0x04),), retries=3,
        notes="电量在 Byte 6，Byte 7 为充电状态，Byte 8..9 为大端序 mV 电压",
    ),
    # 协议 2B：64 字节 Output Report，Report ID 0x08，命令 0x04；ATK 新款 8K (Nordic 54L15)
    ProtocolSpec(
        name="ATK 8K 无线鼠标（Nordic 54L15 64B）",
        vid=0x373B, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=64, level_offset=6, wait_ms=100, scale=1, level_max=100,
        charging_offset=7, voltage_offset=8, voltage_be=True,
        mode="output", expect=((1, 0x04),), retries=3,
        notes="64 字节扩展帧，电量在 Byte 6",
    ),
    # Compx 0x3554 通用方案（覆盖 VXE / VGN / Pulsar 派世 / Scyrox / Zaopin 造品 / Waizowl 威泽尔 / Dareu 达尔优）
    ProtocolSpec(
        name="VXE / VGN / Pulsar / Scyrox / 造品（Compx 17B）",
        vid=0x3554, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=17, level_offset=6, wait_ms=100, scale=1, level_max=100,
        charging_offset=7, voltage_offset=8, voltage_be=True,
        mode="output", expect=((1, 0x04),), retries=3,
        notes="覆盖 VXE R1、VGN F1、Pulsar X2/Xlite、Scyrox V8、造品 Z1/Z2 等",
    ),
    ProtocolSpec(
        name="VXE / VGN / Pulsar / Scyrox（Compx 64B）",
        vid=0x3554, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 14 + [0x49],
        length=64, level_offset=6, wait_ms=100, scale=1, level_max=100,
        charging_offset=7, voltage_offset=8, voltage_be=True,
        mode="output", expect=((1, 0x04),), retries=3,
        notes="Compx 64B 扩展帧",
    ),
    # ---- 2. Lamzu 兰族 (0x373E) / WLmouse 万灵 (0x342D) ----
    ProtocolSpec(
        name="Lamzu 兰族无线鼠标（Maya X / Atlantis 8K）",
        vid=0x373E, usage_page=0xFFFF, report_id=0x00,
        payload=[0x00, 0x02, 0x02, 0x00, 0x83],
        length=64, level_offset=7, charging_offset=6,
        wait_ms=120, scale=1, level_max=100, mode="feature",
        expect=((0, 0xA1), (2, 0x02), (5, 0x83)), retries=3,
        notes="64 字节 Feature Report，A1 00 02 02 00 83 响应头",
    ),
    ProtocolSpec(
        name="WLmouse 万灵无线鼠标（Beast X 系列）",
        vid=0x342D, usage_page=0xFFFF, report_id=0x00,
        payload=[0x00, 0x02, 0x02, 0x00, 0x83],
        length=64, level_offset=7, charging_offset=6,
        wait_ms=120, scale=1, level_max=100, mode="feature",
        expect=((0, 0xA1), (2, 0x02), (5, 0x83)), retries=3,
        notes="64 字节 Feature Report，A1 00 02 02 00 83 响应头",
    ),
    # ---- 3. Rapoo 雷柏 (0x24AE) VT 系列 2.4G ----
    ProtocolSpec(
        name="Rapoo 雷柏无线鼠标（VT 系列 Report 0x07）",
        vid=0x24AE, usage_page=0xFF00, report_id=0x07,
        payload=[0x00, 0x04],
        length=16, level_offset=3, charging_offset=5,
        wait_ms=120, scale=1, level_max=100, level_min=1, mode="output",
        retries=3, notes="雷柏 V+ 4K/8K 接收器状态帧",
    ),
    ProtocolSpec(
        name="Rapoo 雷柏无线鼠标（VT 系列 Report 0x00）",
        vid=0x24AE, usage_page=0xFF00, report_id=0x00,
        payload=[0x07, 0x00, 0x04],
        length=17, level_offset=4, charging_offset=5,
        wait_ms=120, scale=1, level_max=100, level_min=1, mode="output",
        retries=3, notes="雷柏无 Report ID 映射状态帧",
    ),
    # ---- 4. Endgame Gear / VAXEE / Nordic 公版 ----
    ProtocolSpec(
        name="Endgame Gear（XM2w / OP1w 无线接收器）",
        vid=0x3367, usage_page=0xFF01, report_id=0xA1,
        payload=[0xB4], length=64,
        level_offset=16, wait_ms=350, scale=1,
        voltage_offset=17, mode="feature",
        expect=((1, 0x01),), retries=3,
        notes="byte1 状态：0x01 有效 / 0x03 忙 / 0x07 不支持 / 0x08 鼠标休眠；"
              "需连读两次，第一次是唤醒",
    ),
    ProtocolSpec(
        name="VAXEE / ZYGEN（含 4K 接收器）",
        vid=0x3057, usage_page=0xFF05, report_id=0x0E,
        payload=[0xA5, 0x0B, 0x01, 0x01], length=64,
        level_offset=5, wait_ms=120, scale=5, level_max=20, mode="feature",
        notes="以 5% 为步长上报；充电状态用命令 0x10",
    ),
    ProtocolSpec(
        name="Nordic nRF 方案 2.4G 接收器",
        vid=0x1915, usage_page=0xFFA0, report_id=0x05,
        payload=[0x15, 0x00, 0x00, 0x01], length=32,
        level_offset=9, wait_ms=90, scale=1, mode="feature",
        notes="请求 0x15 读取电量",
    ),
]


def load_user_protocols() -> list[ProtocolSpec]:
    """读取用户通过“手动标定”保存的协议。"""
    if not os.path.exists(USER_PROTOCOL_FILE):
        return []
    try:
        with open(USER_PROTOCOL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(ProtocolSpec(
                name=item.get("name", "自定义协议"),
                vid=int(str(item["vid"]), 16) if isinstance(item["vid"], str) else int(item["vid"]),
                usage_page=int(str(item["usage_page"]), 16) if isinstance(item["usage_page"], str) else int(item["usage_page"]),
                report_id=int(str(item["report_id"]), 16) if isinstance(item["report_id"], str) else int(item["report_id"]),
                payload=list(item.get("payload", [])),
                length=int(item.get("length", 64)),
                level_offset=int(item.get("level_offset", 0)),
                wait_ms=int(item.get("wait_ms", 150)),
                scale=int(item.get("scale", 1)),
                charging_offset=item.get("charging_offset"),
                voltage_offset=item.get("voltage_offset"),
                voltage_be=bool(item.get("voltage_be", False)),
                level_max=item.get("level_max"),
                level_min=int(item.get("level_min", 0)),
                mode=item.get("mode", ""),
                notes=item.get("notes", "用户自定义"),
            ))
        except Exception:
            continue
    return out


def save_user_protocol(spec: ProtocolSpec) -> None:
    raw = []
    if os.path.exists(USER_PROTOCOL_FILE):
        try:
            with open(USER_PROTOCOL_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = []
    raw.append({
        "name": spec.name,
        "vid": f"{spec.vid:04X}",
        "usage_page": f"{spec.usage_page:04X}",
        "report_id": f"{spec.report_id:02X}",
        "payload": list(spec.payload),
        "length": spec.length,
        "level_offset": spec.level_offset,
        "wait_ms": spec.wait_ms,
        "scale": spec.scale,
        "charging_offset": spec.charging_offset,
        "voltage_offset": spec.voltage_offset,
        "voltage_be": spec.voltage_be,
        "level_max": spec.level_max,
        "level_min": spec.level_min,
        "mode": spec.mode,
        "notes": spec.notes,
    })
    with open(USER_PROTOCOL_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def all_protocols() -> list[ProtocolSpec]:
    return BUILTIN_PROTOCOLS + load_user_protocols()



# --------------------------------------------------------------------------
# 策略 A：标准 HID 电量字段
# --------------------------------------------------------------------------
def strategy_standard_battery_usage(iface: HidInterface) -> Optional[BatteryResult]:
    if not iface.feature_len:
        return None
    try:
        with HidDevice(iface.path, write=False) as dev:
            caps_list = dev.value_caps(HidP_Feature)
            for cap in caps_list:
                key = (cap.UsagePage, cap.u.NotRange.Usage)
                match = next((b for b in hw.BATTERY_USAGES if b[0] == key[0] and b[1] == key[1]), None)
                if not match:
                    continue
                try:
                    report = dev.get_feature_report(cap.ReportID)
                except HidError as e:
                    return BatteryResult(
                        ok=False, source="标准 HID 电量字段",
                        detail=f"接口声明了 {match[2]}，但读取 Feature Report "
                               f"0x{cap.ReportID:02X} 失败：{e}",
                    )
                value = dev.usage_value(HidP_Feature, report, cap)
                if value is None:
                    continue
                # Battery System state-of-charge fields already use percent;
                # their descriptor's storage range may still be 0..255.
                if key[0] == 0x85:
                    if value < 0 or (key[1] == 0x64 and value > 100):
                        continue
                    level = min(100, value)
                else:
                    level = _scale(value, cap.LogicalMax)
                return BatteryResult(
                    ok=True, level=level, raw_level=value,
                    status="unknown",
                    source=f"标准 HID 电量字段（{match[2]}）",
                    detail=f"UsagePage=0x{cap.UsagePage:04X} Usage=0x{cap.u.NotRange.Usage:04X} "
                           f"ReportID=0x{cap.ReportID:02X} 原始值={value} 上限={cap.LogicalMax}",
                    raw=report,
                )
    except HidError as e:
        return BatteryResult(ok=False, source="标准 HID 电量字段", detail=str(e))
    return None


# --------------------------------------------------------------------------
# 策略 A2：DARKRIM（杰理方案）2.4G 耳机 dongle —— 逆向得到的 TLV 协议
# --------------------------------------------------------------------------
def strategy_darkrim(iface: HidInterface) -> Optional[BatteryResult]:
    if (iface.vid != drp.VID or iface.usage_page != drp.USAGE_PAGE
            or iface.output_len != drp.REPORT_LEN):
        return None
    info = drp.read_info(iface, timeout=0.35)
    if info.battery is None:
        return BatteryResult(ok=False, source="DARKRIM 私有协议（TLV）",
                             detail=info.detail or "没有取到电量字段")
    return BatteryResult(
        ok=True, level=info.battery, raw_level=info.battery_raw,
        status="unknown", source="DARKRIM 私有协议（TLV over HID）",
        detail=f"{info.detail}；电量 TLV(0x01) 原始值 0x{info.battery_raw:02X}",
        extra={"name": info.name, "dongle_fw": info.dongle_firmware,
               "headset_fw": info.headset_firmware, "address": info.address,
               "status_code": info.status_code, "tlvs": info.raw_tlvs},
    )


# --------------------------------------------------------------------------
# 策略 B / D：厂商协议（含用户自定义）
# --------------------------------------------------------------------------
def _matches(report: bytes, expect: tuple) -> bool:
    return bool(report) and all(
        len(report) > off and report[off] == val for off, val in expect
    )


def _read_report_after_request(dev: HidDevice, spec: ProtocolSpec,
                               kind: Optional[str] = None) -> bytes:
    """按协议发请求、等回复、再读回报文。

    kind="feature"：发 Feature Report，直接读回 Feature Report。
    kind="output" ：发 Output Report，然后等 Input Report；
                    有 expect 时只接受符合特征的回复，否则拿最后一条。
    """
    kind = kind or spec.kind
    body = ([spec.report_id] + list(spec.payload) + [0] * spec.length)[: spec.length]
    last = b""
    for _ in range(max(1, spec.retries)):
        dev.drain()
        if kind == "feature":
            dev.set_feature_report(bytes(body))
            time.sleep(spec.wait_ms / 1000.0)
            last = dev.get_feature_report(spec.report_id, spec.length)
            if not spec.expect or _matches(last, spec.expect):
                return last
            continue
        dev.write_output(bytes(body))
        time.sleep(spec.wait_ms / 1000.0)
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            r = dev.read_input(timeout_ms=150)
            if not r:
                continue
            last = r
            if not spec.expect or _matches(r, spec.expect):
                return r
    return last


def _parse_spec_response(spec: ProtocolSpec, report: bytes, iface: HidInterface,
                         kind: str) -> BatteryResult:
    if not _matches(report, spec.expect):
        return BatteryResult(ok=False, source=spec.name, detail="回复头与电量协议不匹配", raw=report)
    if len(report) <= spec.level_offset:
        return BatteryResult(ok=False, source=spec.name,
                             detail=f"回复长度不足（{len(report)} 字节）", raw=report)
    raw_level = report[spec.level_offset]
    if spec.level_max is not None and raw_level > spec.level_max:
        return BatteryResult(ok=False, source=spec.name,
                             detail=f"电量字节 0x{raw_level:02X} 超出预期范围", raw=report)
    if raw_level < spec.level_min:
        return BatteryResult(ok=False, source=spec.name,
                             detail=f"电量字节为 0x{raw_level:02X}（外设可能处于休眠状态）", raw=report)
    level = max(0, min(100, raw_level * spec.scale))

    status = "unknown"
    if spec.charging_offset is not None and len(report) > spec.charging_offset:
        v = report[spec.charging_offset]
        if spec.charging_nonzero:
            status = "charging" if v else "discharging"
        else:
            status = "discharging" if v else "charging"

    voltage = None
    if spec.voltage_offset is not None and len(report) > spec.voltage_offset + 1:
        if spec.voltage_be:
            voltage = (report[spec.voltage_offset] << 8) | report[spec.voltage_offset + 1]
        else:
            voltage = report[spec.voltage_offset] | (report[spec.voltage_offset + 1] << 8)
        if not (2500 <= voltage <= 5000):
            voltage = None

    return BatteryResult(
        ok=True, level=level, raw_level=raw_level, status=status,
        voltage_mv=voltage, source=spec.name,
        detail=f"ReportID=0x{spec.report_id:02X} 电量字节@{spec.level_offset}=0x{raw_level:02X}"
               f" ×{spec.scale}" + (f" 电压={voltage}mV" if voltage else ""),
        raw=report,
    )



def strategy_vendor_protocol(iface: HidInterface) -> Optional[BatteryResult]:
    specs = [s for s in all_protocols()
             if s.vid == iface.vid and s.usage_page == iface.usage_page]
    if not specs:
        return None
    last = None
    for spec in specs:
        kind = spec.mode or ("feature" if iface.feature_len else "output")
        try:
            with HidDevice(iface.path, write=True) as dev:
                report = _read_report_after_request(dev, spec, kind)
                if not report:
                    last = BatteryResult(ok=False, source=spec.name,
                                         detail="设备没有回复（多半是外设正在休眠，"
                                                "动一下鼠标/开机后重试）")
                    continue
                res = _parse_spec_response(spec, report, iface, kind)
                if res.ok:
                    return res
                last = res
        except HidError as e:
            last = BatteryResult(ok=False, source=spec.name,
                                 detail=f"收发失败：{e}（可能是权限被占用，"
                                        f"请关闭厂商驱动软件后重试）")
    return last


# --------------------------------------------------------------------------
# 策略 C：罗技 HID++ 2.0
# --------------------------------------------------------------------------
HIDPP_DEVICE_INDICES = [0xFF, 1, 2, 3, 4, 5, 6]

_FEATURE_BATTERY_STATUS = 0x1000
_FEATURE_BATTERY_VOLTAGE = 0x1001
_FEATURE_UNIFIED_BATTERY = 0x1004
_FEATURE_DEVICE_NAME = 0x0005

_HIDPP_STATUS = {
    0: ("discharging", "放电中"),
    1: ("charging", "充电中"),
    2: ("charging", "充电中（末段）"),
    3: ("full", "已充满"),
    4: ("charging", "充电中（慢速）"),
    5: ("unknown", "电池类型无效"),
    6: ("unknown", "温度异常"),
    7: ("unknown", "充电异常"),
}


def _hidpp_error(report: bytes) -> Optional[int]:
    if len(report) < 6:
        return None
    if report[2] in (0x8F, 0xFF):
        return report[5]
    return None


def _hidpp_call(dev: HidDevice, rid: int, index: int, feature_index: int,
                function: int, params: list, timeout_ms: int = 260,
                total_len: Optional[int] = None) -> Optional[bytes]:
    body = [rid, index, feature_index, ((function & 0x0F) << 4) | 0x01] + list(params)
    length = total_len or (20 if rid == 0x11 else 7)
    body = (body + [0] * length)[:length]
    try:
        dev.drain()
        dev.write_output(bytes(body))
    except HidError:
        return None
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        report = dev.read_input(timeout_ms=int(min(0.15, deadline - time.monotonic()) * 1000) or 1)
        if not report or len(report) < 4:
            continue
        if (len(report) >= 6 and report[1] == index and report[2] in (0x8F, 0xFF)
                and report[3] == feature_index and report[4] == body[3]):
            return report
        if report[0] != rid or report[1] != index or report[2] != feature_index:
            continue
        if report[3] != body[3]:
            continue
        return report
    return None


def _hidpp_feature_index(dev: HidDevice, rid: int, index: int, feature_id: int) -> Optional[int]:
    reply = _hidpp_call(dev, rid, index, 0x00, 0x00,
                        [feature_id >> 8, feature_id & 0xFF])
    if not reply or _hidpp_error(reply) is not None:
        return None
    return reply[4] if len(reply) > 4 and reply[4] else None


def _hidpp_get_device_name(dev: HidDevice, rid: int, index: int) -> str:
    """通过罗技 HID++ 2.0 Feature 0x0005 (GetDeviceName) 直接从无线外设固件读取完整型号名称。"""
    fidx = _hidpp_feature_index(dev, rid, index, _FEATURE_DEVICE_NAME)
    if not fidx:
        return ""
    reply = _hidpp_call(dev, rid, index, fidx, 0x00, [])
    if not reply or _hidpp_error(reply) is not None or len(reply) < 5:
        return ""
    name_len = reply[4]
    if not (1 <= name_len <= 64):
        return ""
    chars = bytearray()
    while len(chars) < name_len:
        chunk_reply = _hidpp_call(dev, rid, index, fidx, 0x01, [len(chars)])
        if not chunk_reply or _hidpp_error(chunk_reply) is not None or len(chunk_reply) <= 4:
            break
        chunk = chunk_reply[4:]
        if not chunk:
            break
        chars.extend(chunk)
    raw_name = bytes(chars[:name_len]).split(b"\x00", 1)[0]
    try:
        return raw_name.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def strategy_logitech_hidpp(iface: HidInterface) -> Optional[BatteryResult]:
    if iface.vid != 0x046D or not iface.output_len:
        return None
    try:
        with HidDevice(iface.path, write=True) as dev:
            for rid in (0x11, 0x10):
                if dev.caps.OutputReportByteLength and dev.caps.OutputReportByteLength < (
                        20 if rid == 0x11 else 7):
                    continue
                for index in HIDPP_DEVICE_INDICES:
                    res = _logitech_probe_index(dev, iface, rid, index)
                    if res is not None:
                        return res
    except HidError as e:
        return BatteryResult(ok=False, source="罗技 HID++",
                             detail=f"无法打开厂商接口：{e}")
    return None


def _logitech_probe_index(dev: HidDevice, iface: HidInterface, rid: int,
                          index: int) -> Optional[BatteryResult]:
    fidx = _hidpp_feature_index(dev, rid, index, _FEATURE_BATTERY_STATUS)
    if fidx:
        reply = _hidpp_call(dev, rid, index, fidx, 0x00, [])
        if reply and _hidpp_error(reply) is None and len(reply) >= 7 and reply[4] <= 100:
            raw_level, next_level, st = reply[4], reply[5], reply[6]
            status, text = _HIDPP_STATUS.get(st, ("unknown", f"状态 {st}"))
            level = raw_level
            model_name = _hidpp_get_device_name(dev, rid, index)
            return BatteryResult(
                ok=True, level=max(0, min(100, level)), raw_level=raw_level,
                status=status, source="罗技 HID++ 2.0（0x1000 电池状态）",
                detail=f"设备索引 {index}" + (f" [{model_name}]" if model_name else "")
                       + f"：电量={raw_level}% 状态={text}",
                raw=reply,
                extra={"model_name": f"Logitech {model_name}" if model_name and not model_name.lower().startswith("logitech") else model_name},
            )

    fidx = _hidpp_feature_index(dev, rid, index, _FEATURE_UNIFIED_BATTERY)
    if fidx:
        reply = _hidpp_call(dev, rid, index, fidx, 0x01, [])
        if reply and _hidpp_error(reply) is None and len(reply) >= 7 and reply[4] <= 100:
            raw_level, st = reply[4], reply[6]
            # A nonzero level flag with zero percentage means coarse levels only.
            # Do not invent a precise percentage from that flag.
            if raw_level == 0 and reply[5] != 0:
                return BatteryResult(ok=False, source="罗技 HID++ 统一电池",
                                     detail="设备仅返回粗略电量档位，未提供百分比", raw=reply)
            status, text = _HIDPP_STATUS.get(st, ("unknown", f"状态 {st}"))
            model_name = _hidpp_get_device_name(dev, rid, index)
            return BatteryResult(
                ok=True, level=max(0, min(100, raw_level)), raw_level=raw_level,
                status=status, source="罗技 HID++ 2.0（0x1004 统一电池）",
                detail=f"设备索引 {index}" + (f" [{model_name}]" if model_name else "")
                       + f"：电量={raw_level}% 状态={text}",
                raw=reply,
                extra={"model_name": f"Logitech {model_name}" if model_name and not model_name.lower().startswith("logitech") else model_name},
            )

    fidx = _hidpp_feature_index(dev, rid, index, _FEATURE_BATTERY_VOLTAGE)
    if fidx:
        reply = _hidpp_call(dev, rid, index, fidx, 0x00, [])
        if reply and _hidpp_error(reply) is None and len(reply) >= 6:
            mv = (reply[4] << 8) | reply[5]
            if 2000 <= mv <= 5000:
                model_name = _hidpp_get_device_name(dev, rid, index)
                return BatteryResult(
                    ok=True, level=None, voltage_mv=mv, status="unknown",
                    source="罗技 HID++ 2.0（0x1001 电池电压）",
                    detail=f"设备索引 {index}：电压 {mv} mV（该机型不直接给百分比）",
                    raw=reply,
                    extra={"model_name": f"Logitech {model_name}" if model_name and not model_name.lower().startswith("logitech") else model_name},
                )
    return None


# --------------------------------------------------------------------------
# 策略 E：监听 Input Report 上报的电量
# --------------------------------------------------------------------------
def monitor_input_reports(iface: HidInterface, seconds: float = 3.0,
                          on_report: Optional[Callable[[bytes], None]] = None,
                          stop_flag: Optional[Callable[[], bool]] = None
                          ) -> tuple[Optional[BatteryResult], list[bytes]]:
    """在若干秒内监听 Input Report，找上报的电量字段。"""
    if not iface.input_len:
        return None, []
    try:
        dev = HidDevice(iface.path, write=False)
    except HidError as e:
        return BatteryResult(ok=False, source="输入报告监听", detail=str(e)), []
    found: list[bytes] = []
    result: Optional[BatteryResult] = None
    try:
        battery_caps = []
        for cap in dev.value_caps(HidP_Input):
            key = (cap.UsagePage, cap.u.NotRange.Usage)
            if any(b[0] == key[0] and b[1] == key[1] for b in hw.BATTERY_USAGES):
                battery_caps.append(cap)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if stop_flag is not None and stop_flag():
                break
            report = dev.read_input(timeout_ms=250, stop_flag=stop_flag)
            if not report:
                continue
            found.append(report)
            if on_report:
                on_report(report)
            if result is not None:
                continue
            if battery_caps:
                for cap in battery_caps:
                    value = dev.usage_value(HidP_Input, report, cap)
                    if value is None:
                        continue
                    if cap.UsagePage == 0x85:
                        if value < 0 or (cap.u.NotRange.Usage == 0x64 and value > 100):
                            continue
                        level = min(100, value)
                    else:
                        level = _scale(value, cap.LogicalMax)
                    result = BatteryResult(
                        ok=True, level=level, raw_level=value,
                        status="unknown",
                        source=f"输入报告电量字段（UsagePage=0x{cap.UsagePage:04X} "
                               f"Usage=0x{cap.u.NotRange.Usage:04X}）",
                        detail=f"监听 Input Report 得到 原始值={value} 上限={cap.LogicalMax}",
                        raw=report,
                    )
                    break
    except HidError as e:
        return BatteryResult(ok=False, source="输入报告监听", detail=str(e)), found
    finally:
        dev.close()
    if result:
        found = found[-16:]
    return result, found


# --------------------------------------------------------------------------
# 策略 F：原始 Feature Report 转储
# --------------------------------------------------------------------------
def dump_feature_reports(iface: HidInterface) -> list[tuple[int, bytes, str]]:
    """扫描并读回所有报告 ID 的 Feature Report。返回 (report_id, data, 备注)。"""
    out: list[tuple[int, bytes, str]] = []
    if not iface.feature_len:
        return out
    ids: list[int] = []
    try:
        with HidDevice(iface.path, write=False) as dev:
            for cap in dev.value_caps(HidP_Feature):
                if cap.ReportID not in ids:
                    ids.append(cap.ReportID)
    except HidError:
        pass
    for rid in range(0x00, 0x21):
        if rid not in ids:
            ids.append(rid)

    try:
        with HidDevice(iface.path, write=False) as dev:
            for rid in ids:
                try:
                    data = dev.get_feature_report(rid)
                    note = "有数据"
                    if any(1 <= b <= 100 for b in data[1:]):
                        note = "含疑似电量字节"
                    out.append((rid, data, note))
                except HidError as e:
                    out.append((rid, b"", f"读取失败：{e}"))
    except HidError as e:
        out = [(0, b"", f"无法打开接口：{e}")]
    return out


# --------------------------------------------------------------------------
# 调度
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 工具：探测 Report ID / 试发任意命令（配合手动标定）
# --------------------------------------------------------------------------
def probe_report_ids(iface: HidInterface, max_id: int = 0x20) -> list[int]:
    """试出该接口接受哪些 Output Report 的 Report ID（不认识的设备很有用）。

    Windows 对 Report ID 不匹配的写请求会返回 87（参数错误），
    因此用全 0 报文逐个试就能把合法 ID 列出来。
    """
    if not iface.output_len:
        return []
    ok_ids: list[int] = []
    try:
        with HidDevice(iface.path, write=True) as dev:
            length = dev.caps.OutputReportByteLength or iface.output_len
            for rid in range(0, max_id + 1):
                try:
                    dev.write_output(bytes([rid] + [0] * (length - 1)))
                    ok_ids.append(rid)
                except HidError:
                    continue
    except HidError:
        return []
    return ok_ids


def send_command(iface: HidInterface, rid: int, payload: list[int],
                 kind: str = "output", length: Optional[int] = None,
                 wait_ms: int = 350, repeat: int = 2) -> list[bytes]:
    """按指定方式发一条命令并收集回复（原始探测用）。"""
    replies: list[bytes] = []
    try:
        with HidDevice(iface.path, write=True) as dev:
            size = length or (dev.caps.OutputReportByteLength or iface.output_len)
            body = ([rid] + list(payload) + [0] * size)[:size]
            for _ in range(max(1, repeat)):
                try:
                    if kind == "feature":
                        iface_len = dev.caps.FeatureReportByteLength or size
                        body = ([rid] + list(payload) + [0] * iface_len)[:iface_len]
                        dev.set_feature_report(bytes(body))
                        time.sleep(wait_ms / 1000.0)
                        replies.append(dev.get_feature_report(rid, iface_len))
                    else:
                        dev.drain()
                        dev.write_output(bytes(body))
                        end = time.monotonic() + wait_ms / 1000.0
                        while time.monotonic() < end:
                            r = dev.read_input(timeout_ms=100)
                            if r:
                                replies.append(r)
                except HidError as e:
                    replies.append(b"")
                    break
    except HidError:
        return replies
    return replies


# --------------------------------------------------------------------------
# 策略 C2：ASUS ROG 玩家国度（含 ROG Omni Receiver 一拖二接收器与独立键鼠）
# --------------------------------------------------------------------------
ASUS_VID = 0x0B05

ASUS_ROG_MODELS: dict[int, str] = {
    # 接收器
    0x1ACE: "ROG Omni Receiver (一拖二接收器)",
    0x1B0A: "ROG Gladius III EVA-02 (USB)",
    # ROG 鼠标系列 (无线 2.4G / 有线 PID)
    0x1A94: "ROG Harpe Ace Aim Lab Edition (龙鳞)",
    0x1A92: "ROG Harpe Ace Aim Lab Edition (龙鳞)",
    0x1B4C: "ROG Harpe Ace Extreme (龙鳞 Extreme)",
    0x1B4A: "ROG Harpe Ace Extreme (龙鳞 Extreme)",
    0x1B6E: "ROG Harpe Ace Mini (龙鳞 Mini)",
    0x1B6C: "ROG Harpe Ace Mini (龙鳞 Mini)",
    0x1AB5: "ROG Keris II Ace (月刃2 Ace)",
    0x1AB3: "ROG Keris II Ace (月刃2 Ace)",
    0x1A68: "ROG Keris Wireless AimPoint (月刃 AimPoint)",
    0x1A66: "ROG Keris Wireless AimPoint (月刃 AimPoint)",
    0x1960: "ROG Keris Wireless (月刃无线)",
    0x195E: "ROG Keris Wireless (月刃无线)",
    0x1A72: "ROG Gladius III Wireless AimPoint (战刃3 AimPoint)",
    0x1A70: "ROG Gladius III Wireless AimPoint (战刃3 AimPoint)",
    0x197D: "ROG Gladius III Wireless (战刃3无线)",
    0x197B: "ROG Gladius III Wireless (战刃3无线)",
    0x18E5: "ROG Chakram (魔刃)",
    0x18E3: "ROG Chakram (魔刃)",
    0x1A1A: "ROG Chakram X (魔刃X)",
    0x1A18: "ROG Chakram X (魔刃X)",
    0x1979: "ROG Spatha X (斯巴达X)",
    0x1977: "ROG Spatha X (斯巴达X)",
    0x1949: "ROG Strix Impact II Wireless (影刃2无线)",
    0x1947: "ROG Strix Impact II Wireless (影刃2无线)",
    0x1B0C: "ROG Gladius III EVA-02 (2.4G)",
    0x197F: "ROG Gladius III Wireless (战刃3无线)",
    0x1B63: "ROG Harpe Ace Mini (龙鳞 Mini)",
    0x1B16: "ROG Keris II Ace (月刃2 Ace)",
    0x1908: "ROG Pugio II (进刃2无线)",
    0x19F4: "ASUS TUF Gaming M4 Wireless",
    # ROG 键盘系列 (无线 2.4G / 有线 PID)
    0x1A85: "ROG Azoth 无线机械键盘 (夜魔)",
    0x1A83: "ROG Azoth 无线机械键盘 (夜魔)",
    0x1B62: "ROG Azoth Extreme 无线机械键盘 (夜魔 Extreme)",
    0x1B60: "ROG Azoth Extreme 无线机械键盘 (夜魔 Extreme)",
    0x193E: "ROG Falchion 无线机械键盘 (游侠无线)",
    0x193C: "ROG Falchion 无线机械键盘 (游侠无线)",
    0x1B14: "ROG Falchion RX Low Profile (游侠RX矮轴)",
    0x1B12: "ROG Falchion RX Low Profile (游侠RX矮轴)",
    0x1ADE: "ROG Strix Scope II 96 Wireless (游侠2 96%无线)",
    0x1ADC: "ROG Strix Scope II 96 Wireless (游侠2 96%无线)",
    0x1A98: "ROG Strix Scope RX TKL Wireless Deluxe",
    0x19B6: "ROG Claymore II (龙骑士2无线)",
    # ROG 耳机系列
    0x1A5C: "ROG Delta S Wireless (棱镜S无线)",
    0x1B26: "ROG Delta II Wireless (棱镜2无线)",
    0x1B58: "ROG Pelta 无线游戏耳机",
    0x1AE0: "ROG Cetra True Wireless SpeedNova",
}

ASUS_LEGACY_4STEP_PIDS = {0x18E5, 0x1960, 0x1949}

# 缓存最近一次读取成功的设备电量：path -> (level, voltage_mv, time_str, extra_dict)
_LAST_VALID_BATTERY: dict[str, tuple[int, Optional[int], str, dict]] = {}
# 缓存 ROG Omni Receiver 槽位配对的子设备真实 PID：usage_page (0xFF00/0xFF01) -> paired_pid
_OMNI_PAIRED_PIDS: dict[int, int] = {}
# 缓存 ASUS 设备固件版本：path -> (dev_fw, dongle_fw)
_ASUS_FW_CACHE: dict[str, tuple[str, str]] = {}


def _probe_asus_omni_paired_pids() -> dict[int, int]:
    """向 ROG Omni Receiver 的 0xFF02 管理通道发送 [0x01, 0xA0, 0x00] 指令，
    解析当前配对在键盘槽(RID=2/0xFF00)与鼠标槽(RID=3/0xFF01)上的真实外设 PID。
    例如响应 `01 a0 00 01 00 94 1a 03 05` 代表槽位 RID=0x03 (0xFF01 鼠标槽) 配对了 PID=0x1A94 (ROG Harpe Ace Aim Lab Edition)。
    """
    if _OMNI_PAIRED_PIDS:
        return _OMNI_PAIRED_PIDS
    try:
        for d in hw.enumerate_hid():
            if d.vid == ASUS_VID and d.pid == 0x1ACE and d.usage_page == 0xFF02 and d.output_len:
                with HidDevice(d.path, write=True) as dev:
                    out_len = dev.caps.OutputReportByteLength or d.output_len or 64
                    pkt = bytes(([0x01, 0xA0, 0x00] + [0] * out_len)[:out_len])
                    for use_hidd in (False, True):
                        try:
                            dev.drain()
                            dev.write_output(pkt, use_hidd=use_hidd)
                            r = dev.read_input(timeout_ms=150)
                        except HidError:
                            continue
                        if r and len(r) >= 9 and r[1] == 0xA0 and r[2] == 0x00:
                            # 解析配对槽位条目：每 4 字节 [pid_lo, pid_hi, slot_rid, dev_sub]，起始于偏移 5
                            for base in (5, 9, 13):
                                if len(r) >= base + 3:
                                    sub_pid = r[base] | (r[base + 1] << 8)
                                    slot_rid = r[base + 2]
                                    if sub_pid > 0x1000 and sub_pid != 0xFFFF:
                                        if slot_rid == 0x02:
                                            _OMNI_PAIRED_PIDS[0xFF00] = sub_pid
                                        elif slot_rid == 0x03:
                                            _OMNI_PAIRED_PIDS[0xFF01] = sub_pid
                                        elif sub_pid in ASUS_ROG_MODELS:
                                            name = ASUS_ROG_MODELS[sub_pid]
                                            up = 0xFF00 if ("键盘" in name or "Azoth" in name or "Falchion" in name or "Scope" in name) else 0xFF01
                                            _OMNI_PAIRED_PIDS[up] = sub_pid
                            break
                break
    except Exception:
        pass
    return _OMNI_PAIRED_PIDS


def _probe_asus_fw(dev: HidDevice, path: str, rid: int, out_len: int) -> tuple[str, str]:
    """发送 [RID, 0x12, 0x00] 读取 ROG 外设固件与接收器固件版本。"""
    if path in _ASUS_FW_CACHE:
        return _ASUS_FW_CACHE[path]
    dev_fw, dongle_fw = "", ""
    try:
        dev.drain()
        dev.write_output(bytes(([rid, 0x12, 0x00] + [0] * out_len)[:out_len]), use_hidd=False)
        r = dev.read_input(timeout_ms=100)
        if r and len(r) >= 12:
            off = 1 if (r[0] == rid and r[1] == 0x12 and r[2] == 0x00) else (
                0 if (r[0] == 0x12 and r[1] == 0x00) else -1
            )
            if off >= 0:
                if r[off + 4] or r[off + 5] or r[off + 6]:
                    dongle_fw = f"v{r[off + 4]}.{r[off + 5]:02d}.{r[off + 6]:02d}"
                if len(r) >= off + 11 and (r[off + 8] or r[off + 9] or r[off + 10]) and r[off + 8] != 0xFF:
                    dev_fw = f"v{r[off + 8]}.{r[off + 9]:02d}.{r[off + 10]:02d}"
    except Exception:
        pass
    if dev_fw or dongle_fw:
        _ASUS_FW_CACHE[path] = (dev_fw, dongle_fw)
    return dev_fw, dongle_fw


def asus_channel_title(iface: HidInterface) -> str:
    """根据 ROG PID、Omni Receiver 配对探测结果与 Usage Page 返回完整设备型号名称。"""
    if iface.pid == 0x1ACE:
        paired = _probe_asus_omni_paired_pids()
        sub_pid = paired.get(iface.usage_page)
        if sub_pid:
            if sub_pid in ASUS_ROG_MODELS:
                return ASUS_ROG_MODELS[sub_pid]
            kind = "无线键盘" if iface.usage_page == 0xFF00 else "无线鼠标"
            return f"ASUS ROG {kind} (0B05:{sub_pid:04X})"
        if iface.usage_page == 0xFF00:
            return "ASUS ROG 无线键盘 (Omni 通道)"
        if iface.usage_page == 0xFF01:
            return "ASUS ROG 无线鼠标 (Omni 通道)"
    return ASUS_ROG_MODELS.get(iface.pid, iface.product or "ASUS ROG 外设")


def strategy_asus_rog(iface: HidInterface) -> Optional[BatteryResult]:
    """ASUS ROG 无线键鼠 2.4G / 有线电量协议（VID 0x0B05）。

    支持 ROG Omni Receiver（0xFF00 键盘槽 RID=0x02、0xFF01 鼠标槽 RID=0x03）
    以及独立无线接收器（RID=0x00/0x01/0x02/0x03 自适应）。
    请求：[RID, 0x12, 0x07, 0x00...]
    回复：[RID, 0x12, 0x07, 0x00, 0x00, <level>, <sleep>, <warn>, <v_lo>, <v_hi>, <charging>, ...]
    """
    if iface.vid != ASUS_VID or iface.usage_page not in (0xFF00, 0xFF01) or not iface.output_len:
        return None

    # 根据 Usage Page 优先尝试对应的 Report ID，减少无效写入
    if iface.usage_page == 0xFF00:
        cand_rids = (0x02, 0x00, 0x01, 0x03)
    else:
        cand_rids = (0x03, 0x00, 0x01, 0x02)

    try:
        with HidDevice(iface.path, write=True) as dev:
            out_len = dev.caps.OutputReportByteLength or iface.output_len or 64
            for rid in cand_rids:
                body = bytes(([rid, 0x12, 0x07] + [0] * out_len)[:out_len])
                try:
                    dev.drain()
                    dev.write_output(body)
                except HidError:
                    continue

                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    r = dev.read_input(timeout_ms=100)
                    if not r or len(r) < 6:
                        break
                    # 有些固件返回带 RID，有些首字节就是 0x12
                    off = 1 if (r[0] == rid and r[1] == 0x12 and r[2] == 0x07) else (
                        0 if (r[0] == 0x12 and r[1] == 0x07) else -1
                    )
                    if off < 0:
                        continue

                    raw_level = r[off + 4]
                    mv = None
                    if len(r) >= off + 9:
                        cand_mv = r[off + 7] | (r[off + 8] << 8)
                        if 2500 <= cand_mv <= 5000:
                            mv = cand_mv

                    charging_byte = r[off + 9] if len(r) >= off + 10 else 0
                    ch_key_str = f"{iface.vid:04X}:{iface.pid:04X}:{iface.usage_page:04X}"
                    # 全 0 代表该槽位没有配对外设，或外设正处于 2.4G 深度休眠
                    if raw_level == 0 and mv is None and charging_byte == 0 and all(b == 0 for b in r[off + 4: off + 10]):
                        extra_sleep: dict = {"empty_or_sleeping": True}
                        if iface.pid == 0x1ACE:
                            paired_map = _probe_asus_omni_paired_pids()
                            if paired_map and iface.usage_page not in paired_map:
                                extra_sleep["unpaired_omni_slot"] = True
                                return BatteryResult(
                                    ok=False, source="ASUS ROG 无线协议",
                                    detail="该 Omni 接收器通道未配对外设",
                                    raw=r, extra=extra_sleep,
                                )
                            if iface.usage_page in paired_map:
                                p_pid = paired_map[iface.usage_page]
                                extra_sleep["paired_vid_pid"] = f"0B05:{p_pid:04X}"
                                extra_sleep["model_name"] = ASUS_ROG_MODELS.get(
                                    p_pid, f"ASUS ROG 外设 (0B05:{p_pid:04X})"
                                )

                        cached = _LAST_VALID_BATTERY.get(iface.path)
                        if cached is None:
                            try:
                                import tray_common as tc
                                p_cache = (tc.load_config().get("last_readings") or {}).get(ch_key_str)
                                if isinstance(p_cache, dict) and p_cache.get("level") is not None:
                                    cached = (
                                        int(p_cache["level"]),
                                        p_cache.get("mv"),
                                        str(p_cache.get("time") or "近期"),
                                        dict(p_cache.get("extra") or {}),
                                    )
                                    _LAST_VALID_BATTERY[iface.path] = cached
                            except Exception:
                                pass

                        if cached is not None:
                            c_lvl, c_mv, c_time, c_extra = cached
                            merged_extra = {**extra_sleep, **dict(c_extra)}
                            merged_extra["sleeping"] = True
                            return BatteryResult(
                                ok=True, level=c_lvl, raw_level=0,
                                status="discharging", voltage_mv=c_mv,
                                source=f"ASUS ROG 协议（休眠保活，{c_time}）",
                                detail=f"外设当前处于休眠省电状态，显示最近一次有效电量 {c_lvl}%"
                                       + (f"（{c_mv} mV）" if c_mv else ""),
                                raw=r,
                                extra=merged_extra,
                            )
                        return BatteryResult(
                            ok=False, source="ASUS ROG 无线协议",
                            detail="外设正在休眠（晃动鼠标/按键即可唤醒更新实时电量）",
                            raw=r,
                            extra=extra_sleep,
                        )

                    if raw_level > 100:
                        continue
                    if iface.pid in ASUS_LEGACY_4STEP_PIDS:
                        if raw_level > 4:
                            continue
                        level = min(100, raw_level * 25)
                    else:
                        level = max(0, min(100, raw_level))

                    status = "charging" if charging_byte else "discharging"
                    if level >= 100 and status == "charging":
                        status = "full"

                    # 读取固件版本与 Omni 配对真实型号
                    dev_fw, dongle_fw = _probe_asus_fw(dev, iface.path, rid, out_len)
                    paired_pid = None
                    model_name = ASUS_ROG_MODELS.get(iface.pid, "")
                    if iface.pid == 0x1ACE:
                        paired_map = _probe_asus_omni_paired_pids()
                        paired_pid = paired_map.get(iface.usage_page)
                        if paired_pid:
                            model_name = ASUS_ROG_MODELS.get(paired_pid, f"ASUS ROG 外设 (0B05:{paired_pid:04X})")

                    extra_info = {}
                    if model_name:
                        extra_info["model_name"] = model_name
                    if paired_pid:
                        extra_info["paired_vid_pid"] = f"0B05:{paired_pid:04X}"
                    if dev_fw:
                        extra_info["device_fw"] = dev_fw
                    if dongle_fw:
                        extra_info["dongle_fw"] = dongle_fw

                    now_hm = time.strftime("%H:%M")
                    _LAST_VALID_BATTERY[iface.path] = (level, mv, now_hm, extra_info)
                    try:
                        import tray_common as tc
                        cfg_now = tc.load_config()
                        lr = dict(cfg_now.get("last_readings") or {})
                        lr[ch_key_str] = {
                            "level": level,
                            "mv": mv,
                            "time": now_hm,
                            "extra": extra_info,
                        }
                        cfg_now["last_readings"] = lr
                        tc.save_config(cfg_now)
                    except Exception:
                        pass

                    return BatteryResult(
                        ok=True, level=level, raw_level=raw_level,
                        status=status, voltage_mv=mv,
                        source=f"ASUS ROG 无线协议（RID=0x{rid:02X}）",
                        detail=f"指令 12 07 -> 电量={level}%"
                               + (f" 电压={mv}mV" if mv else "")
                               + (f" 型号={model_name}" if model_name else ""),
                        raw=r,
                        extra=extra_info,
                    )
    except HidError as e:
        return BatteryResult(ok=False, source="ASUS ROG 无线协议", detail=f"访问失败：{e}")
    return None


# --------------------------------------------------------------------------
# 策略 C3：怒喵 Angry Miao / Dry Studio（AM Infinity Mouse 8K & AM Master & 键盘）
# --------------------------------------------------------------------------
ANGRY_MIAO_VIDS = {0x3151}  # YICHIP 易兆微 (AM Infinity Mouse 8K 0x3151:0x5007)
ANGRY_MIAO_KEYWORDS = (
    "angry miao", "angrymiao", "dry studio", "cyberboard",
    "am infinity", "am hatsune", "am compact", "black diamond", "am master",
)


def is_angry_miao_iface(iface: HidInterface) -> bool:
    if am_battery.supports_device(iface):
        return True
    if iface.vid == 0x3151 and iface.pid == 0x5007:
        return True
    text = f"{iface.manufacturer} {iface.product}".lower()
    return any(k in text for k in ANGRY_MIAO_KEYWORDS)


def strategy_angry_miao(iface: HidInterface) -> Optional[BatteryResult]:
    """怒喵 Angry Miao / Dry Studio 2.4G 外设电量协议。

    1) AM Infinity Mouse 8K (VID 0x3151, PID 0x5007, UsagePage 0xFFFF):
       发送 0xF7 状态轮询指令激活 2.4G 遥测，读取 Report ID 0x05 输入帧：
       [0x05, 0x00, 0x00, <battery 1..100>, <charging>, ...]
    2) AM Master 协议体系 (AM35 Report ID 0x07 魔数头 0x05 0x5A / Protocol A 65B Feature)
    """
    if am_battery.supports_device(iface):
        return am_battery.read(iface, BatteryResult)
    is_am_vid = (iface.vid in ANGRY_MIAO_VIDS and iface.usage_page in (0xFFFF, 0xFF00, 0xFF01, 0xFF60))
    if not (is_am_vid or is_angry_miao_iface(iface)):
        return None
    if iface.usage_page < 0xFF00:
        return None

    try:
        with HidDevice(iface.path, write=True) as dev:
            out_len = dev.caps.OutputReportByteLength or iface.output_len or 64
            feat_len = dev.caps.FeatureReportByteLength or iface.feature_len or 0

            # 1) 尝试 AM Infinity Mouse 8K (0xF7 轮询 + Report ID 0x05)
            if iface.output_len:
                for rid in (0x00, 0x05):
                    try:
                        dev.drain()
                        body = bytes(([rid, 0xF7] + [0] * out_len)[:out_len])
                        dev.write_output(body)
                    except HidError:
                        continue
                    deadline = time.monotonic() + 0.3
                    while time.monotonic() < deadline:
                        r = dev.read_input(timeout_ms=100)
                        if not r or len(r) < 5:
                            break
                        if r[0] == 0x05 and 1 <= r[3] <= 100:
                            lvl = r[3]
                            chg = "charging" if (len(r) > 4 and r[4]) else "discharging"
                            _LAST_VALID_BATTERY[iface.path] = (lvl, None, time.strftime("%H:%M"), {
                                "model_name": "怒喵 AM Infinity Mouse 8K (无限鼠标)"
                            })
                            return BatteryResult(
                                ok=True, level=lvl, raw_level=lvl, status=chg,
                                source="怒喵 AM Infinity 2.4G 协议（Report 0x05）",
                                detail=f"0xF7 轮询 -> 电量={lvl}% 状态={'充电中' if chg == 'charging' else '放电中'}",
                                raw=r,
                                extra={"model_name": "怒喵 AM Infinity Mouse 8K (无限鼠标)"},
                            )

            # 2) 尝试 AM35 分片帧协议 (Report ID 0x07, 2.4G 代理标志 0x80, 魔数 0x05 0x5A)
            if iface.output_len and out_len >= 16:
                for proxy in (0x80, 0x00):
                    try:
                        dev.drain()
                        pkt = [0x07, proxy, 0x05, 0x5A, 0x02, 0x00, 0x01, 0x00]
                        dev.write_output(bytes((pkt + [0] * out_len)[:out_len]))
                        deadline = time.monotonic() + 0.25
                        while time.monotonic() < deadline:
                            r = dev.read_input(timeout_ms=100)
                            if not r or len(r) < 10:
                                break
                            if r[0] == 0x07 and r[2] == 0x05 and r[3] == 0x5A:
                                for idx in (8, 9, 7):
                                    if len(r) > idx and 1 <= r[idx] <= 100:
                                        lvl = r[idx]
                                        return BatteryResult(
                                            ok=True, level=lvl, raw_level=lvl,
                                            status="unknown",
                                            source="怒喵 AM Master (AM35) 协议",
                                            detail=f"AM35 帧(05 5A) -> 电量={lvl}%",
                                            raw=r,
                                        )
                    except HidError:
                        pass

            # 3) 尝试 AM Master Protocol A (65B Feature Report, 末字节 0xFF - sum 校验)
            if feat_len >= 64:
                for cmd in (0x01, 0x04, 0x83):
                    try:
                        payload = [0x00, cmd] + [0] * (feat_len - 3)
                        chk = (0xFF - (sum(payload[1:]) & 0xFF)) & 0xFF
                        req = bytes(payload + [chk])
                        dev.set_feature_report(req)
                        time.sleep(0.08)
                        resp = dev.get_feature_report(0x00, feat_len)
                        if resp and len(resp) >= 8:
                            for idx in (3, 4, 6, 7):
                                if 1 <= resp[idx] <= 100:
                                    lvl = resp[idx]
                                    return BatteryResult(
                                        ok=True, level=lvl, raw_level=lvl,
                                        status="unknown",
                                        source="怒喵 AM Master (Protocol A) 协议",
                                        detail=f"Feature 0x{cmd:02X} -> 电量={lvl}%",
                                        raw=resp,
                                    )
                    except HidError:
                        continue
    except HidError as e:
        return BatteryResult(ok=False, source="怒喵 Angry Miao 协议", detail=f"访问失败：{e}")
    return None


# --------------------------------------------------------------------------
# 策略 C4：MCHOSE 迈从无线外设协议（方案 A 按位取反 XOR 0xFF / 方案 B G7 系列）
# --------------------------------------------------------------------------
def strategy_mchose(iface: HidInterface) -> Optional[BatteryResult]:
    """迈从 MCHOSE 2.4G 无线鼠标电量协议。"""
    # 方案 A：VID 0x5253 / 0x3837 (A5 / A7 / M7 系列)，XOR 0xFF 编码
    if iface.vid in (0x5253, 0x3837) and iface.usage_page in (0xFF01, 0xFF0B) and iface.output_len:
        try:
            with HidDevice(iface.path, write=True) as dev:
                out_len = dev.caps.OutputReportByteLength or iface.output_len or 64
                raw_cmd = [0x53, 0x52, 0x31, 0x00, 0x02, 0x05, 0x07, 0x00, 0x09]
                encoded = [b ^ 0xFF for b in raw_cmd]
                for rid in (0x11, 0x12):
                    body = bytes(([rid] + encoded + [0xFF] * out_len)[:out_len])
                    try:
                        dev.drain()
                        dev.write_output(body)
                    except HidError:
                        continue
                    deadline = time.monotonic() + 0.25
                    while time.monotonic() < deadline:
                        r = dev.read_input(timeout_ms=100)
                        if not r or len(r) < 13:
                            break
                        dec = bytes(b ^ 0xFF for b in r[1:13])
                        if dec[:3] == b"\x53\x52\x31" and 1 <= dec[9] <= 100:
                            lvl = dec[9]
                            chg = "charging" if dec[11] else "discharging"
                            return BatteryResult(
                                ok=True, level=lvl, raw_level=lvl, status=chg,
                                source="迈从 MCHOSE 无线协议（XOR 编码）",
                                detail=f"RID=0x{rid:02X} 解码后电量={lvl}%",
                                raw=r,
                            )
        except HidError as e:
            return BatteryResult(ok=False, source="迈从 MCHOSE 协议", detail=str(e))

    # 方案 B：VID 0xA8A5 (MCHOSE G7 系列)
    if iface.vid == 0xA8A5 and iface.usage_page == 0xFF01 and iface.output_len:
        try:
            with HidDevice(iface.path, write=True) as dev:
                out_len = dev.caps.OutputReportByteLength or iface.output_len or 65
                req = [0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01]
                dev.drain()
                dev.write_output(bytes((req + [0] * out_len)[:out_len]))
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    r = dev.read_input(timeout_ms=100)
                    if not r or len(r) < 11:
                        break
                    off = 1 if (r[1] == 0xAA and r[2] == 0x30) else (0 if (r[0] == 0xAA and r[1] == 0x30) else -1)
                    if off >= 0 and 1 <= r[off + 8] <= 100:
                        lvl = r[off + 8]
                        chg = "charging" if r[off + 9] else "discharging"
                        return BatteryResult(
                            ok=True, level=lvl, raw_level=lvl, status=chg,
                            source="迈从 MCHOSE G7 无线协议",
                            detail=f"AA 30 响应帧 -> 电量={lvl}%",
                            raw=r,
                        )
        except HidError as e:
            return BatteryResult(ok=False, source="迈从 MCHOSE G7 协议", detail=str(e))
    return None


# --------------------------------------------------------------------------
# 策略 C5：Razer 雷蛇 90 字节 Feature Report 标准协议 (VID 0x1532)
# --------------------------------------------------------------------------
def _razer_packet(tx_id: int, cmd_class: int, cmd_id: int, data_size: int = 2) -> bytes:
    pkt = [0x00] * 91  # 首字节 Report ID 0x00 + 90 字节 Razer 标准帧
    pkt[1] = 0x00      # Status: New Command
    pkt[2] = tx_id     # Transaction ID (0x1F / 0x3F / 0xFF)
    pkt[6] = data_size
    pkt[7] = cmd_class
    pkt[8] = cmd_id
    crc = 0
    for b in pkt[3:89]:
        crc ^= b
    pkt[89] = crc
    return bytes(pkt)


def _valid_razer_reply(resp: bytes, tx_id: int, command: int) -> bool:
    if (not resp or len(resp) != 91 or resp[0] != 0 or resp[1] != 2
            or resp[2] != tx_id or resp[6] != 2 or resp[7] != 7 or resp[8] != command):
        return False
    checksum = 0
    for value in resp[3:89]:
        checksum ^= value
    return checksum == resp[89]


def strategy_razer(iface: HidInterface) -> Optional[BatteryResult]:
    """Razer 雷蛇无线鼠标/键盘 90 字节 Feature Report 电量协议（VID 0x1532）。"""
    if iface.vid != 0x1532 or iface.feature_len < 90:
        return None
    try:
        with HidDevice(iface.path, write=True) as dev:
            for tx_id in (0x1F, 0x3F, 0xFF):
                try:
                    dev.set_feature_report(_razer_packet(tx_id, 0x07, 0x80, 0x02))
                    time.sleep(0.04)
                    resp = dev.get_feature_report(0x00, 91)
                except HidError:
                    continue
                if not resp or len(resp) < 11:
                    continue
                # resp[1] 为 Status (0x02 = Success)，resp[7]=0x07, resp[8]=0x80, resp[10]=raw(0..255)
                if _valid_razer_reply(resp, tx_id, 0x80):
                    raw_val = resp[10]
                    level = max(0, min(100, round(raw_val * 100 / 255)))
                    status = "unknown"
                    try:
                        dev.set_feature_report(_razer_packet(tx_id, 0x07, 0x84, 0x02))
                        time.sleep(0.03)
                        c_resp = dev.get_feature_report(0x00, 91)
                        if _valid_razer_reply(c_resp, tx_id, 0x84) and c_resp[10] in (0, 1):
                            status = "charging" if c_resp[10] else "discharging"
                    except HidError:
                        pass
                    return BatteryResult(
                        ok=True, level=level, raw_level=raw_val, status=status,
                        source=f"Razer 雷蛇无线协议（TxID=0x{tx_id:02X}）",
                        detail=f"Class 0x07 Cmd 0x80 -> 原始值={raw_val}/255 ({level}%)",
                        raw=resp,
                    )
    except HidError as e:
        return BatteryResult(ok=False, source="Razer 雷蛇无线协议", detail=str(e))
    return None


def read_battery(iface: HidInterface, listen_seconds: float = 3.0,
                 progress: Optional[Callable[[str], None]] = None
                 ) -> list[BatteryResult]:
    from device_io import device_session
    try:
        with device_session():
            return _read_battery_unlocked(iface, listen_seconds, progress)
    except TimeoutError as exc:
        return [BatteryResult(ok=False, source='设备通信', detail=str(exc))]


def _read_battery_unlocked(iface: HidInterface, listen_seconds: float = 3.0,
                           progress: Optional[Callable[[str], None]] = None
                           ) -> list[BatteryResult]:
    """按顺序跑所有策略，返回所有尝试记录（第一条 ok=True 的是结论）。"""
    attempts: list[BatteryResult] = []
    if receivers.profile_for(iface):return [receivers.read(iface, BatteryResult)]
    if jbl.claims(iface):return [jbl.battery_read(iface, BatteryResult)]
    import xiaomi_mouse2 as xm
    if xm.claims(iface):return [xm.battery_read(iface,BatteryResult)]
    if is_unidentified_logged_receiver(iface):
        return [BatteryResult(ok=False, source='接收器识别',
            detail='用户确认此接收器连接 RK-G98；共用 ID 无法自动核实型号，2.4G 电量和灯效协议尚未确认')]
    if k1.claims(iface):
        return [k1.battery_read(iface, BatteryResult)]
    if am_battery.supports_device(iface):
        return [am_battery.read(iface, BatteryResult)]
    # Exact receiver routes are terminal: a failed query must not fall through
    # into a different OEM's VID-wide protocol.
    if peripheral.profile_for(iface):
        return [peripheral.read(iface, BatteryResult)]
    if wireless.claims(iface):
        return [wireless.read(iface, BatteryResult)]
    steps = [
        ("标准 HID 电量字段", lambda: strategy_standard_battery_usage(iface)),
        ("ASUS ROG 无线协议", lambda: strategy_asus_rog(iface)),
        ("怒喵 Angry Miao 协议", lambda: strategy_angry_miao(iface)),
        ("迈从 MCHOSE 无线协议", lambda: strategy_mchose(iface)),
        ("Razer 雷蛇无线协议", lambda: strategy_razer(iface)),
        ("DARKRIM 私有协议", lambda: strategy_darkrim(iface)),
        ("厂商协议", lambda: strategy_vendor_protocol(iface)),
        ("罗技 HID++", lambda: strategy_logitech_hidpp(iface)),
    ]
    for label, fn in steps:
        if progress:
            progress(f"尝试：{label}")
        try:
            res = fn()
        except Exception as e:               # 单个策略异常不影响其它策略
            res = BatteryResult(ok=False, source=label, detail=f"异常：{e}")
        if res is not None:
            attempts.append(res)
            if res.ok:
                return attempts

    if listen_seconds > 0:
        if progress:
            progress(f"尝试：监听输入报告 {listen_seconds:.0f} 秒")
        res, _ = monitor_input_reports(iface, seconds=listen_seconds)
        if res is not None:
            attempts.append(res)
    return attempts


def best_result(attempts: list[BatteryResult]) -> Optional[BatteryResult]:
    for a in attempts:
        if a.ok:
            return a
    return attempts[0] if attempts else None


# --------------------------------------------------------------------------
# 多品牌外设完整型号数据库与自动发现引擎（供 my_devices.py / tray_app.py / desktop_widget.py 共用）
# --------------------------------------------------------------------------
KNOWN_BRAND_NAMES: dict[int, str] = {
    0x373B: "ATK 无线鼠标",
    0x3554: "ATK / VXE / VGN / Scyrox 无线鼠标",
    0x0B05: "ASUS ROG 外设",
    0x3151: "怒喵 AM Infinity Mouse 8K (无限鼠标)",
    0x373E: "Lamzu 兰族无线鼠标",
    0x36A7: "WLmouse 万灵无线鼠标",
    0x3710: "Pulsar 派世无线鼠标",
    0x2023: "MCHOSE 迈从无线鼠标",
    0x342D: "WLmouse 万灵 / 怒喵无线外设",
    0x5253: "MCHOSE 迈从无线鼠标",
    0x3837: "MCHOSE 迈从无线鼠标",
    0xA8A5: "MCHOSE 迈从 G7 无线鼠标",
    0x046D: "Logitech 罗技无线外设",
    0x1532: "Razer 雷蛇无线外设",
    0x24AE: "Rapoo 雷柏无线鼠标",
    0x3367: "Endgame Gear 无线鼠标",
    0x3057: "VAXEE 无线鼠标",
    0x1915: "Nordic 2.4G 无线外设",
    0x3654: "DARKRIM 2.4G 无线耳机",
}

# (VID, PID) -> 完整设备型号表（涵盖 ATK / VXE / VGN / ROG / 怒喵 / 雷蛇 / 罗技 / 迈从 / 兰族 / 万灵 等）
KNOWN_DEVICE_MODELS: dict[tuple[int, int], str] = {
    # --- ATK / VXE / VGN / Scyrox (0x373B & 0x3554) ---
    (0x373B, 0x1001): "ATK 烈空 F1 系列无线鼠标 (F1 Pro / Pro Max / Ultimate)",
    (0x373B, 0x1002): "ATK 烈空 X1 系列无线鼠标 (X1 Pro / Pro Max / Ultra)",
    (0x373B, 0x1003): "ATK 烈空 Z1 系列无线鼠标 (Z1 Pro / Ultra)",
    (0x373B, 0x1004): "ATK 烈空 F1 Extreme 无线鼠标",
    (0x373B, 0x1005): "ATK 烈空 X1 Ultimate 无线鼠标",
    (0x373B, 0x1006): "ATK 半壁江山 A87 / RS7 无线键盘",
    (0x3554, 0xF58A): "VXE 蜻蜓 R1 系列 (R1 / R1 SE+ / R1 Pro / Pro Max)",
    (0x3554, 0xF58B): "VGN 蜻蜓 F1 系列 (F1 Pro / Pro Max / MOBA)",
    (0x3554, 0xF58C): "VXE MAD R 系列 (MAD R / MAD R Major)",
    (0x3554, 0xF58D): "ATK 烈空 F1 / X1 (8K 接收器通道)",
    (0x3554, 0xF58E): "Scyrox V8 / V6 超轻无线鼠标",
    (0x3554, 0xF5F4): "ATK / VXE 8K Nano 无线接收器外设",
    (0x3554, 0xF5F7): "Pulsar X2 / X2V2 / Xlite V3 无线鼠标",

    # --- 怒喵 Angry Miao / Dry Studio ---
    (0x3151, 0x5007): "怒喵 AM Infinity Mouse 8K (无限鼠标)",
    (0x3151, 0x5006): "怒喵 AM Infinity Mouse (有线模式)",
    (0x595A, 0x0001): "怒喵 CYBERBOARD 无线客制化键盘",
    (0x342D, 0xE480): "Dry Studio Black Diamond 75 (黑钻75)",

    # --- Razer 雷蛇 (0x1532) ---
    (0x1532, 0x00C1): "Razer Viper V3 Pro (毒蝰 V3 专业版)",
    (0x1532, 0x00C0): "Razer Viper V3 Pro (毒蝰 V3 专业版 有线)",
    (0x1532, 0x00B6): "Razer Viper V3 HyperSpeed (毒蝰 V3 极速版)",
    (0x1532, 0x00A6): "Razer Viper V2 Pro (毒蝰 V2 专业版)",
    (0x1532, 0x00A5): "Razer Viper V2 Pro (毒蝰 V2 专业版 有线)",
    (0x1532, 0x007B): "Razer Viper Ultimate (毒蝰终极版)",
    (0x1532, 0x00B3): "Razer DeathAdder V3 Pro (炼狱蝰蛇 V3 专业版)",
    (0x1532, 0x00B2): "Razer DeathAdder V3 Pro (炼狱蝰蛇 V3 专业版 有线)",
    (0x1532, 0x00C3): "Razer DeathAdder V3 HyperSpeed (炼狱蝰蛇 V3 极速版)",
    (0x1532, 0x00AA): "Razer Basilisk V3 Pro (巴塞利斯蛇 V3 专业版)",
    (0x1532, 0x00AB): "Razer Basilisk V3 Pro (巴塞利斯蛇 V3 专业版)",
    (0x1532, 0x00B7): "Razer Cobra Pro (眼镜蛇专业版)",
    (0x1532, 0x00A8): "Razer Naga V2 Pro (那伽梵蛇 V2 专业版)",
    (0x1532, 0x0090): "Razer Naga Pro (那伽梵蛇专业版)",
    (0x1532, 0x009C): "Razer Orochi V2 (八岐大蛇 V2)",
    (0x1532, 0x0296): "Razer DeathStalker V2 Pro (噬魂金蝎 V2 专业版)",
    (0x1532, 0x0298): "Razer DeathStalker V2 Pro TKL (噬魂金蝎 V2 竞技版)",
    (0x1532, 0x025A): "Razer BlackWidow V3 Pro (黑寡妇蜘蛛 V3 无线版)",
    (0x1532, 0x02A6): "Razer BlackWidow V4 Pro 75% (黑寡妇蜘蛛 V4 无线版)",

    # --- Logitech 罗技常见接收器 (配合 HID++ 0x0005 动态固件型号读取) ---
    (0x046D, 0xC547): "Logitech LIGHTSPEED 无线外设 (GPW2 / G502X)",
    (0x046D, 0xC54D): "Logitech PRO X SUPERLIGHT 2 (GPW3 8K 接收器)",
    (0x046D, 0xC539): "Logitech LIGHTSPEED 无线鼠标 (GPW / G903 / G703)",
    (0x046D, 0xC53F): "Logitech G304 / G305 LIGHTSPEED 无线鼠标",
    (0x046D, 0xC548): "Logitech Logi Bolt 无线外设 (MX Master / MX Keys)",
    (0x046D, 0xC52B): "Logitech Unifying 优联无线外设",

    # --- MCHOSE 迈从 ---
    (0x5253, 0x0001): "MCHOSE 迈从 A5 系列无线鼠标 (A5 Pro / Pro Max / Ultra)",
    (0x3837, 0x0001): "MCHOSE 迈从 A7 / M7 系列无线鼠标",
    (0xA8A5, 0x0001): "MCHOSE 迈从 G7 无线鼠标",

    # --- Lamzu 兰族 & WLmouse 万灵 ---
    (0x373E, 0x0001): "Lamzu 兰族 Atlantis / Maya / Thorn 无线鼠标",
    (0x342D, 0x0001): "WLmouse 万灵 Beast X (兽X / 剑X) 镁合金无线鼠标",

    # --- DARKRIM ---
    (0x3654, 0xF507): "DARKRIM 2.4G 无线游戏耳机",
}

# 预设热门型号列表（供用户在界面一键选择或自定义修正共享接收器下的具体子型号）
PRESET_MODEL_OPTIONS: list[str] = [
    "自动识别（默认）",
    # ATK / VXE / VGN
    "ATK 烈空 F1 Ultimate 大师版",
    "ATK 烈空 F1 Pro Max",
    "ATK 烈空 F1 Extreme 探索版",
    "ATK 烈空 X1 Ultimate 大师版",
    "ATK 烈空 X1 Pro Max",
    "ATK 烈空 Z1 Ultra",
    "VXE 蜻蜓 R1 Pro Max",
    "VXE 蜻蜓 R1 SE+",
    "VXE MAD R Major",
    "VGN 蜻蜓 F1 MOBA",
    # ASUS ROG
    "ROG Harpe Ace Aim Lab Edition (龙鳞)",
    "ROG Harpe Ace Extreme (龙鳞 Extreme)",
    "ROG Harpe Ace Mini (龙鳞 Mini)",
    "ROG Keris II Ace (月刃2 Ace)",
    "ROG Keris Wireless AimPoint (月刃 AimPoint)",
    "ROG Gladius III Wireless AimPoint (战刃3 AimPoint)",
    "ROG Azoth 无线机械键盘 (夜魔)",
    "ROG Azoth Extreme 无线机械键盘 (夜魔 Extreme)",
    "ROG Falchion RX Low Profile (游侠RX矮轴)",
    "ROG Strix Scope II 96 Wireless (游侠2 96%无线)",
    # 怒喵 Angry Miao / Dry Studio
    "怒喵 AM Infinity Mouse 8K (无限鼠标)",
    "怒喵 CYBERBOARD R4 无线客制化键盘",
    "怒喵 CYBERBOARD Terminal / Novel",
    "怒喵 AM Compact Touch (触控无线键盘)",
    "怒喵 AM Hatsune (初音未来联名键盘)",
    "怒喵 AM AFA (Adjustable Flex Alice)",
    "Dry Studio Black Diamond 75 (黑钻75)",
    # 罗技 / 雷蛇 / 迈从
    "Logitech PRO X SUPERLIGHT 2 (GPW3)",
    "Logitech PRO X SUPERLIGHT (GPW2)",
    "Razer Viper V3 Pro (毒蝰 V3 专业版)",
    "Razer DeathAdder V3 Pro (炼狱蝰蛇 V3 专业版)",
    "MCHOSE 迈从 A5 Ultra",
    "MCHOSE 迈从 A7 Ultra",
]


@dataclass
class DiscoveredReading:
    key: str                      # 稳定唯一标识（用于托盘图标与卡片复用）
    title: str                    # 显示主标题（完整设备型号，如 "ROG Harpe Ace Aim Lab Edition (龙鳞)"）
    subtitle: str                 # 副标题（如 "配对型号 0B05:1A94 · 接收器 0B05:1ACE"）
    level: Optional[int] = None   # 0-100
    status: str = ""              # "放电中" / "充电中" / "已充满" / ""
    voltage_mv: Optional[int] = None
    detail: str = ""              # 命中的协议说明
    error: str = ""               # 错误提示
    extra: str = ""               # 附加信息（电压、固件版本等）
    is_headset: bool = False      # 是否使用耳机底板图标
    updated_at: Optional[float] = None  # Last successful shared acquisition.
    stale: bool = False          # Retained battery, not a new measurement.
    level_label: str = ""         # Discrete category, never a fabricated percentage.

    @property
    def ok(self) -> bool:
        return self.level is not None


def is_unidentified_logged_receiver(iface: HidInterface) -> bool:
    # Run 46d63078: inventory only; shared OEM ID cannot identify a keyboard.
    return ((iface.vid,iface.pid) == (0x25A7,0xFA70)
            and (iface.usage_page,iface.usage,iface.feature_len) == (0xFF02,2,8))


def _is_candidate_interface(iface: HidInterface, specs: list[ProtocolSpec]) -> bool:
    """判断某个 HID 接口是否属于受支持的无线外设通信通道。"""
    if iface.vid == 0 or (iface.input_len == 0 and iface.output_len == 0 and iface.feature_len == 0):
        return False
    if receivers.profile_for(iface):return receivers.matches(iface)
    if jbl.claims(iface):return jbl.candidate(iface)
    import xiaomi_mouse2 as xm
    if xm.claims(iface):return xm.matches(iface)
    if is_unidentified_logged_receiver(iface):
        return True
    if k1.claims(iface):
        return k1.matches(iface)
    if am_battery.supports_device(iface):
        return am_battery.matches_interface(iface)
    if peripheral.profile_for(iface):
        return peripheral.matches_interface(iface)
    if wireless.claims(iface):
        return wireless.candidate(iface)
    # 1. ASUS ROG
    if iface.vid == ASUS_VID and iface.usage_page in (0xFF00, 0xFF01) and iface.output_len > 0:
        return True
    # 2. 怒喵 Angry Miao
    if (iface.vid in ANGRY_MIAO_VIDS or is_angry_miao_iface(iface)) and iface.usage_page >= 0xFF00:
        return True
    # 3. DARKRIM 耳机
    if iface.vid == drp.VID and iface.usage_page == drp.USAGE_PAGE and iface.output_len == drp.REPORT_LEN:
        return True
    # 4. 迈从 MCHOSE
    if iface.vid in (0x5253, 0x3837) and iface.usage_page in (0xFF01, 0xFF0B) and iface.output_len > 0:
        return True
    if iface.vid == 0xA8A5 and iface.usage_page == 0xFF01 and iface.output_len > 0:
        return True
    # 5. Razer 雷蛇
    if iface.vid == 0x1532 and iface.feature_len >= 90:
        return True
    # 6. Logitech 罗技 HID++ (短报文 7 / 长报文 20)
    if iface.vid == 0x046D and iface.usage_page >= 0xFF00 and iface.output_len in (7, 20, 64):
        return True
    # 7. 匹配内置或用户协议表 (ATK / VXE / VGN / Lamzu / WLmouse / Rapoo / Endgame / VAXEE / Nordic)
    if any(s.vid == iface.vid and s.usage_page == iface.usage_page for s in specs):
        return True
    return False


_GENERIC_RECEIVER_STRINGS = {
    "2.4g wireless receiver", "usb receiver", "wireless receiver",
    "2.4g receiver", "gaming mouse", "wireless mouse", "wireless gaming mouse",
    "compx 2.4g dual mode mouse", "usb gaming mouse", "hid-compliant mouse",
    "rog omni receiver",
}


def resolve_full_model_name(iface: HidInterface,
                            res: Optional[BatteryResult] = None,
                            custom_models: Optional[dict[str, str]] = None,
                            key: str = "") -> str:
    """多级完整型号识别引擎：
    1) 用户在界面手动指定的自定义/预设完整型号（最高优先级）；
    2) 协议层从硬件寄存器/接收器动态读回的真实配对型号（如 ROG Omni 0xA0 0x00 配对 PID、罗技 HID++ 0x0005 固件设备名、DARKRIM TLV 0x05 设备名）；
    3) 内置 (VID, PID) 精确型号映射库 (`KNOWN_DEVICE_MODELS` / `ASUS_ROG_MODELS`)；
    4) USB 描述符 (`Manufacturer` + `Product`) 完整型号提取与品牌补全；
    5) 品牌默认型号名称。
    """
    # 1. 用户自定义型号
    if custom_models and key:
        user_name = (custom_models.get(key) or "").strip()
        if user_name and user_name != "自动识别（默认）":
            return user_name

    # 2. 硬件协议动态返回的完整型号（ROG Omni 真实配对型号 / 罗技 HID++ 0x0005 / DARKRIM TLV name）
    if res and res.extra:
        dyn_model = (res.extra.get("model_name") or res.extra.get("name") or "").strip()
        if dyn_model and dyn_model.lower() not in _GENERIC_RECEIVER_STRINGS:
            return dyn_model

    # 3. ASUS ROG 专用通道与 PID 型号库
    if iface.vid == ASUS_VID:
        return asus_channel_title(iface)

    if k1.claims(iface):
        return 'GravaStar K1 系列接收器（型号待确认）'
    if is_unidentified_logged_receiver(iface):
        return 'RK-G98（用户确认的共享接收器）'

    # 4. 精确 (VID, PID) 型号库匹配
    exact = KNOWN_DEVICE_MODELS.get((iface.vid, iface.pid))
    prod = (iface.product or "").strip()
    mfg = (iface.manufacturer or "").strip()

    # 如果 USB 描述符自带了更具体的型号（且不是通用 Receiver 字样），优先结合品牌展示
    if prod and prod.lower() not in _GENERIC_RECEIVER_STRINGS:
        if iface.vid == 0x373B and "atk" not in prod.lower():
            return f"ATK {prod}"
        if is_angry_miao_iface(iface) and not prod.startswith("怒喵"):
            return f"怒喵 {prod}"
        if mfg and mfg.lower() not in prod.lower() and mfg.lower() not in ("usb", "wch.cn", "telink", "compx", "nordic"):
            return f"{mfg} {prod}"
        return prod

    if exact:
        return exact

    if is_angry_miao_iface(iface):
        return prod or "怒喵 Angry Miao 无线外设"

    if iface.vid in KNOWN_BRAND_NAMES:
        return KNOWN_BRAND_NAMES[iface.vid]

    return prod or f"无线外设 ({iface.vid_pid})"


def _load_custom_models() -> dict[str, str]:
    """从 tray_config.json 读取用户自定义的设备完整型号映射。"""
    cfg_path = os.path.join(_app_base_dir(), "tray_config.json")
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cm = data.get("custom_models")
        return cm if isinstance(cm, dict) else {}
    except Exception:
        return {}


def discover_and_read_all(include_bt: bool = False, presence: Optional[dict] = None) -> list[DiscoveredReading]:
    """自动扫描本机所有已连接的多品牌 2.4G 外设（ATK、ROG、怒喵、罗技、雷蛇、迈从等），识别完整型号并读取电量。"""
    specs = all_protocols()
    all_ifaces = hw.enumerate_hid()
    if presence is not None:
        # Inventory is independent of battery replies (including sleeping mice
        # and standard HID devices which currently return no battery report).
        presence['hid_keys'] = {f'{d.vid:04X}:{d.pid:04X}:{d.usage_page:04X}' for d in all_ifaces}
        presence['hid_unknown'] = {f'{d.vid:04X}:{d.pid:04X}' for d in all_ifaces if not d.usage_page}
    peripheral.prune_listeners(all_ifaces)
    candidates = [d for d in all_ifaces if _is_candidate_interface(d, specs)]
    # Hyzen's vendor driver excludes its dongle while the cable is connected.
    if any((d.vid, d.pid) == (0x388D, 0x0024) for d in all_ifaces):
        candidates = [d for d in candidates if (d.vid, d.pid) != (0x388D, 0x0025)]
    custom_models = _load_custom_models()

    readings: list[DiscoveredReading] = []
    # 按 (vid, pid, usage_page) 去重，避免同一通道重复读取
    seen_channels: set[tuple[int, int, int]] = set()
    # 记录每个 (vid, pid) 下读取到的结果，方便对 ROG Omni Receiver 这类多槽位接收器过滤未配对空槽
    grouped: dict[tuple[int, int], list[ tuple[HidInterface, Optional[BatteryResult]] ]] = {}

    for iface in candidates:
        ch_key = ((iface.vid, iface.pid, iface.path) if peripheral.profile_for(iface)
                  else (iface.vid, iface.pid, iface.usage_page))
        if ch_key in seen_channels:
            continue
        seen_channels.add(ch_key)

        attempts = read_battery(iface, listen_seconds=0.0)
        res = best_result(attempts)
        grouped.setdefault((iface.vid, iface.pid), []).append((iface, res))

    for (vid, pid), items in grouped.items():
        # 优先剔除明确未配对外设的 Omni 空闲通道（如仅配对了鼠标时的 0xFF00 键盘空槽）
        non_empty = [
            it for it in items
            if not (it[1] is not None and it[1].extra.get("unpaired_omni_slot"))
        ]
        if presence is not None:
            presence.setdefault('excluded_keys', set()).update(
                f'{d.vid:04X}:{d.pid:04X}:{d.usage_page:04X}'
                for d, r in items if r is not None and r.extra.get('unpaired_omni_slot'))
        items = non_empty

        # 如果同一个接收器（如 ROG Omni 0B05:1ACE 或 ATK 多接口）有至少一个通道成功读出电量，
        # 则自动隐藏其余返回 empty_or_sleeping 或未响应的副通道，避免界面出现多余的空槽位报错卡片
        has_ok = any(r is not None and r.ok for _, r in items)
        if (vid, pid) in peripheral.CATALOG and has_ok:
            # One wireless target per newly supported dongle; multiple report
            # collections can deliver the same event.
            items = [next(it for it in items if it[1] is not None and it[1].ok)]
        for idx, (iface, res) in enumerate(items):
            if has_ok and (res is None or not res.ok):
                continue
            # 如果全部都没读出来，每个 (vid, pid) 只保留第一条代表性卡片提示用户唤醒
            if not has_ok and idx > 0:
                continue

            key = f"{iface.vid:04X}:{iface.pid:04X}:{iface.usage_page:04X}"
            title = resolve_full_model_name(iface, res, custom_models, key)

            # 构建带配对硬件 ID 的完整副标题
            paired_vp = res.extra.get("paired_vid_pid") if (res and res.extra) else None
            if paired_vp:
                sub = f"配对型号 {paired_vp} · 接收器 {iface.vid_pid} ({iface.product or 'ROG Omni Receiver'})"
            else:
                sub = f"硬件 ID {iface.vid_pid} · {iface.product or iface.manufacturer or '2.4G 无线接收器'}"
            if am_battery.supports_device(iface):
                connection = 'USB 有线' if iface.pid == 0x0880 else '2.4G 接收器'
                sub = f'硬件 ID {iface.vid_pid} · {connection}'

            is_headset = (iface.vid == drp.VID or "headset" in title.lower() or "耳机" in title)

            r_obj = DiscoveredReading(
                key=key, title=title, subtitle=sub, is_headset=is_headset
            )
            if res is None:
                r_obj.error = "没有找到可用的协议读取方式"
            elif res.ok:
                r_obj.level = res.level
                r_obj.level_label = res.extra.get('level_label', '')
                r_obj.status = res.status_text
                r_obj.voltage_mv = res.voltage_mv
                r_obj.detail = res.source
                bits = []
                if res.voltage_mv:
                    bits.append(f"电池电压 {res.voltage_mv} mV")
                if res.extra.get("device_fw"):
                    bits.append(f"设备固件 {res.extra['device_fw']}")
                if res.extra.get("dongle_fw"):
                    bits.append(f"接收器固件 {res.extra['dongle_fw']}")
                if res.extra.get("headset_fw"):
                    bits.append(f"耳机固件 {res.extra['headset_fw']}")
                if res.extra.get("sleeping"):
                    r_obj.status = "休眠中"
                r_obj.extra = " · ".join(bits)
            else:
                r_obj.error = res.detail or "外设无响应（可能处于休眠或未开机）"
            readings.append(r_obj)

    # 可选：补充具有标准 HID 电量字段的其它设备（如怒喵键盘有线/2.4G 标准电池页）
    for iface in all_ifaces:
        if am_battery.supports_device(iface) or k1.claims(iface):
            continue
        if (iface.vid, iface.pid) in grouped or not iface.feature_len:
            continue
        res = strategy_standard_battery_usage(iface)
        if res and res.ok:
            grouped[(iface.vid, iface.pid)] = [(iface, res)]
            key = f"{iface.vid:04X}:{iface.pid:04X}:{iface.usage_page:04X}"
            title = resolve_full_model_name(iface, res, custom_models, key)
            readings.append(DiscoveredReading(
                key=key,
                title=title,
                subtitle=f"硬件 ID {iface.vid_pid} · {iface.product or 'HID 外设'}",
                level=res.level,
                status=res.status_text,
                detail=res.source,
            ))

    # 可选：合并当前已连接的蓝牙无线外设电量
    if include_bt:
        try:
            import bt_battery as bb
            for bdev in bb.connected_devices(presence=presence):
                key = f"BT:{bdev.instance_id}"
                bt_title = custom_models.get(key) or custom_models.get(f"BT:{bdev.source_instance_id}") or bdev.name
                speaker = any(word in bdev.name.casefold() for word in ('tribit', 'speaker', '音箱', '音响'))
                readings.append(DiscoveredReading(
                    key=key,
                    title=bt_title,
                    subtitle="蓝牙音箱" if speaker else "蓝牙耳机" if bdev.headset else "蓝牙无线连接",
                    level=bdev.battery,
                    status="系统缓存" if bdev.cached else "已连接",
                    detail=bdev.reason,
                    error=bdev.reason if bdev.battery is None else '',
                    is_headset=bdev.headset and not speaker,
                ))
        except Exception as exc:
            if presence is not None:
                presence['bt_error'] = f'蓝牙连接状态读取失败：{exc}'

    import xinput_battery
    readings.extend(xinput_battery.readings(custom_models, presence))
    return readings


