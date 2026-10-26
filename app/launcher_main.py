# -*- coding: utf-8 -*-
"""launcher_main.py —— 独立可执行文件 (.exe) 统一入口

支持双击直接运行，也支持通过子命令参数启动各个子模块进程：
  * 直接双击运行（无参数）：自动拉起后台任务栏托盘与桌面挂件，并打开主工具界面
  * --tray      ：后台常驻任务栏电量图标与低电量通知监控
  * --ui        ：打开「我的设备电量」主工具界面
  * --widget    ：启动桌面固定电量小组件
  * --toast     ：弹出右下角 Windows 11 风格低电量预警通知横幅
  * --debugger  ：打开 HID 协议调试与手动标定工具
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

# 在 PyInstaller --windowed 无控制台模式下，sys.stdout / sys.stderr 为 None，
# 绑定到 os.devnull 防止任何 print / argparse / traceback 报错崩溃
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    except Exception:
        pass

# 确保 PyInstaller 打包时静态分析收录全部模块
import app
import battery
import bt_battery
import darkrim_proto
import desktop_widget
import hid_win
import installer
import my_devices
import tray_app
import tray_common as tc
import win_icon
import win_link
import win_shell
import win_toast


def main() -> int:
    args = sys.argv[1:]

    if "--selftest" in args:
        import tray_font
        assert any(tray_font.digit_mask('100', 16)), 'Bundled tray font failed'
        readings = battery.discover_and_read_all(include_bt=True)
        for r in readings:
            print(f"{r.title}: ok={r.ok} level={r.level}")
        return 0

    if "--tray" in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--tray"]
        return int(tray_app.main() or 0)

    if "--widget" in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--widget"]
        return int(desktop_widget.run_widget() or 0)

    if "--toast" in args:
        clean_args = [a for a in args if a != "--toast"]
        p = argparse.ArgumentParser(description="Windows 右下角置顶通知横幅")
        p.add_argument("--title", default="外设低电量预警")
        p.add_argument("--message", default="外设电量偏低，请及时充电。")
        p.add_argument("--warning", action="store_true")
        p.add_argument("--duration", type=float, default=7.0)
        parsed, _ = p.parse_known_args(clean_args)
        return int(win_toast._run_toast_window(
            parsed.title, parsed.message, parsed.warning, parsed.duration
        ) or 0)

    if "--debugger" in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--debugger"]
        app.main()
        return 0

    if "--install" in args or "--uninstall" in args:
        return int(installer.main() or 0)

    if "--ui" in args or "--once" in args:
        sys.argv = [sys.argv[0]] + [a for a in args if a != "--ui"]
        my_devices.main()
        return 0

    # 默认行为（用户直接双击 .exe）：
    # 1. 若后台任务栏托盘尚未运行，先在后台无感启动托盘与桌面固定挂件
    if not tc.is_tray_running():
        try:
            subprocess.Popen(
                tc.build_subcommand("--tray", "tray_app.py"),
                cwd=tc.BASE_DIR,
                env=tc.clean_subprocess_env(),
                creationflags=0x00000008,
            )
        except Exception:
            pass

    # 2. 若工具界面已经打开则激活到前台，否则直接打开工具界面
    hwnd = tc.ui_hwnd()
    if hwnd:
        tc.USER32.ShowWindow(hwnd, 9)
        tc.USER32.SetForegroundWindow(hwnd)
        return 0

    my_devices.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
