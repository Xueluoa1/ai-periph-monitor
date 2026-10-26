# -*- coding: utf-8 -*-
"""Program-owned notification banner with one sound per displayed banner."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Optional


def play_notification_sound(warning: bool = True) -> None:
    """播放 Windows 系统原生通知提示音。"""
    try:
        import winsound
        alias = "SystemExclamation" if warning else "SystemNotification"
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if warning else winsound.MB_ICONASTERISK)
        except Exception:
            pass


def trigger_toast(title: str, message: str, warning: bool = True,
                  duration_sec: float = 7.0) -> bool:
    """在独立无控制台子进程中弹出 Windows 11 风格右下角置顶通知横幅并播放提示音。"""

    extra = [
        "--title", title,
        "--message", message,
        "--duration", str(duration_sec),
    ]
    if warning:
        extra.append("--warning")

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
        cmd = [sys.executable, "--toast"] + extra
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            cand = exe[:-10] + "pythonw.exe"
            if os.path.exists(cand):
                exe = cand
        cmd = [exe, os.path.abspath(__file__)] + extra

    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        for k in list(env.keys()):
            ku = k.upper()
            if (
                ku.startswith("_PYI_")
                or ku.startswith("_MEI")
                or ku in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONPATH", "PYTHONHOME")
            ):
                env.pop(k, None)

    try:
        subprocess.Popen(cmd, cwd=base_dir, env=env, creationflags=0x00000008)
        return True
    except Exception:
        return False


def _run_toast_window(title: str, message: str, warning: bool = True,
                      duration_sec: float = 7.0) -> int:
    import tkinter as tk
    import ui_theme as ui

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    play_notification_sound(warning=warning)

    ui.prepare_process()
    import tray_common as tc
    ui.select_theme(tc.load_config().get('theme', 'light'))
    root = tk.Tk()
    root.title("DeviceBatteryToastBanner")
    root.overrideredirect(True)

    bg_main = ui.PANEL
    border_col = ui.LINE
    accent_col = ui.RED if warning else ui.ACCENT
    fg_main = ui.FG
    fg_muted = ui.MUTED
    btn_bg = ui.RAISED

    root.configure(bg=border_col)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.97)

    width = round(420 * ui.scale(root))
    outer = tk.Frame(root, bg=bg_main, padx=16, pady=12)
    outer.pack(fill="both", expand=True, padx=1, pady=1)

    # 顶部彩色强调条
    accent_bar = tk.Frame(outer, bg=accent_col, height=3)
    accent_bar.pack(fill="x", pady=(0, 8))

    # 顶栏：应用标识 + 关闭按钮
    top_row = tk.Frame(outer, bg=bg_main)
    top_row.pack(fill="x")
    badge_txt = "无线外设电量监视器"
    lbl_app = tk.Label(top_row, text=badge_txt, bg=bg_main, fg=fg_muted,
                       font=("Microsoft YaHei UI", 8, "bold"))
    lbl_app.pack(side="left")

    def close_now(_event=None):
        try:
            root.destroy()
        except Exception:
            pass

    lbl_close = tk.Label(top_row, text="✕", bg=bg_main, fg=fg_muted,
                         font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2", padx=10, pady=7)
    lbl_close.pack(side="right")
    lbl_close.bind("<Button-1>", close_now)

    # 标题
    lbl_title = tk.Label(outer, text=title, bg=bg_main, fg=fg_main,
                         font=("Microsoft YaHei UI", 12, "bold"),
                         anchor="w", justify="left", wraplength=width - 36)
    lbl_title.pack(fill="x", pady=(6, 4))

    # 正文
    lbl_msg = tk.Label(outer, text=message, bg=bg_main, fg=ui.FG,
                       font=("Microsoft YaHei UI", 9),
                       anchor="w", justify="left", wraplength=width - 36)
    lbl_msg.pack(fill="x", pady=(0, 10))

    # 底部操作栏
    bot_row = tk.Frame(outer, bg=bg_main)
    bot_row.pack(fill="x")
    lbl_time = tk.Label(bot_row, text=f"{time.strftime('%H:%M:%S')} · 自动消隐",
                        bg=bg_main, fg=fg_muted, font=("Microsoft YaHei UI", 8))
    lbl_time.pack(side="left")

    def open_ui_and_close(_event=None):
        try:
            import tray_common as tc
            hwnd = tc.ui_hwnd()
            if hwnd:
                tc.USER32.ShowWindow(hwnd, 9)
                tc.USER32.SetForegroundWindow(hwnd)
            else:
                subprocess.Popen(
                    tc.build_subcommand("--ui", "my_devices.py"),
                    cwd=tc.BASE_DIR,
                    env=tc.clean_subprocess_env(),
                    creationflags=0x00000008,
                )
        except Exception:
            pass
        close_now()

    btn_ok = tk.Label(bot_row, text="我知道了", bg=btn_bg, fg=fg_main,
                      font=("Microsoft YaHei UI", 8, "bold"),
                      padx=12, pady=9, cursor="hand2")
    btn_ok.pack(side="right")
    btn_ok.bind("<Button-1>", close_now)

    btn_open = tk.Label(bot_row, text="查看设备", bg=ui.ACCENT, fg=ui.ACCENT_FG,
                        font=("Microsoft YaHei UI", 8, "bold"),
                        padx=12, pady=9, cursor="hand2")
    btn_open.pack(side="right", padx=(0, 6))
    btn_open.bind("<Button-1>", open_ui_and_close)

    root.update_idletasks()
    height = max(130, outer.winfo_reqheight() + 4)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()

    # 放置在右下角任务栏上方
    target_x = max(16, sw - width - 20)
    target_y = max(16, sh - height - 64)
    root.geometry(f"{width}x{height}+{target_x}+{target_y}")

    # 设置 WS_EX_TOOLWINDOW | WS_EX_TOPMOST 且不抢夺当前活动窗口焦点
    try:
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010

        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        hwnd = int(root.winfo_id())
        parent = user32.GetParent(hwnd)
        real_hwnd = int(parent) if parent else hwnd
        ex = user32.GetWindowLongW(real_hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(real_hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        user32.SetWindowPos(real_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass

    root.after(int(max(2.0, duration_sec) * 1000), close_now)
    root.mainloop()
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Windows 右下角置顶通知横幅")
    p.add_argument("--title", default="外设低电量预警")
    p.add_argument("--message", default="外设电量偏低，请及时充电。")
    p.add_argument("--warning", action="store_true")
    p.add_argument("--duration", type=float, default=7.0)
    args = p.parse_args()
    sys.exit(_run_toast_window(args.title, args.message, args.warning, args.duration))
