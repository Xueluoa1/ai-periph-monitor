# -*- coding: utf-8 -*-
"""tray_app.py —— 托盘电量监视器（纯 ctypes，无第三方依赖）

界面约定
--------
* 通知区域两个图标：ATK 鼠标（纯数字）、DARKRIM 耳机（数字带深色底板）。
  图标颜色随电量变化：绿 ≥60 / 黄 30-59 / 红 <30。
* 右键图标 → 打开主页、显示与挂件开关、开机启动、退出。
* 左键图标 → 立刻刷新一次。
* 悬停显示 tooltip：设备名 + 电量 + 更新时间。

其它
----
* 开机自启用「启动」文件夹里的快捷方式（`电量托盘.lnk`，任务管理器可见）+ pythonw.exe。
* 低 CPU：SetTimer 驱动，空闲不轮询；读取在后台线程；默认 60 秒一轮。
* **不写日志文件**：只有 `--debug` 时才往控制台打印。
* 全 ctypes 调 shell32/user32，不依赖 pystray / Pillow。

启动：
    pythonw tray_app.py        # 静默驻留（开机自启用的就是这个）
    python  tray_app.py --debug   # 前台运行并打印过程信息（排查问题用）
    python  tray_app.py --status
    python  tray_app.py --quit
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
import shared_readings as shared
from ctypes import wintypes
from typing import Optional

import hid_win as hw
import battery as bt
import darkrim_proto as drp
import win_icon
import win_toast
import tray_common as tc

BASE_DIR = tc.BASE_DIR
APP_NAME = tc.APP_NAME
MUTEX_NAME = tc.MUTEX_NAME
WND_CLASS = tc.WND_CLASS

USER32 = tc.USER32
KERNEL32 = tc.KERNEL32
SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
MSG_TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH_DONE = WM_APP + 2

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x01, 0x02, 0x03

SW_RESTORE = 9

# --------------------------------------------------------------------------
# Win32 结构体与原型
# --------------------------------------------------------------------------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", ctypes.c_void_p),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", hw.GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", ctypes.c_void_p), ("message", wintypes.UINT),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


USER32.RegisterClassExW.restype = wintypes.WORD
USER32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
USER32.CreateWindowExW.restype = ctypes.c_void_p
USER32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
USER32.DestroyWindow.restype = wintypes.BOOL
USER32.DestroyWindow.argtypes = [ctypes.c_void_p]
USER32.SetTimer.restype = ctypes.c_size_t
USER32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT,
                            ctypes.c_void_p]
USER32.KillTimer.restype = wintypes.BOOL
USER32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
USER32.GetMessageW.restype = ctypes.c_int
USER32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                               wintypes.UINT, wintypes.UINT]
USER32.PostQuitMessage.restype = None
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.TranslateMessage.restype = wintypes.BOOL
USER32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
USER32.DispatchMessageW.restype = ctypes.c_ssize_t
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
USER32.CreatePopupMenu.restype = ctypes.c_void_p
USER32.CreatePopupMenu.argtypes = []
USER32.AppendMenuW.restype = wintypes.BOOL
USER32.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
USER32.TrackPopupMenu.restype = wintypes.BOOL
USER32.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_void_p]
USER32.DestroyMenu.restype = wintypes.BOOL
USER32.DestroyMenu.argtypes = [ctypes.c_void_p]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_UNCHECKED = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

IDM_AUTOSTART = 1001
IDM_DESKTOP_PIN = 1002
IDM_QUIT = 1003
IDM_HOME = 1004
IDM_TASKBAR = 1005
IDM_WIDGET_COMPACT = 1006
IDM_WIDGET_LOCK = 1007

MENU_TOGGLES = (
    (IDM_TASKBAR, 'taskbar_show', '任务栏显示', True),
    (IDM_DESKTOP_PIN, 'desktop_widget', '桌面挂件', True),
    (IDM_WIDGET_COMPACT, 'widget_compact', '桌面挂件紧凑模式', False),
    (IDM_WIDGET_LOCK, 'widget_locked', '锁定挂件位置', False),
)

SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]

KERNEL32.GetModuleHandleW.restype = ctypes.c_void_p
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                  wintypes.LPCWSTR]

# --------------------------------------------------------------------------
# 托盘程序（动态适配多品牌无线外设 + 任务栏常驻 + Win 系统通知电量预警）
# --------------------------------------------------------------------------
class TrayApp:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.cfg = tc.load_config()
        self.hwnd: Optional[int] = None
        self.wndproc_ref = None
        self.busy = False
        snapshot = shared.load_snapshot()
        self.readings: list[bt.DiscoveredReading] = shared.readings_from(snapshot)
        self.shared_revision = snapshot['revision']
        self.icon_retry = False
        self.active_uids: set[int] = set()
        # 记录每个设备的低电量预警阶段：0=未预警，1=已触发普通低电量预警，2=已触发 <=10% 紧急预警
        self.notified_stage: dict[str, int] = {}
        self.taskbar_created_msg = USER32.RegisterWindowMessageW("TaskbarCreated")
        self.msg_quit = USER32.RegisterWindowMessageW(tc.MSG_QUIT)
        self.msg_reload = USER32.RegisterWindowMessageW(tc.MSG_RELOAD)
        self.msg_test_notify = USER32.RegisterWindowMessageW(tc.MSG_TEST_NOTIFY)
        self.log("启动，配置 = " + json.dumps(self.cfg, ensure_ascii=False))

    # ---- 输出（默认不落盘）---------------------------------------------
    def log(self, msg: str) -> None:
        """只在 --debug 时输出到控制台。"""
        if self.debug:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)

    # ---- 图标 ----------------------------------------------------------
    @staticmethod
    def _color(level: Optional[int]) -> tuple[int, int, int]:
        if level is None:
            return (150, 155, 160)
        if level >= 60:
            return (93, 220, 138)
        if level >= 30:
            return (245, 196, 81)
        return (255, 107, 107)

    def _nid(self, uID: int):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = MSG_TRAY_CALLBACK
        return nid

    def _current_slots(self) -> list[tuple[int, str, Optional[int], str, bool, str]]:
        """返回 [(uID, title, level, status_or_err, headset, transport), ...]。"""
        from tray_pixels import transport_for_key
        from device_visibility import visible_readings, selected_keys
        if not self.cfg.get('taskbar_show', True):
            return []
        readings = visible_readings(self.readings, self.cfg, 'taskbar')
        if not self.readings and selected_keys(self.cfg, 'taskbar') is None:
            return [(1, "无线外设", None, "正在扫描或未检测到接收器", False, 'unknown')]
        if not hasattr(self, '_device_uids'):
            self._device_uids = {}
        slots = []
        for r in readings:
            if r.key not in self._device_uids:
                self._device_uids[r.key] = len(self._device_uids) + 2
            idx = self._device_uids[r.key]
            info = r.status if r.ok else (r.error or "未知")
            if r.ok and not getattr(r, 'stale', False) and getattr(r, 'updated_at', None):
                if '系统缓存' not in info:
                    info = f"{info} · {time.strftime('%m-%d %H:%M:%S', time.localtime(r.updated_at))} 更新"
            if r.ok and r.voltage_mv:
                info = f"{info} {r.voltage_mv}mV".strip()
            slots.append((idx, r.title, r.level, info, r.is_headset, transport_for_key(r.key)))
        return slots

    def _apply_icon(self, action: int, uID: int, title: str,
                    level: Optional[int], info: str, plate: bool, transport: str = 'unknown') -> bool:
        text = str(level) if level is not None else "--"
        nid = self._nid(uID)
        import tray_pixels
        kind = tray_pixels.kind_for_title(title, plate)
        state = "sleep" if "休眠" in info else "charging" if "充电" in info else "online"
        nid.hIcon = win_icon.make_hicon(text, self._color(level), plate, kind=kind, state=state, transport=transport)
        if not nid.hIcon:
            return False  # Never replace a working icon with an empty HICON.
        if level is not None:
            extra = f" · {info}" if info and info not in ("未知", "状态未知") else ""
            timestamp = '（系统上报时间未知）' if '系统缓存' in info else ''
            tip = f"{title}：{level}%{extra}{timestamp}"
        else:
            tip = f"{title}：{info or '未知'}"
        connection = tray_pixels.TRANSPORT_LABELS.get(transport, '连接方式待识别')
        nid.szTip = (f'外设电量 · {connection} · ' + tip)[:127]
        return bool(SHELL32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    def add_icons(self) -> None:
        self.active_uids.clear()
        self.icon_retry = False
        for uID, title, level, info, plate, transport in self._current_slots():
            ok = self._apply_icon(NIM_ADD, uID, title, level, info, plate, transport)
            if ok:
                self.active_uids.add(uID)
            else:
                self.icon_retry = True
            self.log(f"NIM_ADD #{uID} {title} -> {'ok' if ok else '失败'}")
        if self.cfg.get("taskbar_show", True):
            tc.promote_tray_icons_to_taskbar(True)

    def update_icons(self) -> None:
        slots = self._current_slots()
        new_uids = {slot[0] for slot in slots}
        added_any = False
        self.icon_retry = False
        # 移除多余的旧图标
        for old_uid in list(self.active_uids - new_uids):
            nid = self._nid(old_uid)
            nid.uFlags = 0
            SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.active_uids.discard(old_uid)
        # 新增或更新当前图标
        for uID, title, level, info, plate, transport in slots:
            if uID in self.active_uids:
                if not self._apply_icon(NIM_MODIFY, uID, title, level, info, plate, transport):
                    if not self._apply_icon(NIM_ADD, uID, title, level, info, plate, transport):
                        self.icon_retry = True
            else:
                if self._apply_icon(NIM_ADD, uID, title, level, info, plate, transport):
                    self.active_uids.add(uID)
                    added_any = True
                else:
                    self.icon_retry = True
        if added_any and self.cfg.get("taskbar_show", True):
            tc.promote_tray_icons_to_taskbar(True)

    def remove_icons(self) -> None:
        for uID in list(self.active_uids):
            nid = self._nid(uID)
            nid.uFlags = 0
            SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.active_uids.clear()

    # ---- 程序内低电量通知 ----
    def send_notification(self, title: str, message: str,
                          warning: bool = True) -> bool:
        """Use only the app banner; never emit a second Windows notification."""
        ok = win_toast.trigger_toast(title, message, warning=warning)
        self.log(f"发送程序通知 [{title}] -> {ok}")
        return ok

    def check_low_battery_alerts(self) -> None:
        """检查所有已读取设备是否低于设定阈值，触发防打扰分级系统通知。"""
        if not self.cfg.get("notify_enabled", True):
            return
        threshold = int(self.cfg.get("notify_threshold", 20))
        from device_visibility import managed_readings
        for r in managed_readings(self.readings, self.cfg):
            if not r.ok or r.level is None or getattr(r, 'stale', False):
                continue
            stage = self.notified_stage.get(r.key, 0)
            # 正在充电或电量回升超过阈值+3% 时重置预警状态
            if r.status in ("充电中", "已充满") or r.level > max(threshold + 3, 15):
                self.notified_stage[r.key] = 0
                continue
            if r.status == "休眠中" or "系统缓存" in r.status:
                continue

            v_str = f"（电压 {r.voltage_mv} mV）" if r.voltage_mv else ""
            if r.level <= 10 and stage < 2:
                self.notified_stage[r.key] = 2
                self.send_notification(
                    "外设电量紧急预警 (≤10%)",
                    f"【{r.title}】剩余电量仅 {r.level}%{v_str}，即将耗尽断连，请立即连接充电！",
                    warning=True,
                )
            elif r.level <= threshold and stage < 1:
                self.notified_stage[r.key] = 1
                self.send_notification(
                    f"外设低电量预警 (≤{threshold}%)",
                    f"【{r.title}】当前电量为 {r.level}%{v_str}，已低于设定预警线 {threshold}%，请及时充电。",
                    warning=True,
                )

    def test_notification(self) -> None:
        """手动触发一条程序通知。"""
        from device_visibility import managed_readings
        threshold = int(self.cfg.get("notify_threshold", 20))
        ok_list = [
            f"{r.title}：{r.level}%" + ('（系统缓存）' if '系统缓存' in r.status else '') + (f" ({r.voltage_mv}mV)" if r.voltage_mv else "")
            for r in managed_readings(self.readings, self.cfg) if r.ok
        ]
        if ok_list:
            body = "当前在线外设：" + " | ".join(ok_list) + f"\n当电量 ≤{threshold}% 时将自动弹出低电量程序提醒。"
        else:
            body = f"低电量预警已启用（预警阈值 ≤{threshold}%）。当外设电量过低时将通过此通知提醒您。"
        self.send_notification("无线外设电量监视器 · 预警通知测试", body, warning=True)

    # ---- 定时器 / 刷新 --------------------------------------------------
    def apply_timer(self) -> None:
        if not self.hwnd:
            return
        USER32.KillTimer(self.hwnd, 1)
        USER32.SetTimer(self.hwnd, 1, int(self.cfg["interval"]) * 1000, None)

    def apply_config(self) -> None:
        """同步界面里改过的设置。"""
        new = tc.load_config()
        changed_interval = new["interval"] != self.cfg["interval"]
        changed_taskbar = new.get("taskbar_show") != self.cfg.get("taskbar_show")
        changed_devices = new.get('taskbar_devices') != self.cfg.get('taskbar_devices')
        changed_hidden = new.get('hidden_devices') != self.cfg.get('hidden_devices')
        changed_notify = (
            new.get("notify_enabled") != self.cfg.get("notify_enabled")
            or new.get("notify_threshold") != self.cfg.get("notify_threshold")
        )
        changed_models = new.get("custom_models") != self.cfg.get("custom_models")
        self.cfg = new
        if changed_interval:
            self.log(f"刷新间隔 -> {new['interval']} 秒")
            self.apply_timer()
        if changed_taskbar or changed_devices or changed_hidden or changed_models:
            self.update_icons()
        if changed_taskbar and new.get('taskbar_show', True):
            tc.promote_tray_icons_to_taskbar(True)
        if changed_notify:
            # 用户修改了预警阈值或重新开启了通知，立刻按新阈值重评一次当前电量
            self.notified_stage.clear()
            self.check_low_battery_alerts()
        if changed_models:
            self.refresh_now()

        # 同步桌面挂件启停状态
        tc.sync_widget_state(self.cfg)

        actual = tc.is_autostart_enabled()
        if bool(self.cfg.get("autostart")) != actual:
            self.cfg["autostart"] = actual
            tc.save_config(self.cfg)
            self.log(f"开机自启实际为 {actual}，已同步到配置（未改动系统设置）")

    def refresh_now(self, force=True) -> None:
        if self.busy or not self.hwnd:
            return
        self.busy = True
        threading.Thread(target=self._worker, args=(self.hwnd, force), daemon=True).start()

    def _worker(self, hwnd: int, force=False) -> None:
        try:
            shared.collect(force=force)
        except Exception:
            self.log("读取异常:\n" + traceback.format_exc())
        USER32.PostMessageW(hwnd, MSG_REFRESH_DONE, 0, 0)

    def on_refresh_done(self) -> None:
        self.busy = False
        self.adopt_snapshot()
        summary = " | ".join(
            f"{r.title}={r.level}%({r.status or 'ok'})" if r.ok else f"{r.title}=失败({r.error})"
            for r in self.readings
        ) or "无受支持外设"
        self.log(f"刷新完成：{summary}")
        self.update_icons()
        self.check_low_battery_alerts()

    def adopt_snapshot(self):
        snapshot = shared.load_snapshot()
        if snapshot['revision'] and snapshot['revision'] != self.shared_revision:
            self.shared_revision = snapshot['revision']
            self.readings = shared.readings_from(snapshot)
            self.update_icons()
            self.check_low_battery_alerts()

    # ---- 打开工具界面 / 右键菜单 / 退出 ---------------------------------
    def open_ui(self, home=False) -> None:
        hwnd = tc.ui_hwnd()
        if hwnd:
            if home:
                tc.request_ui_home(hwnd)
            USER32.ShowWindow(hwnd, SW_RESTORE)
            USER32.SetForegroundWindow(hwnd)
            self.log("工具界面已在前台")
            return
        import subprocess
        try:
            subprocess.Popen(tc.build_subcommand("--ui", "my_devices.py"),
                             cwd=BASE_DIR, env=tc.clean_subprocess_env(),
                             creationflags=0x00000008)
            self.log("已打开工具界面")
        except Exception as e:
            self.log(f"打开界面失败：{e}")

    def show_context_menu(self) -> None:
        """Native checked switches; reload before writing to preserve other edits."""
        if not self.hwnd:
            return
        menu_cfg = tc.load_config()
        hmenu = USER32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            autostart_on = tc.is_autostart_enabled()
            USER32.AppendMenuW(hmenu, MF_STRING, IDM_HOME, '打开主页')
            USER32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            for command, key, label, default in MENU_TOGGLES:
                USER32.AppendMenuW(hmenu, MF_STRING | (MF_CHECKED if menu_cfg.get(key, default) else MF_UNCHECKED),
                                   command, label)
            USER32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)

            USER32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if autostart_on else MF_UNCHECKED),
                IDM_AUTOSTART,
                "开机启动",
            )
            USER32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            USER32.AppendMenuW(hmenu, MF_STRING, IDM_QUIT, "退出程序")

            pt = wintypes.POINT()
            USER32.GetCursorPos(ctypes.byref(pt))
            USER32.SetForegroundWindow(self.hwnd)
            cmd = int(USER32.TrackPopupMenu(
                hmenu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pt.x, pt.y, 0, self.hwnd, None
            ))
            USER32.PostMessageW(self.hwnd, WM_NULL, 0, 0)

            if cmd == IDM_HOME:
                self.open_ui(home=True)
            elif cmd == IDM_AUTOSTART:
                want = not autostart_on
                tc.set_autostart(want)
                latest = tc.load_config()
                latest['autostart'] = tc.is_autostart_enabled()
                tc.save_config(latest)
                self.apply_config()
                self.log(f"右键菜单切换：开机启动 -> {want}")
            elif cmd in {item[0] for item in MENU_TOGGLES}:
                _, key, label, default = next(item for item in MENU_TOGGLES if item[0] == cmd)
                latest = tc.load_config()
                want = not bool(menu_cfg.get(key, default))
                # Keep a recovery route when the user removes the menu's icons.
                if key == 'taskbar_show' and not want:
                    self.open_ui(home=True)
                latest[key] = want
                tc.save_config(latest)
                self.apply_config()
                self.log(f'右键菜单切换：{label} -> {want}')
            elif cmd == IDM_QUIT:
                self.quit()
        finally:
            USER32.DestroyMenu(hmenu)

    def quit(self) -> None:
        self.log("退出")
        self.remove_icons()
        tc.stop_widget_app()
        ui_h = tc.ui_hwnd()
        if ui_h:
            USER32.PostMessageW(ui_h, 0x0010, 0, 0)
        try:
            win_icon.destroy_all()
            win_icon.cleanup_files()
        except Exception:
            self.log("清理图标时出错（忽略）：\n" + traceback.format_exc())
        if self.hwnd:
            # DestroyWindow 会触发 WM_DESTROY -> PostQuitMessage，消息循环随之结束
            USER32.DestroyWindow(self.hwnd)

    # ---- 窗口过程 -------------------------------------------------------
    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg in (0x001A, 0x031A, 0x007E):  # Settings/theme/display changes.
            self.update_icons()
            return 0
        if msg == MSG_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self.show_context_menu()
            elif event == WM_LBUTTONDBLCLK:
                self.open_ui()                     # 双击：打开工具界面
            elif event == WM_LBUTTONUP:
                self.refresh_now()                 # 单击：立即刷新一次电量
            return 0
        if msg == MSG_REFRESH_DONE:
            self.on_refresh_done()
            return 0
        if msg == WM_TIMER:
            if wparam == 2:
                self.adopt_snapshot()
                if self.icon_retry:
                    self.update_icons()
                # Schedule from the last completed shared scan, not from the
                # unrelated UI/tray timer phases (which can otherwise skip a cycle).
                if not self.busy and shared.due():
                    self.refresh_now(force=False)
                return 0
            self.apply_config()                    # 顺手同步一下界面里改的设置
            self.refresh_now(force=False)
            return 0
        if msg == self.msg_quit:
            self.quit()
            return 0
        if msg == self.msg_reload:
            self.apply_config()
            return 0
        if msg == self.msg_test_notify:
            self.test_notification()
            return 0
        if msg == self.taskbar_created_msg:
            self.log("任务栏重建，重新添加图标")
            self.add_icons()
            return 0
        if msg == WM_DESTROY:
            # 重要：销毁窗口后必须让消息循环退出，否则进程会残留成"僵尸"
            USER32.PostQuitMessage(0)
            return 0
        return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)


    # ---- 启动 ----------------------------------------------------------
    def run(self) -> int:
        hinst = KERNEL32.GetModuleHandleW(None)
        self.wndproc_ref = WNDPROC(self.wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self.wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = WND_CLASS
        if not USER32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):
                self.log(f"RegisterClassExW 失败 err={err}")
                return 1

        hwnd = USER32.CreateWindowExW(0, WND_CLASS, APP_NAME, 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            self.log(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
            return 1
        self.hwnd = hwnd

        self.add_icons()
        self.apply_config()
        self.apply_timer()
        USER32.SetTimer(self.hwnd, 2, 1000, None)
        self.refresh_now(force=False)

        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))
        self.log("消息循环结束")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="托盘电量监视器")
    parser.add_argument("--debug", action="store_true", help="前台运行并输出日志")
    parser.add_argument("--status", action="store_true", help="打印当前状态")
    parser.add_argument("--quit", action="store_true", help="让正在运行的实例退出")
    args = parser.parse_args()

    if args.status:
        print("配置：", json.dumps(tc.load_config(), ensure_ascii=False))
        print("开机自启：", tc.is_autostart_enabled())
        print("启动命令：", tc.autostart_command())
        print("托盘是否在运行：", tc.is_tray_running())
        return 0
    if args.quit:
        ok = tc.post_to_tray(tc.MSG_QUIT)
        print("已通知托盘退出" if ok else "托盘没在运行")
        return 0

    # 单实例
    KERNEL32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:
        if tc.is_tray_running():
            # 真有一个正常实例在跑：让它重读配置
            tc.post_to_tray(tc.MSG_RELOAD)
            print("已经在运行了（托盘里两个数字图标就是它；右键图标打开界面）")
            return 0
        # 有人占着锁但已经没有窗口 —— 上一版留下的僵尸进程，清掉后继续启动
        tc.cleanup_zombie()

    tc.write_pid()

    try:
        USER32.SetProcessDPIAware()            # 图标不糊
    except Exception:
        pass

    app = TrayApp(debug=args.debug)
    try:
        return app.run()
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        tc.remove_pid()


if __name__ == "__main__":
    sys.exit(main())
