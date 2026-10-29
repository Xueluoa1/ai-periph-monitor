# -*- coding: utf-8 -*-
"""
多品牌 2.4G/有线 外设配置诊断与日志生成工具 (Peripheral Configuration Diagnostic Tool)
=================================================================================
安全承诺：
  - 仅通过只读查询命令 (Getter / EEPROM Read / HidD_GetFeature) 读取外设当前状态。
  - 绝不向鼠标或键盘发送改写参数、重置配对、擦除 Flash 或固件升级指令。
  - 每个外设接口均在带超时的独立子进程中探测，防止异常驱动阻塞主程序。

支持命令行参数：
  - 默认双击无参运行：自动扫描所有已知外设品牌 + 未知 2.4G 私有接口，生成 ZIP 诊断包。
  - `--all`：探测所有非标准系统 HID 厂商接口。
  - `--vid 0xXXXX`：额外指定需要深度探测的厂商 VID。
  - `--diff <诊断包A.zip> <诊断包B.zip>`：对比两次采集快照的原始报文差异（用于逆向新功能）。
  - `--no-pause`：采集完成后不等待回车直接退出（适合脚本自动化调用）。
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
from types import SimpleNamespace

from hid_win import HidInterface, enumerate_hid
from protocol_probes import BRAND_VIDS, HidDevice, report_ids_for, run_interface_probes
import hid_evidence
import receiver_battery

VERSION = "1.5.0"
BUILD_ID = '20261029-gamepads-headsets'
WORKER_TIMEOUT_S = 18.0
INVENTORY_TIMEOUT_S = 20.0
TOTAL_TIMEOUT_S = 180.0
SUBPROCESS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class JsonlWriter:
    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        self.seq = 0
        self._fh = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, phase: str, event: str, **fields) -> None:
        self.seq += 1
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "seq": self.seq,
            "phase": phase,
            "event": event,
            **fields,
        }
        self._fh.write(redact_username(json.dumps(rec, ensure_ascii=False)) + "\n")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def redact_username(text: str) -> str:
    if not text:
        return text
    try:
        user = getpass.getuser()
        if user:
            text = re.sub(re.escape(user), "<USER>", text, flags=re.IGNORECASE)
    except Exception:
        pass
    return text


def extract_mi_col(path: str) -> tuple[str, str]:
    m_mi = re.search(r"mi_([0-9a-fA-F]{2})", path, re.IGNORECASE)
    m_col = re.search(r"col([0-9a-fA-F]{2})", path, re.IGNORECASE)
    return (m_mi.group(1).lower() if m_mi else "", m_col.group(1).lower() if m_col else "")


def load_pnp_info(vids: set[int]) -> list[dict]:
    if not vids:
        return []
    vid_patterns = "|".join(f"VID_{v:04X}" for v in sorted(vids))
    ps_cmd = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "Get-PnpDevice -PresentOnly | "
        f"Where-Object {{ $_.InstanceId -match '({vid_patterns})' }} | "
        "Select-Object Status,Class,FriendlyName,InstanceId | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=6.0, creationflags=SUBPROCESS_NO_WINDOW,
        )
        raw = res.stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        return [data] if isinstance(data, dict) else data
    except Exception as exc:
        return [{"error": repr(exc)}]


def should_probe_interface(iface_rec: dict, probe_all: bool, extra_vids: set[int]) -> bool:
    """判断该 HID 接口是否需要进行协议探测（跳过纯按键/多媒体输入接口，聚焦双向控制与特征通道）。"""
    vid = iface_rec["vid"]
    up = iface_rec["usage_page"]
    out_len = iface_rec["output_len"]
    feat_len = iface_rec["feature_len"]

    # Known passive gamepad reports must also run when Output/Feature are absent.
    if 'pid' in iface_rec:
        candidate = SimpleNamespace(**iface_rec)
        if receiver_battery.profile_for(candidate):
            return receiver_battery.matches(candidate)

    # 跳过纯系统鼠标/键盘/多媒体输入通道 (无 Feature Report 且 Output<=4B 仅为 CapsLock 指示灯)
    if up in (0x0001, 0x000C) and feat_len == 0 and out_len <= 4:
        return False
    # 跳过既无 Output 也无 Feature 的只读通知通道（除非位于标准电池页）
    if out_len == 0 and feat_len == 0 and up < 0xFF00 and up not in (0x0006, 0x0085, 0x000D):
        return False

    if vid in BRAND_VIDS or vid in extra_vids:
        return up >= 0xFF00 or feat_len > 0 or out_len >= 7 or up in (0x0006, 0x0085, 0x000D)

    if up >= 0xFF00 or probe_all and feat_len > 0:
        return True

    if (up, iface_rec["usage"]) == (0xFF02, 0x0002) and out_len in (17, 64):
        return True
    if up in (0xFFFF, 0xFF01, 0xFF00, 0xFF1C, 0xFF60, 0xFF67, 0xFF68) and (feat_len >= 16 or out_len >= 16):
        return True
    return False


def worker_inventory(out_dir: Path, probe_all: bool, extra_vids: set[int]) -> int:
    run_meta = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    run_id = run_meta["run_id"]
    writer = JsonlWriter(out_dir / "inventory.jsonl", run_id)
    try:
        all_ifaces = enumerate_hid()
        records = []
        vids_present: set[int] = set()
        for idx, iface in enumerate(all_ifaces):
            mi, col = extract_mi_col(iface.path)
            rec = asdict(iface)
            token = hashlib.sha256(iface.path.lower().encode()).hexdigest()[:12]
            rec["interface_id"] = f"iface_vid{iface.vid:04x}_pid{iface.pid:04x}_mi{mi or 'xx'}_col{col or 'xx'}_{token}"
            rec["brand_name"] = BRAND_VIDS.get(iface.vid, f"未知/其他 (0x{iface.vid:04X})")
            if (iface.vid, iface.pid) == (0x320F, 0x221D):
                rec["brand_name"] = '多彩 DELUX M618XSD（用户型号与官方驱动核对）'
            if (iface.vid, iface.pid) == (0x1A2C, 0x8FFF):
                rec["brand_name"] = 'XY 2.4G 键盘接收器（含黑爵 AK820 Max Plus，共用 ID）'
            rec["vid_hex"] = f"0x{iface.vid:04X}"
            rec["pid_hex"] = f"0x{iface.pid:04X}"
            rec["usage_page_hex"] = f"0x{iface.usage_page:04X}"
            rec["usage_hex"] = f"0x{iface.usage:04X}"
            rec["should_probe"] = should_probe_interface(rec, probe_all, extra_vids)
            from hid_win import physical_group_key
            rec['physical_device_token']=hashlib.sha256(physical_group_key(iface.path).lower().encode()).hexdigest()[:16]
            rec['interface_number']=mi
            rec['collection_number']=col
            records.append(rec)
            if rec["should_probe"] or iface.vid in BRAND_VIDS:
                vids_present.add(iface.vid)
            public = {k: v for k, v in rec.items() if k not in ("path", "serial", "group")}
            writer.write("inventory", "hid_interface", **public)

        pnp_list = load_pnp_info(vids_present)
        for item in pnp_list:
            if "InstanceId" in item:
                item["instance_token"] = hashlib.sha256(item.pop("InstanceId").encode()).hexdigest()[:12]
        writer.write("inventory", "pnp_devices", devices=pnp_list)
        (out_dir / "_interfaces.json").write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        writer.close()
    return 0


def worker_system(out_dir: Path) -> int:
    import system_battery_evidence
    run_id = json.loads((out_dir / 'run.json').read_text(encoding='utf8'))['run_id']
    writer = JsonlWriter(out_dir / 'system_battery.jsonl', run_id)
    try:
        system_battery_evidence.collect(lambda event, **fields: writer.write('system', event, **fields))
    finally:
        writer.close()
    return 0


def worker_probe(out_dir: Path, iface_id: str) -> int:
    run_meta = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    run_id = run_meta["run_id"]
    records = json.loads((out_dir / "_interfaces.json").read_text(encoding="utf-8"))
    target = next((r for r in records if r["interface_id"] == iface_id), None)
    if not target:
        return 2

    writer = JsonlWriter(out_dir / f"{iface_id}.jsonl", run_id)
    iface = HidInterface(**{k: target[k] for k in HidInterface.__dataclass_fields__})

    def emit(event: str, **kw) -> None:
        writer.write("probe", event, interface_id=iface_id, **kw)

    try:
        emit("open_start", vid=target["vid_hex"], pid=target["pid_hex"],
             brand=target["brand_name"], product=iface.product,
             usage_page=target["usage_page_hex"], usage=target["usage_hex"])
        import jbl_smart_tx
        profile = receiver_battery.profile_for(iface)
        passive = profile is not None and profile.protocol.startswith('eightbitdo')
        dev = HidDevice(iface,write=target['should_probe'] and not passive and not jbl_smart_tx.claims(iface))
        if not dev.open():
            emit("open_failed", error=dev.open_error)
            return 1
        try:
            try:
                evidence=hid_evidence.descriptor(dev)
                emit('hid_descriptor',**evidence)
            except (OSError,ValueError) as exc:
                evidence=None
                emit('descriptor_error',error=str(exc))
            if not target['should_probe']:
                emit('probe_skipped',reason='Descriptor only: ordinary input is not read')
                return 0
            emit(
                "caps_summary",
                access_mode=dev.access_mode,
                input_len=dev.caps.InputReportByteLength,
                output_len=dev.caps.OutputReportByteLength,
                feature_len=dev.caps.FeatureReportByteLength,
                input_report_ids=report_ids_for(dev, 0),
                output_report_ids=report_ids_for(dev, 1),
                feature_report_ids=report_ids_for(dev, 2),
                value_caps=dev.all_value_caps(),
            )
            try:
                if evidence is not None:
                    options=dict(seconds=8.0,max_reports=256,max_unique=32) if jbl_smart_tx.matches(iface) else {}
                    hid_evidence.observe(dev,iface,evidence,emit,**options)
            except (OSError,ValueError) as exc:
                emit('passive_status',status='failed',error=str(exc))
            probe_summary = run_interface_probes(dev, iface, emit)
            emit("interface_summary", summary=probe_summary)
        finally:
            dev.close()
            emit("closed")
    except Exception as exc:
        emit("worker_exception", error=repr(exc), traceback=traceback.format_exc())
        return 1
    finally:
        writer.close()
    return 0


def generate_human_summary(out_dir: Path) -> Path:
    """汇总所有接口探测结果，生成中文人类可读的《诊断摘要与适配报告.txt》。"""
    run_meta = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    ifaces = json.loads((out_dir / "_interfaces.json").read_text(encoding="utf-8"))
    probed_ifaces = [r for r in ifaces if r.get("should_probe")]

    lines = [
        "=" * 78,
        "  多品牌 2.4G/无线外设配置诊断与功能适配报告 (电量 / DPI / 回报率 / 灯光)",
        "=" * 78,
        f"采集时间 (UTC) : {run_meta['created_utc']}",
        f"系统环境       : {run_meta['os']} | Python {run_meta['python']}",
        f"总 HID 接口数  : {len(ifaces)} 个 (其中外设控制/特征接口 {len(probed_ifaces)} 个)",
        "",
        "-" * 78,
        "一、识别到的外设硬件与控制接口列表",
        "-" * 78,
    ]

    for r in probed_ifaces:
        lines.append(
            f"  - [{r['interface_id']}] {r['brand_name']} | {r['product'] or r['manufacturer'] or '未命名接口'}\n"
            f"    VID:PID = {r['vid_hex']}:{r['pid_hex']} | UsagePage:Usage = {r['usage_page_hex']}:{r['usage_hex']} | "
            f"In={r['input_len']}B Out={r['output_len']}B Feat={r['feature_len']}B"
        )
    if not probed_ifaces:
        lines.append("  (未检测到已知无线外设控制接口，请确认 2.4G 接收器已插入，或使用 --all 参数扫描)")

    lines.extend([
        "",
        "-" * 78,
        "二、四大核心能力探测结果 (电量 / DPI档位 / 回报率 / 灯光与传感器)",
        "-" * 78,
    ])

    decoded_count = 0
    for r in probed_ifaces:
        jpath = out_dir / f"{r['interface_id']}.jsonl"
        if not jpath.exists():
            continue
        events = [json.loads(line) for line in jpath.read_text(encoding="utf-8").splitlines() if line.strip()]
        iface_sum = next((e["summary"] for e in events if e.get("event") == "interface_summary"), None)
        caps = (iface_sum or {}).get("capabilities", {})
        if not caps:
            continue
        decoded_count += 1
        lines.append(f"\n> 接口 [{r['interface_id']}] ({r['brand_name']} - {r['product'] or r['pid_hex']})")
        for key, label in (
            ("omni_slots", "0. 接收器配对子设备槽位 (Omni Receiver Slots)"),
            ("profile", "0. 固件版本与当前激活档位 (Firmware & Active Stage)"),
            ("battery", "1. 电量监测 (Battery)"),
            ("dpi", "2. DPI / API 档位 (DPI Stages)"),
            ("polling_rate", "3. 回报率设置 (Polling Rate)"),
            ("lighting", "4. 灯光配置 (RGB Lighting)"),
            ("lod", "5. 静默高度 (LOD Distance)"),
            ("sensor_params", "5. 传感器参数 (LOD/MotionSync/休眠/消抖)"),
            ("identity", "6. 固件/身份标识 (Identity/CID/MID)"),
            ("devices", "7. 接收器槽位设备表 (Receiver Slots)"),
            ("extended_registers", "8. 扩展寄存器原始快照 (Extended Registers)"),
            ("standard_battery_usages", "9. 标准 HID 电量节点声明"),
            ("feature_report_dump", "10. 通用 Feature Report 快照"),
            ("spontaneous_inputs", "11. 被动监听捕获输入帧"),
        ):
            if key in caps:
                val_str = json.dumps(caps[key], ensure_ascii=False, indent=4)
                indented = "\n".join("      " + ln for ln in val_str.splitlines())
                lines.append(f"   [{label}]\n{indented}")

    if decoded_count == 0:
        lines.append("  暂未在已探测接口上收到有效配置响应（可能鼠标处于休眠状态，请晃动鼠标后重新运行）。")

    lines.extend([
        "",
        "-" * 78,
        "三、如何利用本日志适配新品牌/新功能（A/B 快照对比法）",
        "-" * 78,
        "  1. 先在当前状态下运行本诊断工具，得到【诊断包 A.zip】。",
        "  2. 打开外设官方驱动/网页驱动，仅修改一项设置（例如：将回报率从 1000Hz 改为 2000Hz，或切换灯光模式）。",
        "  3. 再次运行本诊断工具，得到【诊断包 B.zip】。",
        "  4. 运行命令：`多品牌外设配置诊断工具.exe --diff 诊断包A.zip 诊断包B.zip`",
        "     工具会自动比对两次日志中所有接口寄存器/Feature Report 的字节差异，直接定位控制偏移量！",
        "=" * 78,
    ])

    report_path = out_dir / "诊断摘要与适配报告.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _load_rx_map(source: Path) -> dict[str, str]:
    """从诊断目录或 ZIP 包中提取每个接口各探测标签的 RX 报文映射，用于 A/B 差异对比。"""
    rx_map: dict[str, str] = {}

    def process_lines(fname: str, text: str) -> None:
        occurrences = {}
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") == "rx" and "hex" in rec:
                key = f"{rec.get('interface_id', fname)} :: {rec.get('brand', '')} :: {rec.get('label', '')}"
                occurrences[key] = occurrences.get(key, 0) + 1
                key += f" :: sample_{occurrences[key]}"
                rx_map[key] = rec["hex"]

    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".jsonl") and "iface_" in name:
                    process_lines(name, zf.read(name).decode("utf-8", errors="ignore"))
    elif source.is_dir():
        for p in sorted(source.glob("iface_*.jsonl")):
            process_lines(p.name, p.read_text(encoding="utf-8", errors="ignore"))
    return rx_map


def diff_runs(path_a: Path, path_b: Path) -> int:
    print(f"正在对比快照 A ({path_a.name}) 与 快照 B ({path_b.name}) ...")
    map_a = _load_rx_map(path_a)
    map_b = _load_rx_map(path_b)
    all_keys = sorted(set(map_a.keys()) | set(map_b.keys()))
    if not all_keys:
        print('两份日志均无可比较响应；不能判断设置是否相同。')
        return 2
    changes = 0
    for key in all_keys:
        ha, hb = map_a.get(key), map_b.get(key)
        if ha == hb:
            continue
        changes += 1
        if ha is None or hb is None:
            print(f"[响应新增/缺失] {key}")
            continue
        ba, bb = bytes.fromhex(ha), bytes.fromhex(hb)
        diff_pos = [
            f"byte[{i}]: 0x{ba[i]:02X} -> 0x{bb[i]:02X}"
            for i in range(min(len(ba), len(bb)))
            if ba[i] != bb[i]
        ]
        print(f"\n[发现变化 #{changes}] {key}")
        print(f"  快照 A: {ha}")
        print(f"  快照 B: {hb}")
        print(f"  差异字节: {', '.join(diff_pos)}")
        if len(ba) != len(bb):
            print(f"  报文长度变化: {len(ba)} -> {len(bb)}")
    if changes == 0:
        print("本次捕获的可比较响应一致；未捕获的配置无法判断。")
    return 0


def _self_cmd(*extra: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    return [sys.executable, str(Path(__file__).resolve()), *extra]


def orchestrate(args) -> int:
    from diagnostic_runner import run
    return run(args, sys.modules[__name__])


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors="replace")
        sys.stderr.reconfigure(encoding='utf-8', errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="多品牌 2.4G 外设配置诊断与日志生成工具")
    ap.add_argument("--worker", default=None, help="内部子进程标志: inventory 或 interface_id")
    ap.add_argument("--out", default=None, help="输出目录")
    ap.add_argument("--all", action="store_true", help="扫描并探测所有厂商自定义 HID 接口")
    ap.add_argument("--vid", action="append", help="额外指定要探测的 VID (例如 0x1234)")
    ap.add_argument("--diff", nargs=2, metavar=("SNAP_A", "SNAP_B"), help="对比两次诊断包/文件夹的字节差异")
    ap.add_argument("--output", help="日志保存目录，默认 Windows 实际桌面")
    ap.add_argument("--inventory-only", action="store_true", help="仅枚举，不执行协议查询")
    ap.add_argument("--pause", action="store_true", help="结束后等待回车")
    ap.add_argument("--no-pause", action="store_true", help="完成后不等待按键直接退出")
    args = ap.parse_args()

    extra_vids = {int(v, 0) for v in (args.vid or [])}
    if args.worker is not None:
        out_dir = Path(args.out)
        if args.worker == "inventory":
            return worker_inventory(out_dir, args.all, extra_vids)
        if args.worker == 'system':
            return worker_system(out_dir)
        return worker_probe(out_dir, args.worker)

    if args.diff:
        return diff_runs(Path(args.diff[0]), Path(args.diff[1]))

    rc = 0
    try:
        rc = orchestrate(args)
    except Exception:
        traceback.print_exc()
        rc = 1

    if args.pause and not args.no_pause and args.worker is None:
        try:
            input("\n按回车键退出程序...")
        except Exception:
            pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
