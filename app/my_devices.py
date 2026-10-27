# -*- coding: utf-8 -*-
"""我的设备电量 —— 多品牌无线外设的原生电量面板

用法：
    python my_devices.py            打开界面（默认自动刷新）
    python my_devices.py --once     命令行读一次就退出

设备页按设备生成卡片，支持自适应列数、滚动和独立设置页面。
底层复用 battery.py / darkrim_proto.py 里已经验证过的协议。
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Optional

import hid_win as hw
import battery as bt
import shared_readings as shared
import darkrim_proto as drp
import tray_common as tc
import win_link
from hid_win import HidInterface


# ==========================================================================
# 读取层
# ==========================================================================
@dataclass
class Reading:
    title: str
    subtitle: str
    level: Optional[int] = None
    status: str = ""
    detail: str = ""
    error: str = ""
    extra: str = ""
    key: str = ""
    stale: bool = False
    level_label: str = ""

    @property
    def ok(self) -> bool:
        return self.level is not None


def read_all(force=False) -> list[Reading]:
    """自动发现并读取所有已连接的受支持多品牌外设（ATK、ROG、怒喵、罗技、雷蛇、迈从等）。"""
    return display_readings(shared.readings_from(shared.collect(force=force)))


def display_readings(discovered) -> list[Reading]:
    if not discovered:
        return [
            Reading(
                title="未检测到已连接的无线外设",
                subtitle="支持 ATK / VXE / VGN · ASUS ROG · 怒喵 Angry Miao · 罗技 · 雷蛇 · 迈从 · 兰族 · 万灵 · 雷柏 等",
                error="请插入 2.4G 接收器并唤醒外设，或点击下方「协议调试器」探测新设备",
            )
        ]
    out: list[Reading] = []
    for d in discovered:
        out.append(Reading(
            title=d.title,
            subtitle=d.subtitle,
            level=d.level,
            status=d.status,
            detail=d.detail,
            error=d.error,
            extra=d.extra,
            key=d.key,
            stale=d.stale,
            level_label=d.level_label,
        ))
    return out


# ==========================================================================
# 命令行模式
# ==========================================================================
def cli_once() -> None:
    for r in read_all(force=True):
        if r.ok:
            st = f" [{r.status}]" if r.status and r.status not in ("未知", "状态未知") else ""
            print(f"{r.title} ({r.subtitle}): {r.level}%{st}   {r.detail}")
            if r.extra:
                print(f"    {r.extra}")
        elif r.level_label:
            print(f"{r.title}: 电量档位 {r.level_label} · {r.detail}")
        else:
            print(f"{r.title} ({r.subtitle}): 读取失败 —— {r.error}")


# ==========================================================================
# 界面
# ==========================================================================
BG = "#1b1c1f"
PANEL = "#232529"
PANEL2 = "#2b2e33"
FG = "#e8e8ea"
MUTED = "#9aa0a6"
FONT = ("Microsoft YaHei UI", 10)

LEVEL_STYLE = [
    (60, "#5ddc8a", "电量充足"),
    (30, "#f5c451", "电量中等"),
    (0,  "#ff6b6b", "电量偏低"),
]


def level_color(level: Optional[int]) -> str:
    if level is None:
        return MUTED
    for threshold, color, _ in LEVEL_STYLE:
        if level >= threshold:
            return color
    return LEVEL_STYLE[-1][1]


from ui_cards import DeviceCard
from ui_device_list import DeviceList
import ui_theme as ui
from device_visibility import selection_caption, managed_readings
from ui_device_manager import DeviceManager
from ui_device_selection import open_device_selection
BG, PANEL, PANEL2, FG, MUTED = ui.BG, ui.PANEL, ui.RAISED, ui.FG, ui.MUTED


def build_ui():
    import tkinter as tk
    from tkinter import ttk

    try:
        tc.USER32.SetProcessDPIAware()
    except Exception:
        pass

    ui.prepare_process()
    cfg = tc.load_config()
    ui.select_theme(cfg.get('theme', 'light'))
    root = tk.Tk()
    root.title(tc.UI_TITLE)
    root.configure(bg=ui.BG)
    factor = ui.scale(root)
    width = min(round(960 * factor), root.winfo_screenwidth() - 80)
    height = min(round(620 * factor), root.winfo_screenheight() - 100)
    root.geometry(f"{width}x{height}")
    root.minsize(round(640 * factor), min(round(540 * factor), height))

    style = ui.configure(root)

    rail = tk.Frame(root, bg=ui.PANEL, width=round(76*factor),
                    highlightbackground=ui.LINE, highlightthickness=1)
    rail.pack(side='left', fill='y')
    rail.pack_propagate(False)
    mark = tk.Canvas(rail, width=48, height=64, bg=ui.PANEL, highlightthickness=0)
    mark.pack(pady=(10,18))
    mark.create_rectangle(14,16,34,45, outline=ui.ACCENT, width=2)
    mark.create_rectangle(20,12,28,16, fill=ui.ACCENT, outline='')
    mark.create_polygon(26,21,18,32,24,32,22,40,31,28,25,28,fill=ui.ACCENT)
    from ui_mouse_pages import NavigationButton, MouseSettingsPage, DeviceTestPage
    devices_nav = NavigationButton(rail, text='设备', icon='devices', active=True)
    devices_nav.pack(fill='x', pady=(0,6))
    mouse_nav = NavigationButton(rail, text='外设设置', icon='mouse')
    mouse_nav.pack(fill='x', pady=(0,6))
    test_nav = NavigationButton(rail, text='设备测试', icon='test')
    test_nav.pack(fill='x', pady=(0,6))
    settings_button = NavigationButton(rail, text='设置', icon='settings')
    settings_button.pack(fill='x')
    workspace = ttk.Frame(root)
    workspace.pack(side='left', fill='both', expand=True)
    content = ttk.Frame(workspace)
    content.pack(side='left', fill='both', expand=True)

    state = {"busy": False, "auto": True, "interval": 60, "after_id": None,
             "starting": False, "started_at": 0.0}
    # Tkinter 不是线程安全的：后台线程只往队列里放结果，主线程统一取出来处理
    results: "queue.Queue[tuple]" = queue.Queue()

    config_snapshot = dict(cfg)
    state["interval"] = int(cfg["interval"])

    def save_ui_config():
        """Only persist edited UI fields; keep concurrent cached readings/models."""
        latest_cfg = tc.load_config()
        changed = {key: value for key, value in cfg.items() if value != config_snapshot.get(key)}
        latest_cfg.update(changed)
        tc.save_config(latest_cfg)
        cfg.update(latest_cfg)
        config_snapshot.clear()
        config_snapshot.update(cfg)

    # Header and feedback stay outside the scrolling device list.
    head = ttk.Frame(content, padding=(24, 22, 24, 16))
    head.pack(fill="x")
    titlebox = ttk.Frame(head)
    titlebox.pack(side="left", fill="x", expand=True)
    ttk.Label(titlebox, text=tc.UI_TITLE, style="H1.TLabel").pack(anchor="w")
    summary = ttk.Label(titlebox, text="正在发现无线外设…", style="Muted.TLabel")
    summary.pack(anchor="w", pady=(5, 0))
    btn = ttk.Button(head, text="立即刷新", style="Accent.TButton")
    btn.pack(side="right")
    v_auto = tk.BooleanVar(value=True)
    v_interval = tk.StringVar(value=dict((s, l) for s, l in tc.INTERVAL_CHOICES).get(state["interval"], "1 分钟"))

    def update_summary():
        count = sum(bool(r.key) for r in state.get("last_readings", []))
        mode = f"每 {v_interval.get()}刷新" if state["auto"] else "自动刷新已关闭"
        summary.configure(text=f"{count} 台设备 · {mode}" if count else mode)

    def on_auto():
        state["auto"] = v_auto.get()
        _reschedule()
        update_summary()

    footer = ttk.Frame(content, padding=(24, 8, 24, 16))
    footer.pack(side="bottom", fill="x")
    foot = ttk.Label(footer, text="正在读取…", style="Muted.TLabel")
    foot.pack(side="left")
    footer_actions = ttk.Frame(footer)
    footer_actions.pack(side="right")
    statusbar = ttk.Frame(content, padding=(24, 3))
    statusbar.pack(side="bottom", fill="x")
    lab_tray = ttk.Label(statusbar, text="", style="Muted.TLabel", wraplength=500)
    lab_tray.pack(side="left",anchor="w")
    quick = ttk.Frame(content, padding=(24, 8, 24, 12))
    quick.pack(side="bottom", fill="x")

    device_list = DeviceList(content,on_model_change=lambda:refresh())
    device_list.pack(fill="both",expand=True,padx=24)

    def sync_cards(readings: list[Reading]) -> None:
        state['raw_readings'] = list(readings)
        readings = managed_readings(readings, tc.load_config())
        readings = readings or [Reading("暂无显示的设备", "点击“管理设备”恢复隐藏项，或连接设备后刷新。")]
        state["last_readings"] = readings
        device_list.set_readings(readings)
        update_summary()

    def manage_saved():
        sync_cards(state.get('raw_readings', []))
        refresh()

    ttk.Button(head, text='管理设备', command=lambda: DeviceManager(
        root, state.get('raw_readings', []), manage_saved)).pack(side='right', padx=(0, 8))

    sync_cards([Reading("正在扫描无线外设…", "正在匹配已连接的接收器与蓝牙设备")])

    # Only one page is visible. Device cards and quick controls stay in content.
    settings_window = ttk.Frame(workspace)
    mouse_page = MouseSettingsPage(workspace)
    test_page = DeviceTestPage(workspace)
    pages = {'devices':content, 'mouse':mouse_page, 'test':test_page, 'settings':settings_window}
    navs = {'devices':devices_nav, 'mouse':mouse_nav, 'test':test_nav, 'settings':settings_button}
    current_page = ['devices']
    settings_open = [False]
    def show_page(name):
        previous = pages[current_page[0]]
        if name != current_page[0]:
            callback = getattr(previous, 'on_hide', None)
            if callback: callback()
        for key,page in pages.items():
            page.pack_forget()
            navs[key].select(key == name)
        current_page[0] = name
        settings_open[0] = name == 'settings'
        pages[name].pack(fill='both', expand=True)
        callback = getattr(pages[name], 'on_show', None)
        if callback: callback()
    def close_settings():
        show_page('devices')
        devices_nav.focus_set()
    def open_settings():
        show_page('settings')
    root.bind("<Escape>", lambda e: close_settings() if current_page[0] not in ('devices','test') else None)
    devices_nav.configure(command=close_settings)
    settings_button.configure(command=open_settings)
    mouse_nav.configure(command=lambda:show_page('mouse'))
    test_nav.configure(command=lambda:show_page('test'))
    settings_head = ttk.Frame(settings_window, padding=(24, 22, 24, 16))
    settings_head.pack(fill="x")
    ttk.Label(settings_head, text="设置", style="H1.TLabel").pack(anchor="w")
    ttk.Label(settings_head, text="更改后自动生效", style="Muted.TLabel").pack(anchor="w", pady=(5, 0))
    settings_bottom = ttk.Frame(settings_window, padding=(24, 12))
    settings_bottom.pack(side="bottom", fill="x")
    ttk.Button(settings_bottom, text="返回设备", style="Accent.TButton", command=close_settings).pack(side="right")
    settings_feedback = ttk.Label(settings_bottom, text="", style="Muted.TLabel", wraplength=480)
    settings_feedback.pack(side="left", fill="x", expand=True)
    settings_scroll = ui.ScrollFrame(settings_window, content_gap=20)
    settings_scroll.pack(fill="both", expand=True, padx=24)
    setbar = settings_scroll.body

    def refresh_tray_state():
        if state.get("starting"):
            return                              # 正在启动，别覆盖提示文字
        if state.get("notice") and time.time() < state.get("notice_until", 0):
            lab_tray.configure(text=state["notice"])   # 临时提示优先显示
            btn_tray.configure(state="normal",
                               text="退出托盘与挂件" if tc.is_tray_running()
                               else "启动托盘与挂件")
            return
        state["notice"] = ""
        running = tc.is_tray_running()
        w_run = tc.is_widget_running()
        btn_tray.configure(state="normal",
                           text="退出托盘与挂件" if running else "启动托盘与挂件")
        status_parts = []
        status_parts.append(("任务栏：" + selection_caption(cfg, 'taskbar') if cfg.get("taskbar_show", True)
                             else "任务栏：已关闭") if running else "任务栏：后台未运行")
        status_parts.append("桌面挂件：已固定" if w_run else "桌面挂件：已关闭")
        lab_tray.configure(text=" · ".join(status_parts))

    def notify(text: str, seconds: float = 8.0) -> None:
        """在状态标签上显示一条临时提示。"""
        state["notice"] = text
        state["notice_until"] = time.time() + seconds
        settings_feedback.configure(text=text)
        refresh_tray_state()

    def on_interval(_event=None):
        sec = dict((label, sec) for sec, label in tc.INTERVAL_CHOICES)[v_interval.get()]
        cfg["interval"] = sec
        save_ui_config()
        state["interval"] = sec
        tc.post_to_tray(tc.MSG_RELOAD)      # 让托盘立刻换间隔
        _reschedule()
        refresh()

    def on_boot():
        want = bool(v_boot.get())
        ok = tc.set_autostart(want)
        cfg["autostart"] = want
        save_ui_config()
        tc.post_to_tray(tc.MSG_RELOAD)
        if want and not ok:
            notify("开机自启设置失败：无法创建启动文件夹快捷方式")
        elif want:
            notify("已加入开机自启（任务管理器 →「启动」标签里能看到 电量托盘）", 10)
        else:
            notify("已取消开机自启")

    def on_taskbar_show():
        want = bool(v_taskbar.get())
        cfg["taskbar_show"] = want
        save_ui_config()
        tc.post_to_tray(tc.MSG_RELOAD)
        if want:
            if not tc.is_tray_running() and not state['starting']:
                on_tray_toggle()
            notify('已开启任务栏电量：' + selection_caption(cfg, 'taskbar'), 8)
        else:
            notify("已关闭任务栏电量图标，后台监测继续运行", 6)

    def on_widget_toggle():
        want = bool(v_widget.get())
        cfg["desktop_widget"] = want
        save_ui_config()
        tc.sync_widget_state(cfg)
        tc.post_to_tray(tc.MSG_RELOAD)
        notify("已开启桌面固定挂件（右键挂件可调整透明度/置顶/锁定）" if want else "已关闭桌面挂件", 8)

    def on_widget_lock():
        want = bool(v_wlock.get())
        cfg["widget_locked"] = want
        save_ui_config()
        notify("已锁定桌面挂件位置（防误触）" if want else "已解锁桌面挂件位置（可左键拖拽移动）", 6)

    def on_notify_toggle():
        want = bool(v_notify.get())
        cfg["notify_enabled"] = want
        save_ui_config()
        tc.post_to_tray(tc.MSG_RELOAD)
        notify("已开启程序低电量提醒" if want else "已关闭程序低电量提醒", 6)

    def on_threshold(_event=None):
        val = dict((label, val) for val, label in tc.THRESHOLD_CHOICES).get(v_thresh.get(), 20)
        cfg["notify_threshold"] = val
        save_ui_config()
        tc.post_to_tray(tc.MSG_RELOAD)
        notify(f"低电量预警阈值已设为 ≤{val}%（当 ≤10% 时将触发二次紧急预警）", 8)

    def on_test_notify():
        def work():
            import win_toast
            if tc.is_tray_running():
                ok = tc.post_to_tray(tc.MSG_TEST_NOTIFY)
            else:
                tc.start_tray_app(wait_timeout=3.0)
                time.sleep(0.3)
                ok = tc.post_to_tray(tc.MSG_TEST_NOTIFY)
                if not ok:
                    threshold = int(cfg.get("notify_threshold", 20))
                    last_r = [r for r in state.get("last_readings", []) if r.ok]
                    if last_r:
                        devs = " | ".join(f"{r.title}：{r.level}%" + ('（系统缓存）' if '系统缓存' in r.status else '') for r in last_r)
                        body = f"当前在线外设：{devs}\n当电量 ≤{threshold}% 时将自动弹出低电量程序提醒。"
                    else:
                        body = f"低电量预警已启用（阈值 ≤{threshold}%）。"
                    ok = win_toast.trigger_toast("无线外设电量监视器 · 预警通知测试", body, warning=True)
            results.put(("test_notify", ok))
        threading.Thread(target=work, daemon=True).start()

    def on_install_windows():
        btn_install.configure(state="disabled", text="正在安装…")

        def work():
            try:
                import installer
                res = installer.install_to_windows(in_place=False, start_now=True)
                results.put(("install", res))
            except Exception as e:
                traceback.print_exc()
                results.put(("install", {"error": str(e)}))

        threading.Thread(target=work, daemon=True).start()

    def tray_done(ok: bool):
        state["starting"] = False
        refresh_tray_state()
        if not ok:
            notify("启动失败：可点击「一键安装到 Windows」再试", 10)

    def on_links():
        btn_link.configure(state="disabled", text="正在创建…")

        def work():
            try:
                r = tc.install_desktop_links()
            except Exception as e:
                traceback.print_exc()
                r = {"folder": "", "links": {}, "errors": [str(e)]}
            results.put(("links", r))

        threading.Thread(target=work, daemon=True).start()

    def links_done(r: dict):
        import tkinter.messagebox as mb
        btn_link.configure(state="normal", text="重建桌面链接")
        bits = []
        kind = r.get("folder") or ""
        if kind:
            bits.append(f"文件夹→{win_link.KIND_LABEL.get(kind, kind)}")
        for name, k in (r.get("links") or {}).items():
            bits.append(f"{name}→{win_link.KIND_LABEL.get(k, k)}")
        if bits:
            notify("桌面链接已更新：" + "；".join(bits), 10)
        else:
            notify("桌面链接创建失败", 10)
        if r.get("errors"):
            mb.showwarning("桌面链接", "\n".join(r["errors"]))

    def on_tray_toggle():
        if tc.is_tray_running():
            tc.post_to_tray(tc.MSG_QUIT)
            tc.stop_widget_app()
            for delay in (600, 1500, 3000):
                root.after(delay, refresh_tray_state)
            return

        state["starting"] = True
        state["started_at"] = time.time()
        btn_tray.configure(state="disabled", text="正在启动…")
        lab_tray.configure(text="正在启动托盘与桌面挂件…")

        def work():
            try:
                ok = tc.start_tray_app()
                tc.sync_widget_state(cfg)
            except Exception:
                traceback.print_exc()
                ok = False
            results.put(("tray", ok))

        threading.Thread(target=work, daemon=True).start()

    v_boot = tk.BooleanVar(value=tc.is_autostart_enabled())
    v_taskbar = tk.BooleanVar(value=bool(cfg.get("taskbar_show", True)))
    v_widget = tk.BooleanVar(value=bool(cfg.get("desktop_widget", True)))
    v_wcompact = tk.BooleanVar(value=bool(cfg.get("widget_compact", False)))
    v_wlock = tk.BooleanVar(value=bool(cfg.get("widget_locked", False)))
    v_notify = tk.BooleanVar(value=bool(cfg.get("notify_enabled", True)))
    v_thresh = tk.StringVar(value=dict(tc.THRESHOLD_CHOICES).get(int(cfg.get("notify_threshold", 20)), "20%"))

    def on_widget_compact():
        cfg["widget_compact"] = bool(v_wcompact.get())
        save_ui_config()
        notify("已切换为紧凑挂件" if v_wcompact.get() else "已切换为标准挂件", 6)

    def on_open_debugger():
        import subprocess
        try:
            subprocess.Popen(tc.build_subcommand("--debugger", "app.py"),
                             cwd=tc.BASE_DIR, env=tc.clean_subprocess_env(),
                             creationflags=0x00000008)
            notify("已打开协议调试与手动标定工具", 6)
        except Exception as e:
            notify(f"打开失败：{e}", 6)

    def group(label):
        ttk.Label(setbar, text=label, style="Group.TLabel").pack(anchor="w", pady=(18, 6))

    def setting(label, hint=""):
        row = ttk.Frame(setbar, padding=(0, 9))
        row.pack(fill="x")
        text = ttk.Frame(row)
        text.pack(side="left", fill="x", expand=True)
        ttk.Label(text, text=label).pack(anchor="w")
        if hint:
            hint_label = ttk.Label(text, text=hint, style="Muted.TLabel", wraplength=170, justify='left')
            hint_label.pack(anchor="w", pady=(3, 0))
            text.bind('<Configure>', lambda e: hint_label.configure(wraplength=max(90,e.width)))
        ttk.Separator(setbar).pack(fill="x")
        return row

    def setting_switch(label, variable, command, hint=""):
        ui.Switch(setting(label, hint), variable, command, bg=ui.BG).pack(side="right", padx=(12, 0))

    def choice(label, variable, values, command, width=12):
        combo = ttk.Combobox(setting(label), textvariable=variable, values=values, state="readonly", width=width)
        combo.pack(side="right", padx=(10, 0))
        combo.bind("<<ComboboxSelected>>", command)
        return combo

    group("外观")
    theme_row = setting("界面主题", "立即切换，重启后保留")
    theme_controls = ttk.Frame(theme_row)
    theme_controls.pack(side='right', padx=(12, 0))
    v_theme = tk.StringVar(value=ui.CURRENT_THEME)
    def on_theme():
        cfg['theme'] = v_theme.get()
        save_ui_config()
        ui.apply_theme(root, cfg['theme'])
        tc.post_to_tray(tc.MSG_RELOAD)
        notify('已切换为暗色主题' if cfg['theme'] == 'dark' else '已切换为浅色主题')
    for value, label in (('light', '浅色'), ('dark', '暗色')):
        ttk.Radiobutton(theme_controls, text=label, value=value, variable=v_theme,
                        style='Filter.TRadiobutton', command=on_theme).pack(side='left', padx=(4, 0))

    group("桌面挂件")
    setting_switch("显示桌面挂件", v_widget, on_widget_toggle)
    setting_switch("紧凑模式", v_wcompact, on_widget_compact)
    from ui_widget_size import WidgetSizeSettings
    def on_widget_size(values):
        cfg.update(values)
        save_ui_config()
        notify('挂件尺寸已保存' + ('，开启挂件后生效' if not v_widget.get() else ''))
    WidgetSizeSettings(setbar, cfg, on_widget_size).pack(fill='x')
    ttk.Separator(setbar).pack(fill='x')
    setting_switch("锁定位置", v_wlock, on_widget_lock)
    mode_labels = {"desktop": "固定在桌面底层", "topmost": "始终置顶悬浮"}
    v_mode = tk.StringVar(value=mode_labels.get(cfg.get("widget_mode"), mode_labels["desktop"]))
    def on_mode(_event=None):
        cfg["widget_mode"] = {v:k for k,v in mode_labels.items()}[v_mode.get()]
        save_ui_config()
        notify("挂件显示位置已更新")
    choice("显示位置", v_mode, list(mode_labels.values()), on_mode, 16)
    v_alpha = tk.StringVar(value=f'{int(cfg.get("widget_alpha", 92))}%')
    def on_alpha(_event=None):
        cfg["widget_alpha"] = int(v_alpha.get().rstrip("%"))
        save_ui_config()
        notify("挂件透明度已更新")
    choice("不透明度", v_alpha, [f"{v}%" for v in (100, 92, 80, 65)], on_alpha)
    group("常规")
    setting_switch("自动刷新", v_auto, on_auto, "按设定间隔更新主界面电量")
    choice("刷新间隔", v_interval, [label for _, label in tc.INTERVAL_CHOICES], on_interval)
    setting_switch("开机启动", v_boot, on_boot, "登录 Windows 后启动电量监视器")
    setting_switch("显示到任务栏", v_taskbar, on_taskbar_show, "关闭后隐藏图标，后台监测继续")
    group("低电量提醒")
    setting_switch("启用提醒", v_notify, on_notify_toggle)
    choice("提醒阈值", v_thresh, [label for _, label in tc.THRESHOLD_CHOICES], on_threshold)
    ttk.Button(setting("程序通知"), text="测试通知", command=on_test_notify).pack(side="right")
    group("高级与维护")
    ttk.Button(setting("协议调试器", "保留原有 HID 读取与手动标定工具"), text="打开", command=on_open_debugger).pack(side="right")
    btn_tray = ttk.Button(setting("后台进程"), text="退出托盘与挂件", command=on_tray_toggle)
    btn_tray.pack(side="right")
    btn_install = ttk.Button(setting("安装与快捷方式"), text="一键安装到 Windows", command=on_install_windows)
    btn_install.pack(side="right")
    btn_link = ttk.Button(setbar, text="重建桌面链接", command=on_links)
    if not tc.is_frozen():
        btn_link.pack(anchor="e", pady=10)

    selectors = []
    def choose_devices(surface):
        def save_selection(value, names):
            cfg[surface + '_devices'] = value
            cfg['device_names'] = {**(tc.load_config().get('device_names') or {}), **names}
            save_ui_config()
            tc.post_to_tray(tc.MSG_RELOAD)
            for key, button in selectors:
                label = '任务栏设备' if key == 'taskbar' else '挂件设备'
                button.configure(text=f'{label} · {selection_caption(cfg, key)}  ›')
            notify(('任务栏' if surface == 'taskbar' else '桌面挂件') + '显示设备已保存')
        open_device_selection(root, surface, state.get('last_readings', []), tc.load_config(), save_selection)

    for surface, label in [('taskbar', '任务栏设备'), ('widget', '挂件设备')]:
        button = ttk.Button(quick, text=f'{label} · {selection_caption(cfg, surface)}  ›',
                            command=lambda key=surface: choose_devices(key))
        selectors.append((surface, button))

    quick_cards = []
    def quick_switch(label, hint, variable, callback):
        card = ui.Surface(quick, padding=10)
        quick_cards.append(card)
        control = ui.Switch(card.body, variable, callback)
        control.pack(side="right", padx=(6, 0))
        text = tk.Frame(card.body, bg=ui.PANEL)
        text.pack(side="left", fill="both", expand=True)
        title = ttk.Label(text, text=label, style="CardTitle.TLabel", wraplength=160)
        title.pack(anchor="w")
        caption = ttk.Label(text, text=hint, style="CardSub.TLabel", wraplength=160)
        caption.pack(anchor="w", pady=(4, 0))
        text.bind('<Configure>', lambda e: [w.configure(wraplength=max(70, e.width)) for w in (title, caption)])
        return caption

    quick_switch('显示到任务栏', '独立显示设备电量', v_taskbar, on_taskbar_show)
    quick_switch("桌面挂件", "在桌面查看电量", v_widget, on_widget_toggle)
    threshold_caption = quick_switch("低电量提醒", f"电量 ≤{cfg['notify_threshold']}% 时提醒", v_notify, on_notify_toggle)
    v_thresh.trace_add("write", lambda *args: threshold_caption.configure(text=f"电量 ≤{v_thresh.get().split('%')[0]}% 时提醒"))
    quick_switch("开机启动", "登录 Windows 后运行", v_boot, on_boot)
    quick_columns = [None]
    def arrange_quick(event):
        columns = 4 if event.width >= 760 * factor else 2
        if quick_columns[0] == columns:
            return
        quick_columns[0] = columns
        for col in range(4):
            quick.columnconfigure(col, weight=int(col < columns), uniform='quick' if col < columns else '')
        for index, (_key, button) in enumerate(selectors):
            button.grid(row=0, column=index * (columns // 2), columnspan=columns // 2,
                        sticky='ew', padx=(0, 10 if index == 0 else 0), pady=(0, 10))
        for index, card in enumerate(quick_cards):
            row, col = divmod(index, columns)
            card.grid(row=row + 1, column=col, sticky='nsew',
                      padx=(0, 10 if col < columns - 1 else 0), pady=(0, 8 if columns == 2 and row == 0 else 0))
    quick.bind('<Configure>', arrange_quick)
    ttk.Button(footer_actions, text="协议调试器", style="Quiet.TButton", command=on_open_debugger).pack(side="left")
    def exit_program():
        tc.post_to_tray(tc.MSG_QUIT)
        tc.stop_widget_app()
        root.destroy()
    ttk.Button(footer_actions, text="退出程序", style="Quiet.TButton", command=exit_program).pack(side="left")
    refresh_tray_state()
    # Source launches and --ui shortcuts must also start the battery tray.
    # Run after construction and use the existing worker to keep Tk responsive.
    def ensure_background():
        if not tc.is_tray_running():
            on_tray_toggle()
        else:
            threading.Thread(target=lambda: tc.sync_widget_state(cfg), daemon=True).start()
    root.after(150, ensure_background)

    # ---- 刷新逻辑 ----
    def apply(readings: list[Reading]):
        sync_cards(readings)
        readings = [r for r in state['last_readings'] if r.key]
        ok = sum(1 for r in readings if r.ok)
        retained = sum(1 for r in readings if r.ok and r.stale)
        total = len(readings)
        foot.configure(text=f"更新于 {time.strftime('%H:%M:%S')}   "
                            f"显示电量 {ok}/{total} 个设备"
                            + (f" · {retained} 个保留上次电量" if retained else "")
                            + ("" if state["auto"] else "   （自动刷新已关闭）"))
        state["busy"] = False
        btn.configure(state="normal", text="立即刷新")

    def worker(force):
        try:
            readings = read_all(force=force)
        except Exception as e:
            traceback.print_exc()
            readings = [Reading("读取异常", "", error=str(e))]
        results.put(("readings", readings))

    def refresh(force=True):
        if state["busy"]:
            return
        state["busy"] = True
        btn.configure(state="disabled", text="正在刷新…")
        foot.configure(text="正在扫描并读取无线外设电量…")
        threading.Thread(target=worker, args=(force,), daemon=True).start()


    def _reschedule():
        if state["after_id"]:
            root.after_cancel(state["after_id"])
            state["after_id"] = None
        if state["auto"]:
            state["after_id"] = root.after(state["interval"] * 1000, _tick)

    def _tick():
        refresh(force=False)
        _reschedule()

    btn.configure(command=refresh)

    root.after(300, lambda: refresh(force=False))
    _reschedule()

    # 主线程统一处理后台线程的结果 + 跟随托盘状态
    def pump():
        try:
            while True:
                kind, payload = results.get_nowait()
                if kind == "readings":
                    snapshot = shared.load_snapshot()
                    state['shared_revision'] = snapshot['revision']
                    apply(display_readings(shared.readings_from(snapshot)) if snapshot['revision'] else payload)
                elif kind == "tray":
                    tray_done(payload)
                elif kind == "links":
                    links_done(payload)
                elif kind == "test_notify":
                    notify("已发送程序通知（请查看屏幕右下角通知卡片）"
                           if payload else "发送通知失败（请先启动托盘）", 8)
                elif kind == "install":
                    import tkinter.messagebox as mb
                    btn_install.configure(state="normal", text="一键安装到 Windows")
                    if payload.get("error"):
                        notify(f"安装失败：{payload['error']}", 10)
                    else:
                        v_boot.set(True)
                        notify("已成功安装到 Windows（含桌面快捷方式、开始菜单、开机自启与任务栏常驻）", 12)
                        mb.showinfo(
                            "安装完成",
                            f"已成功安装到 Windows！\n\n"
                            f"• 安装目录：{payload.get('install_dir')}\n"
                            f"• 桌面快捷方式：已创建\n"
                            f"• 开始菜单快捷方式：已创建\n"
                            f"• 任务栏电量图标与桌面固定挂件：已自动开启",
                        )
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
            state["busy"] = False
            state["starting"] = False
            btn.configure(state="normal", text="立即刷新")


        # 兜底：启动流程万一卡住，20 秒后放开按钮，别让它永久禁用
        if state["starting"] and time.time() - state["started_at"] > 20:
            state["starting"] = False
            refresh_tray_state()

        # Native tray switches update the same config while this window is open.
        latest = tc.load_config()
        for key, variable in (('taskbar_show', v_taskbar), ('desktop_widget', v_widget),
                              ('widget_compact', v_wcompact), ('widget_locked', v_wlock),
                              ('autostart', v_boot)):
            value = bool(latest.get(key, tc.DEFAULT_CONFIG[key]))
            cfg[key] = value
            config_snapshot[key] = value
            if variable.get() != value:
                variable.set(value)
        if tc.consume_ui_home():
            close_settings()

        snapshot = shared.load_snapshot()
        if not state['busy'] and snapshot['revision'] and snapshot['revision'] != state.get('shared_revision'):
            state['shared_revision'] = snapshot['revision']
            apply(display_readings(shared.readings_from(snapshot)))
        refresh_tray_state()        # FindWindowW 很便宜，顺带同步托盘状态
        root.after(1000, pump)

    root.after(1000, pump)
    # 托盘右键打开时保证窗口到前台
    def raise_me():
        try:
            root.lift()
            root.focus_force()
        except Exception:
            pass
    root.after(250, raise_me)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="我的设备电量")
    parser.add_argument("--once", action="store_true", help="命令行读一次就退出")
    parser.add_argument("--install-links", action="store_true",
                        help="在桌面重建软连接（项目文件夹 + 启动脚本）")
    parser.add_argument("--autostart", choices=["on", "off", "status"],
                        help="开机自启：on 打开 / off 关闭 / status 查看")
    args = parser.parse_args()

    if args.install_links:
        r = tc.install_desktop_links()
        print("桌面链接：")
        print(f"  项目文件夹 {tc.DESKTOP_LINK_NAME} -> "
              f"{win_link.KIND_LABEL.get(r['folder'], r['folder'] or '创建失败')}")
        for name, kind in r["links"].items():
            print(f"  {name} -> {win_link.KIND_LABEL.get(kind, kind)}")
        for msg in r["errors"]:
            print("  注意：", msg)
        return

    if args.autostart:
        if args.autostart == "status":
            st = tc.autostart_status()
            print("开机自启：", "已启用" if st["enabled"] else "未启用")
            print("  方式：", st["method"])
            print("  快捷方式：", st["shortcut"],
                  "(存在)" if st["shortcut_exists"] else "(不存在)")
            print("  任务管理器状态：", st["taskmanager_state"])
            print("  目标：", st["target"])
            print("  旧注册表项：", "还在" if st["legacy_registry"] else "已清理")
        else:
            ok = tc.set_autostart(args.autostart == "on")
            print("已" + ("加入" if args.autostart == "on" else "取消") + "开机自启",
                  "" if ok else "（失败）")
        return

    if args.once:
        cli_once()
        return
    try:
        root = build_ui()
    except Exception:
        print("无法启动图形界面，改用命令行模式：")
        traceback.print_exc()
        cli_once()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
