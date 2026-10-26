# -*- coding: utf-8 -*-
"""desktop_widget.py —— 固定在 Windows 桌面的无线外设电量小组件（纯 tkinter + ctypes）

特性：
  * 无边框暗色极简桌面挂件，实时展示多品牌无线外设电量、状态与电压。
  * 支持两种固定模式：
      1) 固定在桌面底层（默认）：钉在桌面上，不遮挡日常工作窗口，不在 Alt+Tab 出现。
      2) 始终置顶悬浮：浮在所有窗口最上方。
  * 支持鼠标拖拽移动位置并自动记忆坐标；支持「锁定位置」防误触；支持调节透明度。
  * 右键挂件可弹出快捷菜单（刷新 / 切换置顶 / 锁定位置 / 透明度 / 打开主面板 / 关闭挂件）。
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Optional

import battery as bt
import shared_readings as shared
import tray_common as tc
import ui_theme as ui

USER32 = tc.USER32
KERNEL32 = tc.KERNEL32
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

HWND_TOP = 0
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

WM_CLOSE = 0x0010

USER32.GetParent.restype = ctypes.c_void_p
USER32.GetParent.argtypes = [ctypes.c_void_p]
USER32.SetWindowTextW.restype = wintypes.BOOL
USER32.SetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
USER32.SetWindowPos.restype = wintypes.BOOL
USER32.SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_ssize_t,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
]
USER32.GetWindowLongW.restype = ctypes.c_long
USER32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.SetWindowLongW.restype = ctypes.c_long
USER32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]

MUTEX_WIDGET = "Global\\DeviceBatteryDesktopWidget_single"

BG = ui.PANEL
BORDER = ui.LINE
CARD_BG = ui.PANEL
FG = ui.FG
MUTED = ui.MUTED
FONT_TITLE = ("Microsoft YaHei UI", 9, "bold")
FONT_SUB = ("Microsoft YaHei UI", 8)
FONT_PCT = ("Segoe UI", 16)


def sync_theme_colors():
    global BG, BORDER, CARD_BG, FG, MUTED
    BG = CARD_BG = ui.PANEL
    BORDER, FG, MUTED = ui.LINE, ui.FG, ui.MUTED


def level_color(level: Optional[int]) -> str:
    if level is None:
        return MUTED
    if level >= 60:
        return "#78dfa4"
    if level >= 30:
        return "#f1ce81"
    return "#ff969d"


def _top_hwnd(widget) -> int:
    """获取 Tkinter 顶层窗口的真实 Win32 HWND。"""
    hwnd = int(widget.winfo_id())
    parent = USER32.GetParent(hwnd)
    return int(parent) if parent else hwnd


def _apply_win32_style(hwnd: int, mode: str) -> None:
    """设置 WS_EX_TOOLWINDOW（隐藏任务栏与 Alt+Tab 图标）并控制桌面层级。"""
    if not hwnd:
        return
    USER32.SetWindowTextW(hwnd, tc.WIDGET_TITLE)
    ex_style = USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    new_style = (ex_style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if new_style != ex_style:
        USER32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    if mode == "topmost":
        USER32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
    else:
        USER32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, flags)


def run_widget() -> int:
    import tkinter as tk

    # 单实例锁
    # A competing launch can hold the mutex before its Tk window is visible.
    # Do not gate the singleton check on FindWindow: that admits two widgets.
    ctypes.set_last_error(0)
    mutex = KERNEL32.CreateMutexW(None, False, MUTEX_WIDGET)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        KERNEL32.CloseHandle(mutex)
        return 0

    try:
        USER32.SetProcessDPIAware()
    except Exception:
        pass

    ui.prepare_process()
    cfg = tc.load_config()
    ui.select_theme(cfg.get('theme', 'light'))
    sync_theme_colors()
    from widget_size import fixed_size
    root = tk.Tk()
    root.title(tc.WIDGET_TITLE)
    root.overrideredirect(True)
    root.configure(bg=BORDER)

    alpha = max(50, min(100, int(cfg.get("widget_alpha", 92)))) / 100.0
    root.attributes("-alpha", alpha)
    if cfg.get("widget_mode") == "topmost":
        root.attributes("-topmost", True)

    # 定位初始坐标（默认屏幕右上角偏下）
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    initial_size = fixed_size(cfg, sw, sh)
    w_width = initial_size[0] if initial_size else round((260 if cfg.get('widget_compact') else 310) * ui.scale(root))
    x = int(cfg.get("widget_x", -1))
    y = int(cfg.get("widget_y", 60))
    if x < 0 or x > sw - 60:
        x = max(20, sw - w_width - 36)
    if y < 0 or y > sh - 60:
        y = 60
    initial_height = initial_size[1] if initial_size else 110
    x, y = min(x, max(0, sw-w_width)), min(y, max(0, sh-initial_height))
    root.geometry(f"{w_width}x{initial_height}+{x}+{y}")

    # 外层 1px 边框容器
    outer = tk.Frame(root, bg=BG, padx=12, pady=10)
    outer.pack(fill="both", expand=True, padx=1, pady=1)

    # 顶部标题栏（极简）
    header = tk.Frame(outer, bg=BG)
    header.pack(fill="x", pady=(0, 6))
    lbl_head = tk.Label(header, text="设备电量", bg=BG, fg=MUTED,
                        font=("Microsoft YaHei UI", 8, "bold"))
    lbl_head.pack(side="left")
    lbl_pin = tk.Label(header, text="", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8))
    lbl_pin.pack(side="right")

    # 设备列表容器
    viewport = tk.Frame(outer, bg=BG)
    viewport.pack(fill='both', expand=True)
    canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0, height=1, width=1)
    scrollbar = tk.Scrollbar(viewport, orient='vertical', command=canvas.yview,
                            bg=ui.RAISED, troughcolor=BG, activebackground=ui.LINE)
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    items_frame = tk.Frame(canvas, bg=BG)
    content_item = canvas.create_window(0, 0, anchor='nw', window=items_frame)
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(content_item, width=max(1, e.width)))
    items_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    def wheel(event):
        if items_frame.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'
    root.bind('<MouseWheel>', wheel)

    state = {
        "busy": False,
        "drag_x": 0,
        "drag_y": 0,
        "cfg": cfg,
        "hwnd": 0,
        "rows": {},
        "row_order": (),
        "compact": None,
    }
    q: "queue.Queue[list[bt.DiscoveredReading]]" = queue.Queue()

    def update_pin_badge():
        c = state["cfg"]
        mode_txt = "置顶" if c.get("widget_mode") == "topmost" else "桌面固定"
        lock_txt = " · 已锁定" if c.get("widget_locked") else ""
        lbl_pin.configure(text=f"{mode_txt}{lock_txt}")

    update_pin_badge()

    # 拖拽逻辑
    def on_drag_start(event):
        if state["cfg"].get("widget_locked"):
            return
        state["drag_x"] = event.x_root - root.winfo_x()
        state["drag_y"] = event.y_root - root.winfo_y()

    def on_drag_motion(event):
        if state["cfg"].get("widget_locked"):
            return
        nx = event.x_root - state["drag_x"]
        ny = event.y_root - state["drag_y"]
        root.geometry(f"+{nx}+{ny}")

    def on_drag_end(_event):
        if state["cfg"].get("widget_locked"):
            return
        c = tc.load_config()
        c["widget_x"] = root.winfo_x()
        c["widget_y"] = root.winfo_y()
        tc.save_config(c)
        state["cfg"] = c
        if state["hwnd"]:
            _apply_win32_style(state["hwnd"], c.get("widget_mode", "desktop"))

    def bind_drag(w):
        w.bind("<ButtonPress-1>", on_drag_start)
        w.bind("<B1-Motion>", on_drag_motion)
        w.bind("<ButtonRelease-1>", on_drag_end)
        w.bind("<Button-3>", on_context_menu)
        w.bind("<Double-Button-1>", lambda _e: open_main_panel())

    def configure_changed(widget, **options):
        changed = {name: value for name, value in options.items()
                   if str(widget.cget(name)) != str(value)}
        if changed:
            widget.configure(**changed)

    def create_row(compact):
        from tkinter import font as tkfont
        row = tk.Frame(items_frame, bg=CARD_BG, padx=10, pady=5 if compact else 7)
        top = tk.Frame(row, bg=CARD_BG)
        top.pack(fill="x")
        glyph = ui.DeviceGlyph(top, size=20 if compact else 26, bg=CARD_BG)
        glyph.pack(side="left", padx=(0, 8))
        left = tk.Frame(top, bg=CARD_BG)
        left.pack(side="left", fill="x", expand=True)
        title = tk.Label(left, bg=CARD_BG, fg=FG, font=FONT_TITLE, anchor="w",
                         wraplength=round((120 if compact else 150)*ui.scale(root)), justify="left")
        title.pack(anchor="w")
        left.bind('<Configure>', lambda e: title.configure(wraplength=max(40, e.width)))
        percent_font = ("Microsoft YaHei UI", 12, "bold") if compact else FONT_PCT
        metrics = tkfont.Font(root=root, font=percent_font)
        # Reserve enough room for 100% plus charging; digit-count changes must
        # not resize the name column or make the widget jump.
        percent_box = tk.Frame(top, bg=CARD_BG, width=metrics.measure("100%⚡")+4,
                               height=metrics.metrics("linespace")+4)
        percent_box.pack(side="right", padx=(6, 0), before=glyph)
        percent_box.pack_propagate(False)
        percent = tk.Label(percent_box, bg=CARD_BG, fg=MUTED, font=percent_font, anchor="e")
        percent.pack(fill="both", expand=True)
        sub = tk.Label(left, bg=CARD_BG, fg=MUTED, font=FONT_SUB, anchor="w")
        bar = tk.Frame(row, bg=ui.LINE, height=4)
        bar.pack_propagate(False)
        fill = tk.Frame(bar, bg=MUTED, height=4)
        if not compact:
            sub.pack(anchor="w")
            bar.pack(fill="x", pady=(6, 0))
        for widget in (row, top, glyph, left, title, percent_box, percent, sub, bar, fill):
            bind_drag(widget)
        return dict(frame=row, glyph=glyph, title=title, percent=percent,
                    sub=sub, bar=bar, fill=fill, signature=None)

    # Reuse controls across polls; only changed text/progress is repainted.
    def render_readings(readings: list[bt.DiscoveredReading]):
        state["last_readings"] = readings
        from device_visibility import visible_readings, selected_keys
        readings = visible_readings(readings, state['cfg'], 'widget')
        compact = bool(state["cfg"].get("widget_compact", False))
        rows = state["rows"]
        if compact != state["compact"]:
            for row in rows.values():
                row["frame"].destroy()
            rows.clear()
            state["compact"] = compact
            state["row_order"] = ()
        ordered = []
        occurrences = {}
        layout_changed = False
        for reading in readings or [None]:
            base = (getattr(reading, "key", None) or reading.title) if reading else None
            occurrence = occurrences.get(base, 0)
            occurrences[base] = occurrence + 1
            key = (base, occurrence)
            ordered.append(key)
            if key not in rows:
                rows[key] = create_row(compact)
                layout_changed = True
            row = rows[key]
            if reading is None:
                selection = selected_keys(state['cfg'], 'widget')
                title, percent, sub, color, value, kind = (
                    '未选择显示设备' if selection == set() else
                    '所选设备未连接' if selection is not None else '未检测到无线外设',
                    '', '在首页选择桌面挂件设备' if selection == set() else
                    '连接所选设备后自动显示' if selection is not None else
                    '连接 2.4G 或蓝牙设备后自动显示', MUTED, 0, 'mouse')
            else:
                title = reading.title.lstrip("🖱️⌨️🎧 ")
                if compact and len(title) > 15:
                    title = title[:14] + "…"
                kind = ui.device_kind(reading.title, reading.is_headset)
                from ui_connection import reading_color
                color = reading_color(reading.level, getattr(reading, 'key', ''))
                bolt = "⚡" if reading.ok and "充电" in (reading.status or "") else ""
                percent = f"{reading.level}%{bolt}" if reading.ok else "--"
                parts = []
                if reading.ok:
                    if reading.status and reading.status not in ("未知", "状态未知"):
                        parts.append(reading.status)
                    if reading.voltage_mv:
                        parts.append(f"{reading.voltage_mv}mV")
                else:
                    parts.append(reading.error[:18] if reading.error else "未连接")
                sub = " · ".join(parts) or "在线"
                value = max(0, min(100, reading.level or 0))
            signature = (title, percent, sub, color, value, kind)
            previous = row["signature"]
            if signature == previous:
                continue
            if previous is None or previous[0] != title:
                configure_changed(row["title"], text=title)
                layout_changed = True
            configure_changed(row["percent"], text=percent, fg=color)
            configure_changed(row["sub"], text=sub)
            if previous is None or previous[-1] != kind:
                row["glyph"].draw(kind)
            if previous is None or previous[3:5] != signature[3:5]:
                configure_changed(row["fill"], bg=color)
                if value:
                    row["fill"].place(relx=0, rely=0, relwidth=value/100.0, relheight=1.0)
                else:
                    row["fill"].place_forget()
            row["signature"] = signature
        for key in list(rows):
            if key not in ordered:
                rows.pop(key)["frame"].destroy()
                layout_changed = True
        if tuple(ordered) != state["row_order"]:
            for key in ordered:
                rows[key]["frame"].pack_forget()
                rows[key]["frame"].pack(fill="x", pady=2 if compact else 3)
            state["row_order"] = tuple(ordered)
            layout_changed = True
        # Ordinary battery/status updates never flush Tk layout, repack rows,
        # resize the window, or touch its stacking/opacity attributes.
        dimensions = (state['cfg'].get('widget_size_mode'), state['cfg'].get('widget_width'),
                      state['cfg'].get('widget_height'))
        if layout_changed or dimensions != state.get('dimensions'):
            state['dimensions'] = dimensions
            size = fixed_size(state['cfg'], sw, sh)
            cur_w = size[0] if size else min(sw, round((260 if compact else 310) * ui.scale(root)))
            # Set width first, allowing device names to wrap before measuring height.
            if root.winfo_width() != cur_w:
                root.geometry(f'{cur_w}x{root.winfo_height()}')
            if size and not scrollbar.winfo_manager():
                scrollbar.pack(side='right', fill='y', before=canvas)
            elif not size and scrollbar.winfo_manager():
                scrollbar.pack_forget()
            root.update_idletasks()
            content_height = items_frame.winfo_reqheight()
            canvas.configure(height=content_height)
            root.update_idletasks()
            req_h = size[1] if size else min(sh-60, max(58 if compact else 86, outer.winfo_reqheight() + 4))
            if (root.winfo_width(), root.winfo_height()) != (cur_w, req_h):
                root.geometry(f"{cur_w}x{req_h}")
            # A larger custom window should remain reachable near a screen edge.
            nx, ny = min(max(0, root.winfo_x()), max(0, sw-cur_w)), min(max(0, root.winfo_y()), max(0, sh-req_h))
            if (nx, ny) != (root.winfo_x(), root.winfo_y()):
                root.geometry(f'+{nx}+{ny}')

    def open_main_panel():
        hwnd = tc.ui_hwnd()
        if hwnd:
            USER32.ShowWindow(hwnd, 9)
            USER32.SetForegroundWindow(hwnd)
            return
        import subprocess
        try:
            subprocess.Popen(tc.build_subcommand("--ui", "my_devices.py"),
                             cwd=tc.BASE_DIR, env=tc.clean_subprocess_env(),
                             creationflags=0x00000008)
        except Exception:
            pass

    def toggle_mode(new_mode: str):
        c = tc.load_config()
        c["widget_mode"] = new_mode
        tc.save_config(c)
        state["cfg"] = c
        root.attributes("-topmost", new_mode == "topmost")
        if state["hwnd"]:
            _apply_win32_style(state["hwnd"], new_mode)
        update_pin_badge()

    def toggle_compact():
        c = tc.load_config()
        c["widget_compact"] = not bool(c.get("widget_compact", False))
        tc.save_config(c)
        state["cfg"] = c
        render_readings(state.get("last_readings") or [])

    def toggle_lock():
        c = tc.load_config()
        c["widget_locked"] = not bool(c.get("widget_locked", False))
        tc.save_config(c)
        state["cfg"] = c
        update_pin_badge()

    def set_alpha(val: int):
        c = tc.load_config()
        c["widget_alpha"] = val
        tc.save_config(c)
        state["cfg"] = c
        root.attributes("-alpha", val / 100.0)

    def close_widget():
        c = tc.load_config()
        c["desktop_widget"] = False
        tc.save_config(c)
        tc.post_to_tray(tc.MSG_RELOAD)
        root.destroy()

    # 右键快捷菜单
    menu = tk.Menu(root, tearoff=0, bg=ui.PANEL, fg=FG, activebackground=ui.GREEN,
                   activeforeground="#ffffff", font=("Microsoft YaHei UI", 9))

    def on_context_menu(event):
        menu.delete(0, "end")
        c = tc.load_config()
        state["cfg"] = c
        menu.add_command(label="立即刷新电量", command=refresh_now)
        menu.add_command(label="打开主界面（双击挂件）", command=open_main_panel)
        menu.add_command(label="测试低电量通知", command=lambda: tc.post_to_tray(tc.MSG_TEST_NOTIFY))
        menu.add_separator()
        compact_on = bool(c.get("widget_compact", False))
        menu.add_command(
            label=("✓ " if compact_on else "   ") + "紧凑胶囊模式（单行精简）",
            command=toggle_compact,
        )
        is_top = c.get("widget_mode") == "topmost"
        menu.add_command(
            label=("✓ " if not is_top else "   ") + "固定在桌面底层（不遮挡窗口）",
            command=lambda: toggle_mode("desktop"),
        )
        menu.add_command(
            label=("✓ " if is_top else "   ") + "始终置顶悬浮",
            command=lambda: toggle_mode("topmost"),
        )
        locked = bool(c.get("widget_locked"))
        menu.add_command(
            label=("✓ " if locked else "   ") + "锁定挂件位置（防误触）",
            command=toggle_lock,
        )
        menu.add_separator()
        for a_val in (100, 92, 80, 65):
            cur_a = int(c.get("widget_alpha", 92))
            menu.add_command(
                label=("✓ " if cur_a == a_val else "   ") + f"不透明度 {a_val}%",
                command=lambda v=a_val: set_alpha(v),
            )
        menu.add_separator()
        menu.add_command(label="关闭桌面挂件", command=close_widget)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    bind_drag(outer)
    bind_drag(header)
    bind_drag(lbl_head)
    bind_drag(lbl_pin)
    bind_drag(items_frame)


    def worker(force):
        try:
            res = shared.readings_from(shared.collect(force=force))
        except Exception:
            traceback.print_exc()
            res = state.get('last_readings', [])
        q.put(res)

    def refresh_now(force=True):
        if state["busy"]:
            return
        state["busy"] = True
        threading.Thread(target=worker, args=(force,), daemon=True).start()

    def init_win32():
        hwnd = _top_hwnd(root)
        state["hwnd"] = hwnd
        USER32.SetWindowTextW(int(root.winfo_id()), tc.WIDGET_TITLE)
        _apply_win32_style(hwnd, state["cfg"].get("widget_mode", "desktop"))
        render_readings(shared.readings_from(shared.load_snapshot()))
        refresh_now(force=False)

    started_ts = time.time()
    last_refresh = [time.time()]

    def pump():
        try:
            while True:
                readings = q.get_nowait()
                state["busy"] = False
                last_refresh[0] = time.time()
                snapshot = shared.load_snapshot()
                state['shared_revision'] = snapshot['revision']
                render_readings(shared.readings_from(snapshot) if snapshot['revision'] else readings)
        except queue.Empty:
            pass

        snapshot = shared.load_snapshot()
        if snapshot['revision'] and snapshot['revision'] != state.get('shared_revision'):
            state['shared_revision'] = snapshot['revision']
            render_readings(shared.readings_from(snapshot))

        # 若后台托盘与主界面均已退出（例如用户点击了「退出程序」），自动销毁桌面浮窗
        if time.time() - started_ts > 4.0:
            if not tc.is_tray_running() and not tc.ui_hwnd():
                try:
                    root.destroy()
                except Exception:
                    pass
                return

        # 同步配置文件变更（如主面板修改了锁定/置顶/紧凑模式/开关）
        c = tc.load_config()
        if not c.get("desktop_widget", True):
            try:
                root.destroy()
            except Exception:
                pass
            return
        if c != state["cfg"]:
            old = state["cfg"]
            state["cfg"] = c
            if c.get('theme', 'light') != old.get('theme', 'light'):
                ui.apply_theme(root, c.get('theme', 'light'))
                sync_theme_colors()
            if c.get("widget_alpha", 92) != old.get("widget_alpha", 92):
                root.attributes("-alpha", max(50, min(100, int(c.get("widget_alpha", 92)))) / 100.0)
            if c.get("widget_mode", "desktop") != old.get("widget_mode", "desktop"):
                root.attributes("-topmost", c.get("widget_mode") == "topmost")
                if state["hwnd"]:
                    _apply_win32_style(state["hwnd"], c.get("widget_mode", "desktop"))
            if any(c.get(key) != old.get(key) for key in ("widget_mode", "widget_locked")):
                update_pin_badge()
            if (bool(c.get("widget_compact", False)) != bool(old.get("widget_compact", False))
                    or c.get('widget_devices') != old.get('widget_devices')
                    or c.get('hidden_devices') != old.get('hidden_devices')
                    or c.get('custom_models') != old.get('custom_models')
                    or any(c.get(key) != old.get(key) for key in ('widget_size_mode', 'widget_width', 'widget_height'))):
                render_readings(state.get("last_readings") or [])

        interval = max(30, int(state["cfg"].get("interval", 60)))
        if time.time() - last_refresh[0] >= interval:
            refresh_now(force=False)

        root.after(800, pump)

    root.after(100, init_win32)
    root.after(800, pump)
    try:
        root.mainloop()
    finally:
        KERNEL32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    sys.exit(run_widget())
