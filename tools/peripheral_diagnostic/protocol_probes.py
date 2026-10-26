# -*- coding: utf-8 -*-
"""
protocol_probes.py —— 多品牌 2.4G 无线鼠标/键盘只读配置探测与解码器

严格遵循只读（Query-Only）安全策略：
  * 仅发送经核实的查询指令，绝不发送修改 DPI、回报率、灯光、配对、配置层或刷固件的命令。
  * 记录每一条 tx（发送十六进制帧）、rx（接收十六进制帧）与 decoded（结构化解析结果）。
  * 覆盖四大适配维度：电量（Battery）、DPI/API 档位、回报率（Polling Rate）、灯光（RGB Lighting）。
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import time
from typing import Callable, Optional

import hid_win as hw
if not getattr(sys, 'frozen', False):
    sys.path.append(str(Path(__file__).resolve().parents[1] / 'am_diagnostic'))
import am_diagnostic as audited_am
import am_controls_probe
import atk_r1_nk as r1nk
import xiaomi_probe
import weisheng_keyboard as wk
import vxe_r1_pro_max as vxe
import peripheral_protocols as battery_catalog
import wireless_battery as wireless_catalog
from hid_win import HidError, HidInterface, HidP_Feature, HidP_Input, HidP_Output


class HidDevice(hw.HidDevice):
    """兼容包装层：支持传入 HidInterface、安全非抛错读写与完整能力枚举。"""

    def __init__(self, path_or_iface: "str | HidInterface", write: bool = True):
        self.trace = lambda *a, **k: None
        path = path_or_iface.path if isinstance(path_or_iface, HidInterface) else str(path_or_iface)
        self.open_error = ""
        self.handle = None
        self._preparsed = ctypes.c_void_p()
        self._caps = None
        self._read_handle = None
        self._read_event = None
        try:
            super().__init__(path, write=write)
        except Exception as exc:
            self.path = path
            self.write_access = False
            self.open_error = str(exc)

    def open(self) -> bool:
        return bool(self.handle)

    @property
    def access_mode(self) -> str:
        if not self.handle:
            return "closed"
        return "rw" if self.write_access else "ro_zero_access"

    def all_value_caps(self) -> list[dict]:
        out: list[dict] = []
        for rtype, rname in ((HidP_Input, "input"), (HidP_Output, "output"), (HidP_Feature, "feature")):
            for c in self.value_caps(rtype):
                usage = int(c.u.Range.UsageMin if c.IsRange else c.u.NotRange.Usage)
                out.append({
                    "report_type": rname,
                    "report_id": int(c.ReportID),
                    "usage_page": int(c.UsagePage),
                    "usage": usage,
                    "bit_size": int(c.BitSize),
                    "report_count": int(c.ReportCount),
                })
        return out

    def get_feature(self, report_id: int, length: Optional[int] = None) -> Optional[bytes]:
        try:
            raw = self.get_feature_report(report_id, length)
            self.trace('transport_rx', method='get_feature', report_id=report_id, hex=raw.hex())
            return raw
        except Exception as exc:
            self.trace('transport_error', method='get_feature', report_id=report_id, error=str(exc))
            return None

    def get_feature_report(self,report_id,length=None):
        raw=super().get_feature_report(report_id,length)
        self.trace('transport_rx',method='get_feature',report_id=report_id,hex=raw.hex())
        return raw

    def set_feature_report(self,data):
        self.trace('transport_tx',method='set_feature_query',hex=data.hex())
        return super().set_feature_report(data)

    def set_feature(self, data: bytes) -> bool:
        try:
            self.trace('transport_tx', method='set_feature_query', hex=data.hex())
            self.set_feature_report(data)
            return True
        except Exception as exc:
            self.trace('transport_error', method='set_feature_query', error=str(exc))
            return False

    def write_output(self, data: bytes, use_hidd: bool = False) -> bool:
        try:
            self.trace('transport_tx', method='output_query', hex=data.hex(), use_hidd=use_hidd)
            super().write_output(data, use_hidd=use_hidd)
            return True
        except Exception:
            if not use_hidd:
                try:
                    super().write_output(data, use_hidd=True)
                    return True
                except Exception as exc:
                    self.trace('transport_error', method='output_query', error=str(exc))
            return False

    def read_input(self, *args, **kwargs):
        raw = super().read_input(*args, **kwargs)
        if raw:
            self.trace('transport_rx', method='input', hex=raw.hex())
        return raw

EmitFn = Callable[..., None]

# 已知品牌 VID 与型号标识字典
BRAND_VIDS: dict[int, str] = {
    0x0B05: "ASUS ROG (玩家国度)",
    0x373B: "ATK / VXE (烈空/蜻蜓)",
    0x3554: "ATK / VXE / VGN / Scyrox / Lamzu / Pulsar (Compx方案)",
    0x3710: "Pulsar (派世)",
    0x0E8D: "Angry Miao (怒喵 AM35 / .97 / .100R2)",
    0x35A1: "Angry Miao (怒喵 AM35 兼容)",
    0x3151: "Angry Miao / Akko / MonsGeek (YICHIP/RongYuan方案)",
    0x595A: "Angry Miao (怒喵 CYBERBOARD)",
    0x046D: "Logitech (罗技 HID++)",
    0x1532: "Razer (雷蛇)",
    0x3837: "MCHOSE (迈从 M HUB V2/V3)",
    0x5253: "MCHOSE (迈从 A5/A7)",
    0x2023: "MCHOSE (迈从 A5 Pro Max)",
    0xA8A5: "AJAZZ / MCHOSE G7 (黑爵/迈从)",
    0x373E: "Lamzu / Attack Shark / Vancer (兰族/黑鲨 Page方案)",
    0x37B0: "Lamzu (兰族 Inca/Paro)",
    0x36A7: "WLmouse (万灵竞记 Beast X)",
    0x342D: "WLmouse / Dry Studio (万灵/怒喵)",
    0x24AE: "Rapoo (雷柏)",
    0x260D: "DAREU (达尔优)",
    0x0C45: "AULA / AJAZZ (狼蛛/黑爵 2.4G键盘)",
    0x372E: "RK Royal Kludge",
    0x388D: "Lofree (洛斐)",
    0x320F: "Telink / OEM（含 HECATE、ATK、VGN，需核对型号）",
    0x093A: "PixArt / OEM（含触控板，品牌待确认）",
    0x2717: "Xiaomi（小米）",
    0x1D57: "DELUX (多彩)",
    0x3057: "VAXEE (卓威创始团队)",
    0x3367: "Endgame Gear",
    0x3654: "DARKRIM (2.4G无线耳机)",
    0x1915: "Nordic Semiconductor (公版 2.4G)",
}


def report_ids_for(dev: HidDevice, report_type: int) -> list[int]:
    """同时从 ValueCaps 与 ButtonCaps 提取接口声明的所有 Report ID。"""
    ids = {int(cap.ReportID) for cap in dev.value_caps(report_type)}
    if ctypes.sizeof(hw.HIDP_BUTTON_CAPS) == 72:
        count = ctypes.c_ushort(0)
        hw.hid.HidP_GetButtonCaps(report_type, None, ctypes.byref(count), dev._preparsed)
        if count.value:
            caps = (hw.HIDP_BUTTON_CAPS * count.value)()
            st = hw.hid.HidP_GetButtonCaps(
                report_type, ctypes.byref(caps), ctypes.byref(count), dev._preparsed
            )
            if st == hw.HIDP_STATUS_SUCCESS:
                ids.update(int(cap.ReportID) for cap in caps[: count.value])
    return sorted(ids)


# ==============================================================================
# 1. ASUS ROG (0x0B05) —— 电量 / DPI / 回报率 / 灯光 / 配对型号
# ==============================================================================
ASUS_POLLING_RATES = (125, 250, 500, 1000, 2000, 4000, 8000)
ASUS_LIGHT_MODES = {
    0: "常亮 (Static)",
    1: "呼吸 (Breathing)",
    2: "彩色循环 (Color Cycle)",
    4: "按键响应 (Reactive)",
    6: "电量模式 (Battery)",
    0xF0: "关闭 (Off)",
}


def _asus_tx_rx(dev: HidDevice, iface: HidInterface, emit: EmitFn, label: str,
                rid: int, cmd: tuple[int, ...], timeout: float = 0.35) -> Optional[bytes]:
    out_len = dev.caps.OutputReportByteLength or iface.output_len or 64
    pkt = bytes(([rid, *cmd] + [0] * out_len)[:out_len])
    dev.drain()
    emit("tx", brand="asus_rog", label=label, method="output", hex=pkt.hex())
    dev.write_output(pkt, use_hidd=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = dev.read_input(timeout_ms=100)
        if not r:
            continue
        emit("rx", brand="asus_rog", label=label, method="input", hex=r.hex())
        if len(r) >= 4 and (
            (r[0] == rid and r[1:1+len(cmd)] == bytes(cmd))
            or r[:len(cmd)] == bytes(cmd)
            or r[1:3] == b"\xff\xaa"
        ):
            return r
    return None


def probe_asus_rog(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "ASUS ROG"}
    if not iface.output_len:
        return summary

    # 1) Omni 接收器管理通道 0xFF02: 查询槽位配对的真实子设备 PID
    if iface.usage_page == 0xFF02 and iface.pid in (0x1ACE, 0x1B0A):
        r = _asus_tx_rx(dev, iface, emit, "omni_paired_slots", 0x01, (0xA0, 0x00))
        if r and len(r) >= 9 and r[1] == 0xA0:
            slots = []
            count = int.from_bytes(r[3:5], "little")
            for i in range(min(count, 6)):
                off = 5 + i * 4
                if len(r) >= off + 4:
                    sub_pid = int.from_bytes(r[off : off + 2], "little")
                    slot_rid = r[off + 2]
                    slots.append({"slot_rid": slot_rid, "paired_pid": f"{sub_pid:04X}", "type": r[off + 3]})
            summary["omni_slots"] = slots
            emit("decoded", brand="asus_rog", label="omni_slots", value=slots)
        return summary

    if iface.usage_page not in (0xFF00, 0xFF01):
        return summary

    cand_rids = (0x02, 0x00, 0x01, 0x03) if iface.usage_page == 0xFF00 else (0x03, 0x00, 0x01, 0x02)
    active_rid = None
    # 2) 电量与休眠 (12 07)
    for rid in cand_rids:
        r = _asus_tx_rx(dev, iface, emit, f"battery_rid_{rid:02x}", rid, (0x12, 0x07))
        if not r:
            continue
        if r[1:3] == b"\xff\xaa":
            emit("decoded", brand="asus_rog", label="config_error", value="FF AA (外设休眠或未连接)")
            continue
        off = 1 if (r[0] == rid and r[1] == 0x12) else (0 if r[0] == 0x12 else -1)
        if off >= 0 and len(r) >= off + 10:
            if r[off + 4] > 100 or (iface.pid in (0x1ACE, 0x1B0A) and r[off + 5] != 2):
                emit('unconfirmed', brand='asus_rog', label='battery', reason='Slot offline or invalid battery value')
                continue
            active_rid = rid
            mv = r[off + 7] | (r[off + 8] << 8)
            bat = {
                "rid": rid,
                "level_percent": r[off + 4],
                "sleep_code": r[off + 5],
                "voltage_mv": mv if 2000 <= mv <= 5000 else None,
                "charging": bool(r[off + 9]),
            }
            summary["battery"] = bat
            emit("decoded", brand="asus_rog", label="battery", value=bat)
            break

    if active_rid is None:
        return summary

    # 3) 固件版本、当前配置层与当前 DPI 档位 (12 00 00)
    r_prof = _asus_tx_rx(dev, iface, emit, "profile_and_firmware", active_rid, (0x12, 0x00, 0x00))
    if r_prof and r_prof[1:3] != b"\xff\xaa" and len(r_prof) >= 13:
        off = 1 if r_prof[0] == active_rid else 0
        prof_info = {
            "dongle_fw": f"v{r_prof[off+4]}.{r_prof[off+5]:02d}.{r_prof[off+6]:02d}",
            "device_fw": f"v{r_prof[off+8]}.{r_prof[off+9]:02d}.{r_prof[off+10]:02d}",
            "active_profile": r_prof[off + 10] + 1,
            "active_dpi_stage": r_prof[off + 11],
        }
        summary["profile"] = prof_info
        emit("decoded", brand="asus_rog", label="profile", value=prof_info)

    # 4) 回报率、按键消抖、直线修正与基础 DPI (12 04 00)
    r_set = _asus_tx_rx(dev, iface, emit, "polling_and_settings", active_rid, (0x12, 0x04, 0x00))
    if r_set and r_set[1:3] != b"\xff\xaa" and len(r_set) >= 18:
        off = 1 if r_set[0] == active_rid else 0
        rate_byte = r_set[off + 12]
        rate_idx = (rate_byte >> 4) if (rate_byte >> 4) else (rate_byte & 0x07)
        hz = ASUS_POLLING_RATES[rate_idx] if rate_idx < len(ASUS_POLLING_RATES) else None
        deb_raw = r_set[off + 14]
        settings_info = {
            "polling_rate_hz": hz,
            "polling_raw_byte": f"0x{rate_byte:02X}",
            "debounce_ms": deb_raw * 4 + 4 if 2 <= deb_raw <= 7 else deb_raw,
            "angle_snapping": bool(r_set[off + 16] == 1),
        }
        summary["polling_rate"] = settings_info
        emit("decoded", brand="asus_rog", label="polling_rate", value=settings_info)

    # 5) 4 档独立 X/Y DPI (12 04 02) 与 4 档颜色 (12 04 03)
    r_dpi = _asus_tx_rx(dev, iface, emit, "dpi_xy_stages", active_rid, (0x12, 0x04, 0x02))
    r_col = _asus_tx_rx(dev, iface, emit, "dpi_stage_colors", active_rid, (0x12, 0x04, 0x03))
    if r_dpi and r_dpi[1:3] != b"\xff\xaa" and len(r_dpi) >= 21:
        off = 1 if r_dpi[0] == active_rid else 0
        c_off = (1 if r_col and r_col[0] == active_rid else 0) if r_col else 0
        stages = []
        for i in range(4):
            x = int.from_bytes(r_dpi[off + 4 + i * 4 : off + 6 + i * 4], "little") * 50 + 50
            y = int.from_bytes(r_dpi[off + 6 + i * 4 : off + 8 + i * 4], "little") * 50 + 50
            color = None
            if r_col and len(r_col) >= c_off + 4 + (i + 1) * 3:
                color = "#" + r_col[c_off + 4 + i * 3 : c_off + 4 + (i + 1) * 3].hex()
            stages.append({"stage": i + 1, "x": x, "y": y, "color": color})
        summary["dpi"] = {"stages": stages}
        emit("decoded", brand="asus_rog", label="dpi", value=summary["dpi"])

    # 6) LOD 静默高度 (12 06)
    r_lod = _asus_tx_rx(dev, iface, emit, "lod_distance", active_rid, (0x12, 0x06))
    if r_lod and r_lod[1:3] != b"\xff\xaa" and len(r_lod) >= 9:
        off = 1 if r_lod[0] == active_rid else 0
        lod_val = "High" if r_lod[off + 7] == 1 else ("Low" if r_lod[off + 7] == 0 else f"0x{r_lod[off+7]:02X}")
        summary["lod"] = lod_val
        emit("decoded", brand="asus_rog", label="lod", value=lod_val)

    # 7) RGB 灯光状态 (12 03 00)
    r_rgb = _asus_tx_rx(dev, iface, emit, "rgb_lighting", active_rid, (0x12, 0x03, 0x00))
    if r_rgb and r_rgb[1:3] != b"\xff\xaa" and len(r_rgb) >= 10:
        off = 1 if r_rgb[0] == active_rid else 0
        # One queried zone, not three structures in the padding. Physical zone
        # names require the paired model; Harpe index 0 is its scrollwheel.
        base = off + 4
        m_code = r_rgb[base]
        zones = [{
            "zone_query_index": 0,
            "mode_code": m_code,
            "mode_name": ASUS_LIGHT_MODES.get(m_code, f"Mode_{m_code}"),
            "brightness": r_rgb[base + 1],
            "color": "#" + r_rgb[base + 2 : base + 5].hex(),
        }]
        summary["lighting"] = zones
        emit("decoded", brand="asus_rog", label="lighting", value=zones)

    return summary


# ==============================================================================
# 2. ATK / VXE / VGN / Scyrox / Pulsar / 达尔优 (Compx 0x08 协议)
# ==============================================================================
COMPX_POLLING_MAP = {
    0x08: 125,
    0x04: 250,
    0x02: 500,
    0x01: 1000,
    0x10: 2000,   # 部分 Lamzu 接收器为 1000Hz，同时记录原始编码
    0x20: 4000,
    0x40: 8000,
    0x80: 8000,
}


def _atk_frame(cmd: int, payload: tuple[int, ...] = (), out_len: int = 17) -> bytes:
    body = bytes([cmd, *payload])[:15]
    body = body + bytes(15 - len(body))
    chk = (0x55 - 8 - sum(body)) & 0xFF
    pkt17 = bytes([0x08]) + body + bytes([chk])
    return pkt17.ljust(out_len, b"\x00")


def _atk_exchange(dev: HidDevice, iface: HidInterface, emit: EmitFn, label: str,
                  cmd: int, payload: tuple[int, ...] = (), accept=lambda b: True) -> Optional[bytes]:
    out_len = dev.caps.OutputReportByteLength or iface.output_len or 17
    pkt = _atk_frame(cmd, payload, out_len)
    for attempt in range(2):
        dev.drain()
        emit("tx", brand="atk_compx", label=label, attempt=attempt + 1, method="output", hex=pkt.hex())
        dev.write_output(pkt, use_hidd=False)
        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            r = dev.read_input(timeout_ms=100)
            if not r:
                continue
            emit("rx", brand="atk_compx", label=label, method="input", hex=r.hex())
            if len(r) >= 17 and r[0] == 0x08 and (sum(r[:17]) & 0xFF) == 0x55 and accept(r[1:17]):
                return r[1:17]
    return None


def _atk_decode_dpi_both(data: bytes) -> dict:
    """同时计算 Ultra (10-42000) 与 Step50 (50-36000) 解码结果供日志比对。"""
    if len(data) < 4:
        return {"raw": data.hex(), "valid": False}
    valid = (sum(data[:4]) & 0xFF) == 0x55

    def axis_ultra(val: int, mode: int) -> int:
        cnt = ((mode >> 2) & 3) * 256 + val
        base = (10050 + cnt * 50) if (mode & 2) else (cnt + 1) * 10
        return base * (2 if (mode & 1) else 1)

    def axis_step50(val: int, mode: int) -> int:
        cnt = ((mode >> 2) & 3) * 256 + val
        return (cnt + 1) * 50 * (2 if (mode & 1) else 1)

    m_lo, m_hi = data[2] & 0x0F, (data[2] >> 4) & 0x0F
    return {
        "raw": data[:4].hex(),
        "checksum_ok": valid,
        "dpi_ultra": [axis_ultra(data[0], m_lo), axis_ultra(data[1], m_hi)],
        "dpi_step50": [axis_step50(data[0], m_lo), axis_step50(data[1], m_hi)],
    }


def probe_atk_compx(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "ATK / VXE / Compx"}
    # 分支 A: Nordic 54L15 0xFF05 64B 电量协议
    if iface.usage_page == 0xFF05 and iface.output_len >= 64:
        pkt = bytes([0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01]).ljust(iface.output_len, b"\x00")
        dev.drain()
        emit("tx", brand="atk_54l15", label="battery_0x72", method="output", hex=pkt.hex())
        dev.write_output(pkt, use_hidd=False)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            r = dev.read_input(timeout_ms=100)
            if r:
                emit("rx", brand="atk_54l15", label="battery_0x72", method="input", hex=r.hex())
                if len(r) >= 8 and r[1] == 0x72 and r[7] <= 100:
                    summary["battery"] = {"level_percent": r[7], "protocol": "Nordic 54L15 0x72"}
                    emit("decoded", brand="atk_54l15", label="battery", value=summary["battery"])
                    break
        return summary

    if (iface.usage_page, iface.usage) != (0xFF02, 0x0002) or iface.output_len not in (17, 64):
        return summary

    # 1) 电量与电压 (Cmd 0x04)
    r_bat = _atk_exchange(dev, iface, emit, "battery_cmd04", 0x04, (), lambda b: b[0] == 0x04)
    if r_bat and r_bat[1] == 0 and 2 <= r_bat[4] <= 10 and r_bat[5] <= 100 and r_bat[6] in (0, 1):
        mv = int.from_bytes(r_bat[7:9], "big") if r_bat[4] >= 4 else 0
        summary["battery"] = {
            "level_percent": r_bat[5],
            "charging": bool(r_bat[6]),
            "voltage_mv": mv if 2500 <= mv <= 5000 else None,
        }
        emit("decoded", brand="atk_compx", label="battery", value=summary["battery"])

    # 2) 硬件身份 CID/MID (Cmd 0x10) 与版本 (Cmd 0x12 / 0x1D) 与当前配置层 (Cmd 0x0E)
    r_id = _atk_exchange(dev, iface, emit, "identity_cmd10", 0x10, (), lambda b: b[0] == 0x10)
    known_r1 = known_pro_max = False
    if r_id and r_id[1] == 0 and 2 <= r_id[4] <= 10:
        summary["identity"] = {
            "data_length": r_id[4],
            "cid": r_id[5],
            "mid": r_id[6],
            "raw_payload": r_id[5:5+r_id[4]].hex(),
        }
        known_r1 = r1nk.receiver(iface) and tuple(r_id[5:7]) == r1nk.IDENTITY
        if known_r1:
            summary['identity'].update(model=r1nk.MODEL, sensor='PAW3395SE')
        known_pro_max=vxe.matches(iface) and tuple(r_id[5:7])==vxe.IDENTITY
        if known_pro_max:
            summary['identity'].update(model=vxe.MODEL,sensor='PAW3395')
        emit("decoded", brand="atk_compx", label="identity", value=summary["identity"])

    r_prof = _atk_exchange(dev, iface, emit, "active_profile_cmd0e", 0x0E, (), lambda b: b[0] == 0x0E)
    if r_prof and r_prof[1] == 0:
        summary["active_profile"] = r_prof[5]
        emit("decoded", brand="atk_compx", label="active_profile", value=r_prof[5])

    # 3) 只读 EEPROM 寄存器快照 (Cmd 0x08)
    def read_eeprom(label: str, addr: int, count: int) -> Optional[bytes]:
        r = _atk_exchange(
            dev, iface, emit, label, 0x08, (0, addr >> 8, addr & 0xFF, count),
            lambda b: b[0] in (7, 8) and b[1] == 0 and b[2:4] == addr.to_bytes(2, "big") and b[4] >= count,
        )
        return r[5 : 5 + count] if r else None

    # 0x0000: 回报率 (0..1)、DPI 档位数 (2..3)、当前档位 (4..5)、防误触 (6..7)、LOD (10..11)
    sys_block = read_eeprom("eeprom_system_0x0000", 0x0000, 10)
    lod_block = read_eeprom("eeprom_lod_0x000A", 0x000A, 2)
    if sys_block and len(sys_block) >= 6:
        rate_code = sys_block[0]
        stage_count = sys_block[2]
        active_stage = sys_block[4]
        polling_info = {
            "rate_code_hex": f"0x{rate_code:02X}",
            "rate_checksum_ok": ((sys_block[0] + sys_block[1]) & 0xFF) == 0x55,
            "polling_rate_hz": COMPX_POLLING_MAP.get(rate_code),
            "dpi_stage_count": stage_count,
            "active_dpi_stage": active_stage + 1,
            "anti_mistouch_ms": sys_block[6] * 10 if len(sys_block) >= 8 and ((sys_block[6] + sys_block[7]) & 0xFF) == 0x55 else None,
            "lod_raw": lod_block.hex() if lod_block else None,
        }
        summary["polling_rate"] = polling_info
        emit("decoded", brand="atk_compx", label="polling_and_system", value=polling_info)

        # 读取最多 8 档 DPI (0x000C 起，每档 4B) 与 8 档 RGB 颜色 (0x002C 起，每档 4B)
        stages = []
        for idx in range(min(max(stage_count, 4), 8)):
            raw_stage = read_eeprom(f"eeprom_dpi_stage_{idx+1}", 0x000C + idx * 4, 4)
            raw_color = read_eeprom(f"eeprom_dpi_color_{idx+1}", 0x002C + idx * 4, 4)
            if raw_stage:
                item = _atk_decode_dpi_both(raw_stage)
                if known_pro_max and item.get('checksum_ok'):
                    item['dpi']=item.pop('dpi_step50')
                    item['codec']='step50'
                    item.pop('dpi_ultra',None)
                if known_r1 and item['checksum_ok']:
                    try:
                        item['dpi'] = [r1nk.decode_axis(raw_stage[0],raw_stage[2]&15),
                                       r1nk.decode_axis(raw_stage[1],raw_stage[2]>>4)]
                        item['codec'] = 'paw3395se'
                        item.pop('dpi_ultra',None)
                        item.pop('dpi_step50',None)
                    except ValueError as exc:
                        item['decode_error'] = str(exc)
                item["stage"] = idx + 1
                if raw_color and len(raw_color) >= 3:
                    item["color_rgb"] = "#" + raw_color[:3].hex()
                    item["color_raw"] = raw_color.hex()
                stages.append(item)
        summary["dpi"] = {"stages": stages}
        emit("decoded", brand="atk_compx", label="dpi_stages", value=stages)

    # 4) 读取扩展寄存器：0x0070 (VXE R1 实时设置行)、0x00A8..0x00BA (消抖/MotionSync/休眠/直线修正/波纹/性能/灯光)
    ext_registers = {}
    for label, addr, length in (
        ("vxe_live_0x0070", 0x0070, 8),
        ("Light_or_aux_0x004C", 0x004C, 10),
        ("debounce_motionsync_sleep_0x00A8", 0x00A8, 10),
        ("ripple_perf_sensor_0x00B2", 0x00B2, 10),
    ):
        blk = read_eeprom(label, addr, length)
        if blk:
            ext_registers[label] = blk.hex()
    if ext_registers:
        summary["extended_registers"] = ext_registers
        emit("decoded", brand="atk_compx", label="extended_registers", value=ext_registers)
        if known_pro_max and 'Light_or_aux_0x004C' in ext_registers:
            try:
                summary['lighting']=vxe.lighting(bytes.fromhex(ext_registers['Light_or_aux_0x004C'])[:8])
                emit('decoded',brand='atk_compx',label='dpi_indicator_lighting',value=summary['lighting'])
            except ValueError as exc:
                emit('decode_error',brand='atk_compx',label='dpi_indicator_lighting',message=str(exc))

    return summary


# ============================================================================
# 3. Angry Miao (怒喵 AM35 RACE / AM30 / Infinity Mouse / Cyberboard)
# ============================================================================
AM35_POLLING_MAP = {0: 125, 1: 250, 2: 500, 3: 1000, 4: 2000, 5: 4000, 6: 8000}


def _am35_pkt(report_id: int, cmd: int, payload: bytes = b"", total_len: int = 64) -> bytes:
    body = bytes([report_id, len(payload) + 4, 0x05, 0x5A, cmd & 0xFF, (cmd >> 8) & 0xFF]) + payload
    return body.ljust(total_len, b"\x00")[:total_len]


def _am35_parse(raw: Optional[bytes]) -> Optional[tuple[int, int, bytes]]:
    if not raw or len(raw) < 7 or raw[2] != 0x05 or raw[3] != 0x5A:
        return None
    cmd = raw[4] | (raw[5] << 8)
    status = raw[6]
    pay_len = max(0, min(len(raw) - 7, raw[1] - 5))
    return cmd, status, raw[7 : 7 + pay_len]


def _am35_query(dev: HidDevice, iface: HidInterface, emit: EmitFn, label: str,
                cmd: int, payload: bytes = b"") -> Optional[bytes]:
    feat_len = dev.caps.FeatureReportByteLength or iface.feature_len
    out_len = dev.caps.OutputReportByteLength or iface.output_len
    if feat_len >= 60:
        pkt = _am35_pkt(0x14, cmd, payload, feat_len)
        emit("tx", brand="angry_miao_am35", label=label, method="feature_0x14", hex=pkt.hex())
        if dev.set_feature(pkt):
            deadline = time.monotonic() + 0.35
            while time.monotonic() < deadline:
                time.sleep(0.025)
                r = dev.get_feature(0x14, feat_len)
                parsed = _am35_parse(r)
                if parsed and parsed[0] == cmd:
                    emit("rx", brand="angry_miao_am35", label=label, method="feature_0x14", hex=r.hex())
                    return parsed[2] if parsed[1] == 0 else None
    if out_len >= 60:
        pkt = _am35_pkt(0x06, cmd, payload, out_len)
        dev.drain()
        emit("tx", brand="angry_miao_am35", label=label, method="output_0x06", hex=pkt.hex())
        dev.write_output(pkt, use_hidd=False)
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            r = dev.read_input(timeout_ms=80)
            if not r:
                continue
            emit("rx", brand="angry_miao_am35", label=label, method="input", hex=r.hex())
            parsed = _am35_parse(r)
            if parsed and parsed[0] == cmd:
                return parsed[2] if parsed[1] == 0 else None
    return None


def _am30_crc16_xmodem(buf: bytes) -> int:
    crc = 0
    for b in buf:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def _am30_query(dev: HidDevice, emit: EmitFn, label: str, cmd: int) -> Optional[bytes]:
    body = bytearray(64)
    body[0] = 0x4D
    body[1] = cmd & 0xFF
    crc = _am30_crc16_xmodem(bytes(body[:62]))
    body[62] = (crc >> 8) & 0xFF
    body[63] = crc & 0xFF
    pkt = bytes([0x00]) + bytes(body)
    emit("tx", brand="angry_miao_am30", label=label, method="feature_0x00", hex=pkt.hex())
    if not dev.set_feature(pkt):
        return None
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        time.sleep(0.025)
        r = dev.get_feature(0x00, 65)
        if r and len(r) >= 65 and r[1] == 0x4D and r[2] == (cmd & 0xFF):
            emit("rx", brand="angry_miao_am30", label=label, method="feature_0x00", hex=r.hex())
            return r[1:65]
    return None


def probe_angry_miao(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "Angry Miao (怒喵)"}
    feat_ids = set(report_ids_for(dev, 2))
    out_ids = set(report_ids_for(dev, 1))

    # 分支 A: AM35 RACE (Feature 0x14 或 Output 0x06)
    if 0x14 in feat_ids or 0x06 in out_ids or (iface.vid, iface.pid) in ((0x3710, 0x5408), (0x3710, 0x3411)):
        prof_b = _am35_query(dev, iface, emit, "profile_0x30C9", 0x30C9)
        profile = prof_b[0] if prof_b else 0
        summary["active_profile"] = profile

        # 电量 (0x30CF / 0x300F)
        bat_b = _am35_query(dev, iface, emit, "battery_0x30CF", 0x30CF) or _am35_query(dev, iface, emit, "battery_dongle_0x300F", 0x300F)
        if bat_b and len(bat_b) >= 1 and bat_b[0] <= 100:
            summary["battery"] = {
                "level_percent": bat_b[0],
                "charging": bool(bat_b[1] == 1) if len(bat_b) >= 2 else False,
            }
            emit("decoded", brand="angry_miao_am35", label="battery", value=summary["battery"])

        # 回报率 (0x3011 getReportRate)
        rate_b = _am35_query(dev, iface, emit, "report_rate_0x3011", 0x3011, bytes([profile]))
        if rate_b and len(rate_b) >= 2:
            code = rate_b[1]
            summary["polling_rate"] = {
                "profile": rate_b[0],
                "rate_code": code,
                "polling_rate_hz": AM35_POLLING_MAP.get(code, code),
            }
            emit("decoded", brand="angry_miao_am35", label="polling_rate", value=summary["polling_rate"])

        # DPI 档位与 RGB 指示灯颜色 (0x30C5 getDpiValue)
        dpi_b = _am35_query(dev, iface, emit, "dpi_0x30C5", 0x30C5, bytes([profile]))
        if dpi_b and len(dpi_b) >= 4:
            cur_slot, total_slots = dpi_b[1], min(dpi_b[2], 8)
            pos = 4
            stages = []
            for idx in range(total_slots):
                if pos + 7 > len(dpi_b):
                    break
                dx = dpi_b[pos] | (dpi_b[pos + 1] << 8)
                dy = dpi_b[pos + 2] | (dpi_b[pos + 3] << 8)
                color = f"#{dpi_b[pos + 4]:02x}{dpi_b[pos + 5]:02x}{dpi_b[pos + 6]:02x}"
                stages.append({"stage": idx + 1, "x_dpi": dx, "y_dpi": dy, "color_rgb": color})
                pos += 7
            summary["dpi"] = {"active_stage": cur_slot + 1, "stage_count": total_slots, "stages": stages}
            emit("decoded", brand="angry_miao_am35", label="dpi", value=summary["dpi"])

        # 传感器与休眠参数 (0x3013 休眠 / 0x30CD LOD / 0x30D3 MotionSync / 0x30D5 直线修正 / 0x30D7 波纹 / 0x30E9 消抖)
        sensor = {}
        for key, cmd in (
            ("sleep_time_0x3013", 0x3013),
            ("lod_0x30CD", 0x30CD),
            ("motion_sync_0x30D3", 0x30D3),
            ("angle_snapping_0x30D5", 0x30D5),
            ("ripple_control_0x30D7", 0x30D7),
            ("sw_debounce_0x30E9", 0x30E9),
        ):
            resp = _am35_query(dev, iface, emit, key, cmd, bytes([profile]))
            if resp:
                sensor[key] = resp.hex()
        if sensor:
            summary["sensor_params"] = sensor
            emit("decoded", brand="angry_miao_am35", label="sensor_params", value=sensor)

        # 灯光 (0x300E 接收器灯效 + 0x0A0C NVData 鼠标灯效 0x600A)
        lighting = {}
        dl = _am35_query(dev, iface, emit, "dongle_light_0x300E", 0x300E)
        if dl:
            lighting["dongle_light_raw"] = dl.hex()
        nv_light = _am35_query(dev, iface, emit, "mouse_light_nv_0x0A0C", 0x0A0C, bytes([0x0A, 0x60, 12]))
        if nv_light:
            lighting["mouse_light_nv_0x600A"] = nv_light.hex()
        if lighting:
            summary["lighting"] = lighting
            emit("decoded", brand="angry_miao_am35", label="lighting", value=lighting)

    # 分支 B: AM30 / Infinity Mouse (Feature 0x00 65B, UsagePage 0xFF01)
    if iface.usage_page == 0xFF01 and (dev.caps.FeatureReportByteLength or iface.feature_len) >= 64 and "dpi" not in summary:
        bat30 = _am30_query(dev, emit, "am30_battery_0xD6", 0xD6)
        if bat30 and bat30[3] <= 100:
            summary["battery"] = {"level_percent": bat30[3], "charging": bool(bat30[4] == 1)}
            emit("decoded", brand="angry_miao_am30", label="battery", value=summary["battery"])
        dpi30 = _am30_query(dev, emit, "am30_dpi_0xD4", 0xD4)
        if dpi30:
            cur_slot, total_slots = dpi30[4], min(dpi30[3], 8)
            stages = []
            for idx in range(total_slots):
                raw_v = (dpi30[5 + idx * 2] << 8) | dpi30[6 + idx * 2]
                c_off = 21 + idx * 3
                color = f"#{dpi30[c_off]:02x}{dpi30[c_off+1]:02x}{dpi30[c_off+2]:02x}"
                stages.append({"stage": idx + 1, "dpi": (raw_v + 1) * 50, "color_rgb": color})
            summary["dpi"] = {"active_stage": cur_slot + 1, "stage_count": total_slots, "stages": stages}
            emit("decoded", brand="angry_miao_am30", label="dpi", value=summary["dpi"])
        param30 = _am30_query(dev, emit, "am30_params_0xD3", 0xD3)
        if param30:
            summary["polling_rate"] = {
                "rate_code": param30[2],
                "polling_rate_hz": AM35_POLLING_MAP.get(param30[2], param30[2]),
                "lod_mm": 2 if param30[3] == 2 else 1,
                "ripple": bool(param30[4]),
                "angle_snapping": bool(param30[5]),
                "motion_sync": bool(param30[6]),
                "debounce_ms": param30[7],
            }
            emit("decoded", brand="angry_miao_am30", label="polling_and_sensor", value=summary["polling_rate"])
        light30 = _am30_query(dev, emit, "am30_dongle_light_0x88", 0x88)
        if light30:
            summary["lighting"] = {"dongle_light_mode": light30[2], "raw": light30[:8].hex()}
            emit("decoded", brand="angry_miao_am30", label="lighting", value=summary["lighting"])

    return summary


# ============================================================================
# 4. Logitech (罗技 HID++ 2.0: Lightspeed / Bolt / Unifying / PRO X Superlight)
# ============================================================================
LOGI_SW_ID = 0x0A


def _hidpp_txrx(dev: HidDevice, emit: EmitFn, label: str, dev_idx: int,
                feat_idx: int, func_id: int, params: tuple[int, ...] = ()) -> Optional[bytes]:
    out_len = dev.caps.OutputReportByteLength or 20
    rid = 0x11 if out_len >= 20 else 0x10
    pkt_len = 20 if rid == 0x11 else 7
    body = [rid, dev_idx, feat_idx, ((func_id & 0x0F) << 4) | LOGI_SW_ID, *params]
    pkt = bytes(body[:pkt_len]).ljust(pkt_len, b"\x00")
    dev.drain()
    emit("tx", brand="logitech_hidpp", label=label, dev_idx=dev_idx, method="output", hex=pkt.hex())
    if not dev.write_output(pkt, use_hidd=False):
        dev.write_output(pkt, use_hidd=True)
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        r = dev.read_input(timeout_ms=90)
        if not r:
            continue
        emit("rx", brand="logitech_hidpp", label=label, dev_idx=dev_idx, method="input", hex=r.hex())
        if len(r) >= 7 and r[0] in (0x10, 0x11) and r[1] == dev_idx:
            if r[2] == 0xFF or r[2] == 0x8F:
                return None
            if r[2] == feat_idx:
                return r
    return None


def probe_logitech_hidpp(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "Logitech (罗技 HID++ 2.0)"}
    if iface.usage_page < 0xFF00 or (iface.output_len not in (7, 20) and iface.input_len not in (7, 20)):
        return summary

    devices_found = []
    for dev_idx in (1, 2, 0xFF, 3, 4):
        ping = _hidpp_txrx(dev, emit, f"root_ping_idx{dev_idx}", dev_idx, 0x00, 0x01, (0x00, 0x00, 0x5A))
        if not ping or ping[6] != 0x5A:
            continue
        dev_info: dict = {"dev_idx": dev_idx, "protocol_version": f"{ping[4]}.{ping[5]}"}

        def resolve_feature(fid: int) -> int:
            r = _hidpp_txrx(dev, emit, f"get_feat_0x{fid:04X}", dev_idx, 0x00, 0x00, (fid >> 8, fid & 0xFF))
            return r[4] if r and len(r) >= 5 else 0

        # 枚举 FeatureSet (0x0001)
        fset_idx = resolve_feature(0x0001)
        if fset_idx:
            cnt_r = _hidpp_txrx(dev, emit, "feature_count", dev_idx, fset_idx, 0x00)
            count = cnt_r[4] if cnt_r else 0
            feat_table = {}
            for i in range(1, min(count + 1, 45)):
                fr = _hidpp_txrx(dev, emit, f"feature_id_{i}", dev_idx, fset_idx, 0x01, (i,))
                if fr and len(fr) >= 6:
                    fid = (fr[4] << 8) | fr[5]
                    feat_table[f"0x{fid:04X}"] = i
            dev_info["features"] = feat_table

        # 设备名称 (0x0005)
        name_idx = resolve_feature(0x0005)
        if name_idx:
            nr = _hidpp_txrx(dev, emit, "device_name_len", dev_idx, name_idx, 0x00)
            nlen = nr[4] if nr else 0
            chars = bytearray()
            while len(chars) < min(nlen, 32):
                cr = _hidpp_txrx(dev, emit, f"device_name_chunk_{len(chars)}", dev_idx, name_idx, 0x01, (len(chars),))
                if not cr:
                    break
                chars.extend(cr[4:20])
            dev_info["device_name"] = bytes(chars[:nlen]).decode("utf-8", errors="ignore").strip("\x00")

        # 电量 (0x1004 UnifiedBattery / 0x1000 BatteryStatus / 0x1001 BatteryVoltage)
        for bat_fid in (0x1004, 0x1000, 0x1001):
            bidx = resolve_feature(bat_fid)
            if not bidx:
                continue
            br = _hidpp_txrx(dev, emit, f"battery_0x{bat_fid:04X}", dev_idx, bidx, 0x01 if bat_fid == 0x1004 else 0x00)
            if br:
                if bat_fid == 0x1001:
                    mv = (br[4] << 8) | br[5]
                    dev_info["battery"] = {"voltage_mv": mv, "charging": bool(br[6] & 0x80), "feature": "0x1001"}
                else:
                    dev_info["battery"] = {"level_percent": br[4], "status_code": br[6], "feature": f"0x{bat_fid:04X}"}
                summary["battery"] = dev_info["battery"]
                break

        # DPI (0x2201 AdjustableDPI / 0x2202 ExtendedAdjustableDPI)
        for dpi_fid in (0x2201, 0x2202):
            didx = resolve_feature(dpi_fid)
            if not didx:
                continue
            dr = _hidpp_txrx(dev, emit, f"dpi_get_0x{dpi_fid:04X}", dev_idx, didx, 0x02, (0x00,))
            if dr and len(dr) >= 9:
                cur_dpi = (dr[5] << 8) | dr[6]
                def_dpi = (dr[7] << 8) | dr[8]
                dev_info["dpi"] = {"current_dpi": cur_dpi, "default_dpi": def_dpi, "feature": f"0x{dpi_fid:04X}"}
                summary["dpi"] = dev_info["dpi"]
                break

        # 回报率 (0x8060 ReportRate / 0x8061 ExtendedReportRate)
        for rate_fid in (0x8060, 0x8061):
            ridx = resolve_feature(rate_fid)
            if not ridx:
                continue
            rr = _hidpp_txrx(dev, emit, f"report_rate_0x{rate_fid:04X}", dev_idx, ridx, 0x01)
            if rr:
                val = rr[4]
                hz = (1000 // val) if (rate_fid == 0x8060 and 1 <= val <= 8) else {0: 125, 1: 250, 2: 500, 3: 1000, 4: 2000, 5: 4000, 6: 8000}.get(val, val)
                dev_info["polling_rate"] = {"raw_code": val, "polling_rate_hz": hz, "feature": f"0x{rate_fid:04X}"}
                summary["polling_rate"] = dev_info["polling_rate"]
                break

        # RGB 灯光特性检测 (0x8070 ColorLEDs / 0x8071 RGBEffects / 0x8100 OnboardProfiles)
        lighting_feats = {}
        for lfid in (0x8070, 0x8071, 0x8100):
            lidx = resolve_feature(lfid)
            if lidx:
                lr = _hidpp_txrx(dev, emit, f"lighting_or_profile_0x{lfid:04X}", dev_idx, lidx, 0x00)
                lighting_feats[f"0x{lfid:04X}"] = lr[4:12].hex() if lr else f"feat_idx_{lidx}"
        if lighting_feats:
            dev_info["lighting_and_profiles"] = lighting_feats
            summary["lighting"] = lighting_feats

        devices_found.append(dev_info)
        emit("decoded", brand="logitech_hidpp", label=f"device_idx_{dev_idx}", value=dev_info)
        if dev_idx in (1, 2):
            break

    if devices_found:
        summary["devices"] = devices_found
    return summary


# ============================================================================
# 5. Razer (雷蛇 0x1532: 90B Feature Report 0x00 只读协议)
# ============================================================================
RAZER_POLLING_MAP = {0x01: 1000, 0x02: 500, 0x08: 125}
RAZER_EXT_POLLING_MAP = {0x01: 8000, 0x02: 4000, 0x04: 2000, 0x08: 1000, 0x10: 500, 0x20: 250, 0x40: 125}


def _razer_pkt(tx_id: int, cmd_class: int, cmd_id: int, data_size: int, args: bytes = b"") -> bytes:
    buf = bytearray(91)
    buf[0] = 0x00  # Report ID 0
    buf[1] = 0x00  # Status: New Command
    buf[2] = tx_id
    buf[6] = data_size
    buf[7] = cmd_class
    buf[8] = cmd_id
    buf[9 : 9 + len(args)] = args[:80]
    crc = 0
    for b in buf[3:89]:
        crc ^= b
    buf[89] = crc
    return bytes(buf)


def _razer_query(dev: HidDevice, emit: EmitFn, label: str, tx_id: int,
                 cmd_class: int, cmd_id: int, data_size: int, args: bytes = b"") -> Optional[bytes]:
    assert (cmd_id & 0x80) != 0, "Safety: Razer diagnostic only sends getter commands (bit7=1)"
    pkt = _razer_pkt(tx_id, cmd_class, cmd_id, data_size, args)
    emit("tx", brand="razer", label=label, tx_id=f"0x{tx_id:02X}", method="feature_0x00", hex=pkt.hex())
    if not dev.set_feature(pkt):
        return None
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        time.sleep(0.025)
        r = dev.get_feature(0x00, 91)
        if not r or len(r) < 90:
            continue
        status = r[1]
        if status == 0x01:  # Busy
            continue
        emit("rx", brand="razer", label=label, tx_id=f"0x{tx_id:02X}", method="feature_0x00", hex=r.hex())
        if status == 0x02 and r[7] == cmd_class and r[8] == cmd_id:
            return r[9 : 9 + max(data_size, r[6])]
        break
    return None


def probe_razer(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "Razer (雷蛇)"}
    feat_len = dev.caps.FeatureReportByteLength or iface.feature_len
    if feat_len < 90:
        return summary

    active_tx = None
    for tx_id in (0x1F, 0x3F, 0xFF):
        bat = _razer_query(dev, emit, "battery_07_80", tx_id, 0x07, 0x80, 2)
        if bat and len(bat) >= 2 and bat[1] > 0:
            active_tx = tx_id
            chg = _razer_query(dev, emit, "charging_07_84", tx_id, 0x07, 0x84, 2)
            summary["battery"] = {
                "tx_id": f"0x{tx_id:02X}",
                "raw_0_255": bat[1],
                "level_percent": round(bat[1] * 100 / 255),
                "charging": bool(chg and len(chg) >= 2 and chg[1] == 1),
            }
            emit("decoded", brand="razer", label="battery", value=summary["battery"])
            break

    tx_id = active_tx or 0x1F
    # 当前 DPI (04 85, args=[0x00] NOSTORE) 与多档位 DPI 表 (04 86)
    dpi_cur = _razer_query(dev, emit, "dpi_current_04_85", tx_id, 0x04, 0x85, 7, b"\x00")
    if dpi_cur and len(dpi_cur) >= 5:
        x_dpi = (dpi_cur[1] << 8) | dpi_cur[2]
        y_dpi = (dpi_cur[3] << 8) | dpi_cur[4]
        summary["dpi"] = {"x_dpi": x_dpi, "y_dpi": y_dpi}
        stages_raw = _razer_query(dev, emit, "dpi_stages_04_86", tx_id, 0x04, 0x86, 0x26, b"\x01")
        if stages_raw and len(stages_raw) >= 6:
            active_st = stages_raw[1]
            cnt = min(stages_raw[2], 5)
            stages = []
            pos = 3
            for _ in range(cnt):
                if pos + 7 > len(stages_raw):
                    break
                st_id = stages_raw[pos]
                sx = (stages_raw[pos + 1] << 8) | stages_raw[pos + 2]
                sy = (stages_raw[pos + 3] << 8) | stages_raw[pos + 4]
                stages.append({"stage": st_id, "x_dpi": sx, "y_dpi": sy})
                pos += 7
            summary["dpi"]["active_stage"] = active_st
            summary["dpi"]["stages"] = stages
        emit("decoded", brand="razer", label="dpi", value=summary["dpi"])

    # 回报率 (00 85 传统回报率 / 00 C0 HyperPolling 4K/8K 回报率)
    poll_ext = _razer_query(dev, emit, "polling_ext_00_C0", tx_id, 0x00, 0xC0, 2, b"\x00")
    poll_std = _razer_query(dev, emit, "polling_std_00_85", tx_id, 0x00, 0x85, 1)
    if poll_ext and len(poll_ext) >= 2 and poll_ext[1] in RAZER_EXT_POLLING_MAP:
        summary["polling_rate"] = {"code": f"0x{poll_ext[1]:02X}", "polling_rate_hz": RAZER_EXT_POLLING_MAP[poll_ext[1]], "type": "HyperPolling_00_C0"}
    elif poll_std and len(poll_std) >= 1 and poll_std[0] in RAZER_POLLING_MAP:
        summary["polling_rate"] = {"code": f"0x{poll_std[0]:02X}", "polling_rate_hz": RAZER_POLLING_MAP[poll_std[0]], "type": "Standard_00_85"}
    if "polling_rate" in summary:
        emit("decoded", brand="razer", label="polling_rate", value=summary["polling_rate"])

    # 灯光亮度 (0F 84 Extended Matrix Brightness: varstore=0x01, led_id=0x01 Scroll / 0x04 Logo)
    lighting = {}
    for led_name, led_id in (("scroll_led_0x01", 0x01), ("logo_led_0x04", 0x04), ("backlight_0x05", 0x05)):
        lb = _razer_query(dev, emit, f"lighting_brightness_{led_name}", tx_id, 0x0F, 0x84, 3, bytes([0x01, led_id]))
        if lb and len(lb) >= 3:
            lighting[led_name] = {"brightness_0_255": lb[2], "brightness_percent": round(lb[2] * 100 / 255)}
    if lighting:
        summary["lighting"] = lighting
        emit("decoded", brand="razer", label="lighting", value=lighting)

    return summary


# ============================================================================
# 6. MCHOSE (迈从: M HUB V2 反码协议 / M HUB V3 0x4D 协议 / G7)
# ============================================================================
MCHOSE_V2_RATES = [125, 250, 500, 1000, 2000, 4000, 8000]


def _mchose_v2_query(dev: HidDevice, emit: EmitFn, label: str, report_id: int, subcmd: int) -> Optional[bytes]:
    pkt = bytearray(b"\xFF" * 32)
    pkt[0] = report_id
    pkt[1] = 0xFF ^ subcmd
    emit("tx", brand="mchose_v2", label=label, method=f"feature_0x{report_id:02X}", hex=bytes(pkt).hex())
    if not dev.set_feature(bytes(pkt)):
        return None
    time.sleep(0.03)
    r = dev.get_feature(report_id, 32)
    if not r or len(r) < 32:
        return None
    emit("rx", brand="mchose_v2", label=label, method=f"feature_0x{report_id:02X}", hex=r.hex())
    decoded = bytes([r[0]] + [0xFF ^ b for b in r[1:]])
    return decoded if decoded[1] == subcmd else None


def _mchose_v3_query(dev: HidDevice, emit: EmitFn, label: str, cmd_word: int, payload: bytes = b"") -> Optional[bytes]:
    buf = bytearray(65)
    buf[0] = 0x4D
    buf[1] = (cmd_word >> 8) & 0xFF
    buf[2] = cmd_word & 0xFF
    buf[6] = len(payload)
    buf[8 : 8 + len(payload)] = payload
    buf[7] = (0xFF - (sum(buf[1:]) & 0xFF)) & 0xFF
    dev.drain()
    emit("tx", brand="mchose_v3", label=label, method="output_0x4D", hex=bytes(buf).hex())
    if not dev.write_output(bytes(buf), use_hidd=False):
        dev.write_output(bytes(buf), use_hidd=True)
    deadline = time.monotonic() + 0.35
    while time.monotonic() < deadline:
        r = dev.read_input(timeout_ms=90)
        if not r:
            continue
        emit("rx", brand="mchose_v3", label=label, method="input_0x4D", hex=r.hex())
        if len(r) >= 12 and r[0] == 0x4D and r[1] == buf[1] and r[2] == buf[2]:
            return r
    return None


def probe_mchose(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "MCHOSE (迈从)"}
    feat_ids = set(report_ids_for(dev, 2))
    out_ids = set(report_ids_for(dev, 1))

    # 分支 A: M HUB V2 (Feature 0x11 & 0x12, UsagePage 0xFF01)
    if {0x11, 0x12}.issubset(feat_ids) or (iface.usage_page == 0xFF01 and (dev.caps.FeatureReportByteLength or 0) == 32):
        bat_r = _mchose_v2_query(dev, emit, "v2_battery_0x06", 0x11, 0x06)
        if bat_r and bat_r[2] == 1 and bat_r[6] <= 100:
            real_pid = (bat_r[7] << 8) | bat_r[8]
            summary["battery"] = {
                "level_percent": bat_r[6],
                "charging": bool(bat_r[5] & 0x02),
                "wireless_connected": bool(bat_r[5] & 0x01),
                "real_mouse_pid": f"0x{real_pid:04X}",
            }
            emit("decoded", brand="mchose_v2", label="battery", value=summary["battery"])

        # 读取 64B 配置块 (两次 0x12/0x67 拼接)
        p1 = _mchose_v2_query(dev, emit, "v2_config_part1_0x67", 0x12, 0x67)
        p2 = _mchose_v2_query(dev, emit, "v2_config_part2_0x67", 0x12, 0x67) if p1 else None
        if p1 and p2:
            cfg = p1[4:32] + p2[4:32]
            rate_idx = (cfg[1] >> 4) & 0x0F
            dpi_idx = cfg[1] & 0x0F
            stage_cnt = min(max(cfg[14] & 0x0F, 1), 6)
            stages = [int.from_bytes(cfg[2 + i * 2 : 4 + i * 2], "little") for i in range(stage_cnt)]
            sensor_bits = cfg[15]
            summary["polling_rate"] = {
                "rate_index": rate_idx,
                "polling_rate_hz": MCHOSE_V2_RATES[rate_idx] if rate_idx < len(MCHOSE_V2_RATES) else rate_idx,
            }
            summary["dpi"] = {"active_stage": dpi_idx + 1, "stage_count": stage_cnt, "stages": stages}
            summary["sensor_params"] = {
                "profile": cfg[0],
                "lod_code": sensor_bits & 0x03,
                "motion_sync": bool((sensor_bits >> 2) & 1),
                "ripple_control": bool((sensor_bits >> 3) & 1),
                "angle_snapping": bool((sensor_bits >> 4) & 1),
                "perf_mode": (sensor_bits >> 5) & 0x03,
                "debounce_ms": cfg[16],
                "sleep_raw": cfg[17],
            }
            emit("decoded", brand="mchose_v2", label="config_0x67", value={"polling": summary["polling_rate"], "dpi": summary["dpi"], "sensor": summary["sensor_params"]})

    # 分支 B: M HUB V3 (Report ID 0x4D, 65B)
    if 0x4D in out_ids or (iface.usage_page == 0xFF01 and iface.output_len == 65):
        r_info = _mchose_v3_query(dev, emit, "v3_info_0x0900", 0x0900)
        if r_info and r_info[8] <= 100:
            summary["battery"] = {"level_percent": r_info[8], "charging": bool(r_info[9] == 1), "dpi_stage_count": r_info[14]}
            emit("decoded", brand="mchose_v3", label="battery", value=summary["battery"])
        r_set = _mchose_v3_query(dev, emit, "v3_settings_0x0002", 0x0002, b"\x00")
        if r_set:
            rate_code = r_set[10]
            summary["polling_rate"] = {"rate_code": rate_code, "polling_rate_hz": {1: 1000, 2: 500, 4: 250, 8: 125, 16: 2000, 32: 4000, 64: 8000}.get(rate_code, rate_code)}
            emit("decoded", brand="mchose_v3", label="polling_rate", value=summary["polling_rate"])
        r_dpi = _mchose_v3_query(dev, emit, "v3_dpi_0x0003", 0x0003, b"\x00")
        if r_dpi:
            stages = []
            for i in range(6):
                off = 9 + i * 7
                if off + 7 <= len(r_dpi):
                    dx = r_dpi[off] | (r_dpi[off + 1] << 8)
                    dy = r_dpi[off + 2] | (r_dpi[off + 3] << 8)
                    col = f"#{r_dpi[off+4]:02x}{r_dpi[off+5]:02x}{r_dpi[off+6]:02x}"
                    stages.append({"stage": i + 1, "x_dpi": dx, "y_dpi": dy, "color_rgb": col})
            summary["dpi"] = {"stages": stages}
            emit("decoded", brand="mchose_v3", label="dpi", value=summary["dpi"])

    # 分支 C: MCHOSE G7 (0xA8A5:0x2255)
    if (iface.vid, iface.pid) == (0xA8A5, 0x2255) and iface.output_len >= 33:
        pkt = bytes([0x00, 0x55, 0x30, 0xA5, 0x02, 0x01]).ljust(iface.output_len, b"\x00")
        dev.drain()
        emit("tx", brand="mchose_g7", label="battery_g7", method="output", hex=pkt.hex())
        dev.write_output(pkt, use_hidd=False)
        r = dev.read_input(timeout_ms=250)
        if r and len(r) >= 10:
            emit("rx", brand="mchose_g7", label="battery_g7", method="input", hex=r.hex())
            if r[8] <= 100:
                summary["battery"] = {"level_percent": r[8], "charging": bool(r[9] == 1)}
                emit("decoded", brand="mchose_g7", label="battery", value=summary["battery"])

    return summary


# ============================================================================
# 7. CompX 64B Page Feature (Lamzu / WLmouse / Attack Shark / Vancer / A5 Gen1)
# ============================================================================
def _compx_page_query(dev: HidDevice, emit: EmitFn, label: str, page: int, cmd: int, sz: int = 2) -> Optional[bytes]:
    pkt = bytearray(65)
    pkt[0] = 0x00  # Report ID 0
    pkt[1] = 0x00
    pkt[2] = 0x00
    pkt[3] = page & 0xFF
    pkt[4] = sz & 0xFF
    pkt[5] = cmd & 0xFF
    emit("tx", brand="compx_page64", label=label, method="feature_0x00", hex=bytes(pkt).hex())
    if not dev.set_feature(bytes(pkt)):
        return None
    time.sleep(0.03)
    r = dev.get_feature(0x00, 65)
    if r and len(r) >= 12 and r[3] == page and r[5] == cmd:
        emit("rx", brand="compx_page64", label=label, method="feature_0x00", hex=r.hex())
        return r
    return None


def probe_compx_page_and_wlmouse(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {"brand": "Lamzu / WLmouse / CompX-Page"}
    feat_len = dev.caps.FeatureReportByteLength or iface.feature_len

    # 分支 A: CompX 64B Page Feature 协议 (UsagePage 0xFFFF, Feature 65B)
    if iface.usage_page == 0xFFFF and feat_len >= 64:
        bat = _compx_page_query(dev, emit, "battery_p0_0x83", 0x00, 0x83, 2)
        if bat and 0 < bat[6] <= 100:
            summary["battery"] = {"level_percent": bat[6], "charging": bool(bat[7] == 1)}
            emit("decoded", brand="compx_page64", label="battery", value=summary["battery"])
        prof = _compx_page_query(dev, emit, "profile_p0_0x85", 0x00, 0x85, 1)
        if prof:
            summary["active_profile"] = prof[6]
        poll = _compx_page_query(dev, emit, "polling_p0_0x9F", 0x00, 0x9F, 1)
        if poll:
            summary["polling_rate"] = {"raw_code": poll[6]}
            emit("decoded", brand="compx_page64", label="polling_rate", value=summary["polling_rate"])
        dpi_st = _compx_page_query(dev, emit, "dpi_stage_p1_0x82", 0x01, 0x82, 1)
        dpi_val = _compx_page_query(dev, emit, "dpi_table_p1_0x81", 0x01, 0x81, 16)
        if dpi_st or dpi_val:
            summary["dpi"] = {
                "active_stage_raw": dpi_st[6] if dpi_st else None,
                "table_raw": dpi_val[6:22].hex() if dpi_val else None,
            }
            emit("decoded", brand="compx_page64", label="dpi", value=summary["dpi"])
        light = _compx_page_query(dev, emit, "receiver_light_p2_0x80", 0x02, 0x80, 2)
        if light:
            summary["lighting"] = {"receiver_light_raw": light[6:10].hex()}
            emit("decoded", brand="compx_page64", label="lighting", value=summary["lighting"])

    # 分支 B: WLmouse Beast X 4K (0x36A7:0xA887, UsagePage 0xFF1C, Report ID 0x04)
    if iface.usage_page == 0xFF1C and (iface.output_len >= 64 or feat_len >= 64):
        body = bytearray(63)
        body[2] = 0x02
        body[3] = 0x02
        body[4] = 0x1A  # Query Battery
        crc = _am30_crc16_xmodem(bytes(body[:61]))
        body[61] = (crc >> 8) & 0xFF
        body[62] = crc & 0xFF
        pkt = bytes([0x04]) + bytes(body)
        dev.drain()
        emit("tx", brand="wlmouse_4k", label="battery_0x1A", method="output_0x04", hex=pkt.hex())
        dev.write_output(pkt, use_hidd=False)
        r = dev.read_input(timeout_ms=250)
        if r and len(r) >= 9 and r[0] == 0x04 and r[5] == 0x1A:
            emit("rx", brand="wlmouse_4k", label="battery_0x1A", method="input_0x04", hex=r.hex())
            summary["battery"] = {"charging": bool(r[6]), "level_percent": r[7]}
            emit("decoded", brand="wlmouse_4k", label="battery", value=summary["battery"])

    return summary


# ============================================================================
# 8. 通用 HID 标准电量 + 厂商私有接口安全 Feature Report 枚举快照
# ============================================================================
def probe_other_and_generic(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    summary: dict = {}

    # 8.1 检查是否声明了标准 HID 电量 Usage
    std_bat_nodes = [
        v for v in dev.all_value_caps()
        if (v["usage_page"], v["usage"]) in ((0x0006, 0x0020), (0x0085, 0x0064), (0x0085, 0x0065), (0x000D, 0x003B))
    ]
    if std_bat_nodes:
        summary["standard_battery_usages"] = [
            {
                "report_type": v["report_type"],
                "report_id": v["report_id"],
                "usage_page": f"0x{v['usage_page']:04X}",
                "usage": f"0x{v['usage']:04X}",
                "bit_size": v["bit_size"],
            }
            for v in std_bat_nodes
        ]
        emit("decoded", brand="generic_hid", label="standard_battery_usages", value=summary["standard_battery_usages"])

    # 8.2 对所有声明的 Feature Report ID 执行安全只读 HidD_GetFeature 快照
    feat_len = dev.caps.FeatureReportByteLength or iface.feature_len
    if feat_len > 0 and (iface.usage_page >= 0xFF00 or std_bat_nodes):
        rids = report_ids_for(dev, 2)
        feat_dumps = {}
        for rid in rids[:12]:
            raw = dev.get_feature(rid, feat_len)
            if raw:
                feat_dumps[f"0x{rid:02X}"] = raw.hex()
                emit("rx", brand="generic_feature_dump", label=f"get_feature_0x{rid:02X}", method="feature", hex=raw.hex())
        if feat_dumps:
            summary["feature_report_dump"] = feat_dumps

    return summary


# ============================================================================
# 9. 顶层接口探测调度器
# ============================================================================
def run_interface_probes(dev: HidDevice, iface: HidInterface, emit: EmitFn) -> dict:
    """根据 VID/PID 与 UsagePage 调度对应品牌协议探测 + 通用 Feature 快照。"""
    vid = iface.vid
    results: dict = {
        "brand_detected": BRAND_VIDS.get(vid, f"VID_0x{vid:04X}"),
        "probes_executed": [],
        "capabilities": {},
    }

    def merge_probe(name: str, data: dict) -> None:
        results["probes_executed"].append(name)
        for k, v in data.items():
            if k != "brand" and v:
                results["capabilities"][k] = v

    dev.trace = emit
    if iface.reserved:
        emit('probe_skipped', reason='System keyboard/mouse input interface')
        return results
    if wk.matches(iface):
        try:
            parsed,raw=wk.read(dev,iface,emit)
            merge_probe('weisheng_keyboard',{'battery':{'level_percent':parsed[0],'charging':parsed[1]=='charging'}} if parsed else {})
        except (OSError,ValueError) as exc:
            emit('query_error',brand='weisheng_keyboard',message=str(exc))
    elif battery_catalog.dm.matches(iface):
        parsed,raw=battery_catalog.dm.read(dev,iface,emit)
        merge_probe('darmoshark_m3pro',{'battery':{'level_percent':parsed[0],'status':parsed[1],'model':parsed[2]}} if parsed else {})
    elif xiaomi_probe.xm.claims(iface):
        merge_probe('xiaomi_mouse2',xiaomi_probe.probe(dev,iface,emit))
    elif (vid, iface.pid) in audited_am.MODELS:
        am_caps = {}
        previous_emit = audited_am.emit
        def am_emit(event, **fields):
            fields.pop('interface_id', None)
            emit(event, brand='angry_miao_audited', **fields)
            if event == 'result' and fields.get('label') == 'dpi':
                am_caps['dpi'] = fields['value']
            elif event == 'result' and fields.get('label') == 'battery_raw':
                am_caps['battery'] = {'raw': fields['value'], 'decode_status': 'pending'}
        try:
            audited_am.emit = am_emit
            audited_am.probe(iface, hw)
        finally:
            audited_am.emit = previous_emit
        merge_probe('angry_miao_audited', am_caps)
        merge_probe('angry_miao_controls',am_controls_probe.probe(dev,iface,emit))
    elif vid == 0x0B05 and iface.pid in (0x1ACE, 0x1B0A, 0x1A94):
        merge_probe("asus_rog", probe_asus_rog(dev, iface, emit))
    elif (vid in (0x373B, 0x3554) or vid == 0x3710 and iface.pid in (0x5406, 0x3414)) and (iface.usage_page, iface.usage) == (0xFF02, 2) and iface.output_len in (17, 64) and iface.input_len >= 17:
        merge_probe("atk_compx", probe_atk_compx(dev, iface, emit))
    elif vid == 0x046D:
        merge_probe("logitech_hidpp", probe_logitech_hidpp(dev, iface, emit))
    elif vid == 0x1532 and iface.usage_page >= 0xFF00 and iface.feature_len in (90, 91):
        merge_probe("razer", probe_razer(dev, iface, emit))
    elif (vid in (0x3837, 0x5253) or (vid, iface.pid) == (0xA8A5, 0x2255)) and iface.usage_page == 0xFF01:
        merge_probe("mchose", probe_mchose(dev, iface, emit))
    elif ((vid == 0x373E and iface.pid in (0x001C, 0x001D, 0x001E)) or (vid == 0x36A7 and iface.pid in (0xA868, 0xA869, 0xA870, 0xA871, 0xA880, 0xA881, 0xA882, 0xA883, 0xA884, 0xA885, 0xA886, 0xA887)) or (vid == 0x2023 and iface.pid in (0xF013, 0xF015, 0xF019))):
        merge_probe("compx_page_or_wlmouse", probe_compx_page_and_wlmouse(dev, iface, emit))
    else:
        profile=battery_catalog.profile_for(iface)
        route=wireless_catalog.route(iface)
        if profile and profile.protocol not in ('incott','delux') and battery_catalog.matches_interface(iface):
            merge_probe('monitor_battery_catalog',{})
            try:
                parsed,raw=battery_catalog._active_read(dev,iface,profile.protocol)
                if parsed is not None:
                    merge_probe('monitor_battery_result',{'battery':{'level_percent':parsed[0],'status':parsed[1],'model':profile.model}})
            except (OSError,ValueError) as exc:emit('query_error',brand=profile.protocol,message=str(exc))
        elif route:
            merge_probe('monitor_wireless_catalog',{})
            try:
                response=wireless_catalog.query(dev,iface,route)
                if response:
                    parsed,raw=response
                    merge_probe('monitor_battery_result',{'battery':{'level_percent':parsed[0],'status':parsed[1],'voltage_mv':parsed[2]}})
            except (OSError,ValueError) as exc:emit('query_error',brand=route,message=str(exc))
        else:
            emit('probe_skipped', reason='No matched protocol route; descriptor and vendor notifications only')

    # 始终补充标准电量节点检查与安全的只读 Feature 快照
    merge_probe("generic_safe_dump", probe_other_and_generic(dev, iface, emit))
    return results
