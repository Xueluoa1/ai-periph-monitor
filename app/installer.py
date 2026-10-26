# -*- coding: utf-8 -*-
"""installer.py —— Windows 本地一键安装 / 卸载管理器（纯标准库 + ctypes）

功能：
  1. 将程序部署到 Windows 标准用户程序目录：%LOCALAPPDATA%\\Programs\\DeviceBatteryViewer
  2. 生成专属高清图标 app_icon.ico
  3. 创建「桌面快捷方式」（无线外设电量监视器.lnk）與「开始菜单快捷方式」
  4. 配置「开机自启」（启动文件夹快捷方式，Windows 任务管理器可见可控）
  5. 注册到 Windows「设置 -> 应用 -> 已安装的应用」（支持从系统设置中直接卸载）
  6. 安装后自动启动托盘常驻、任务栏直接显示电量数字与桌面固定挂件
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import winreg

import tray_common as tc
import win_icon
import win_shell

APP_ID = "DeviceBatteryViewer"
DISPLAY_NAME = "2.4G 无线外设电量监视器 (ATK/ROG/怒喵多品牌版)"
DISPLAY_VERSION = "1.0.2"
PUBLISHER = "2.4G-Battery-Viewer"
SHORTCUT_NAME = "无线外设电量监视器.lnk"
UNINSTALL_REG_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"

SOURCE_ASSET_FILES = ("app_icon.ico", "app_icon.png", "THIRD_PARTY_NOTICES.txt")
SOURCE_ASSET_DIRS = ("brand_assets", "fonts")

OPTIONAL_FILES = [
    "protocols_user.json",
    "tray_config.json",
    "README.md",
    "PROMPT.md",
]


def source_install_files(source_dir: str) -> list[Path]:
    """Include every runtime module as the application gains new adapters."""
    source = Path(source_dir)
    modules = sorted(path for path in source.glob("*.py")
                     if not path.name.startswith(("verify_", "build_", "qa_", "test_")))
    assets = [source / name for name in SOURCE_ASSET_FILES]
    required = assets + [source / name for name in SOURCE_ASSET_DIRS]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(f"安装资源缺失：{path}")
    return modules + assets + [source / name for name in OPTIONAL_FILES
                              if (source / name).is_file()]


def copy_source_distribution(source_dir: str, destination_dir: str) -> list[str]:
    """Copy runtime code and its assets without touching Windows integration."""
    files = source_install_files(source_dir)
    copied = []
    for path in files:
        shutil.copy2(path, Path(destination_dir) / path.name)
        copied.append(path.name)
    for name in SOURCE_ASSET_DIRS:
        shutil.copytree(Path(source_dir) / name, Path(destination_dir) / name,
                        dirs_exist_ok=True)
    return copied


def install_dir() -> str:
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(local_appdata, "Programs", APP_ID)


def desktop_shortcut_path() -> str:
    return os.path.join(win_shell.desktop_dir(), SHORTCUT_NAME)


def start_menu_shortcut_path() -> str:
    return os.path.join(win_shell.programs_dir(), SHORTCUT_NAME)


def ensure_app_icon(target_dir: str) -> str:
    """在安装目录生成应用图标 app_icon.ico。"""
    os.makedirs(target_dir, exist_ok=True)
    ico_path = os.path.join(target_dir, "app_icon.ico")
    from ui_theme import asset_path
    source = str(asset_path("app_icon.ico"))
    if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(ico_path)):
        shutil.copy2(source, ico_path)
    return ico_path


def is_installed() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY, 0, winreg.KEY_READ):
            return True
    except OSError:
        return os.path.isfile(os.path.join(install_dir(), "my_devices.py"))


def install_to_windows(in_place: bool = False, start_now: bool = True) -> dict:
    """执行安装流程，返回安装结果详情字典。"""
    src_dir = tc.BASE_DIR
    dst_dir = src_dir if in_place else install_dir()
    os.makedirs(dst_dir, exist_ok=True)

    # 先停止可能正在运行的旧托盘与桌面挂件，并等待其完全释放单实例锁
    tc.post_to_tray(tc.MSG_QUIT)
    tc.stop_widget_app()
    import time
    deadline = time.time() + 3.0
    while time.time() < deadline and (tc.is_tray_running() or tc.is_widget_running()):
        time.sleep(0.1)
    time.sleep(0.15)

    copied = []
    if tc.is_frozen():
        exe_name = os.path.basename(sys.executable)
        dst_exe = os.path.join(dst_dir, exe_name)
        if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(os.path.abspath(dst_exe)):
            try:
                shutil.copy2(sys.executable, dst_exe)
                copied.append(exe_name)
            except OSError as exc:
                raise OSError(f"无法复制程序到安装目录 {dst_exe}，安装未完成") from exc
        for fname in OPTIONAL_FILES:
            sp = os.path.join(src_dir, fname)
            dp = os.path.join(dst_dir, fname)
            if os.path.isfile(sp) and os.path.normcase(os.path.abspath(sp)) != os.path.normcase(os.path.abspath(dp)):
                shutil.copy2(sp, dp)
        target_bin = dst_exe
        ui_args = "--ui"
        tray_args = "--tray"
        uninstall_cmd = f'"{dst_exe}" --uninstall'
        tray_launch_cmd = [dst_exe, "--tray"]
    else:
        if os.path.normcase(os.path.abspath(src_dir)) != os.path.normcase(os.path.abspath(dst_dir)):
            copied.extend(copy_source_distribution(src_dir, dst_dir))
        pyw = tc.pythonw_path()
        ui_script = os.path.join(dst_dir, "my_devices.py")
        tray_script = os.path.join(dst_dir, "tray_app.py")
        installer_script = os.path.join(dst_dir, "installer.py")
        target_bin = pyw
        ui_args = f'"{ui_script}"'
        tray_args = f'"{tray_script}"'
        uninstall_cmd = f'"{pyw}" "{installer_script}" --uninstall --gui'
        tray_launch_cmd = [pyw, tray_script]

    ico_path = ensure_app_icon(dst_dir)

    # 1. 桌面快捷方式
    desk_lnk = desktop_shortcut_path()
    desk_ok = win_shell.create_shortcut(
        desk_lnk, target_bin, arguments=ui_args, workdir=dst_dir,
        description=DISPLAY_NAME, icon=ico_path,
    )

    # 2. 开始菜单快捷方式
    menu_lnk = start_menu_shortcut_path()
    menu_ok = win_shell.create_shortcut(
        menu_lnk, target_bin, arguments=ui_args, workdir=dst_dir,
        description=DISPLAY_NAME, icon=ico_path,
    )

    # 3. 开机自启快捷方式
    boot_lnk = tc.startup_lnk()
    boot_ok = win_shell.create_shortcut(
        boot_lnk, target_bin, arguments=tray_args, workdir=dst_dir,
        description="无线外设电量托盘与任务栏监视器", icon=ico_path,
    )
    tc._set_startup_approved(True)

    # 4. 写入 Windows 已安装应用注册表（控制面板/系统设置可卸载）
    reg_ok = False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
            winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, ico_path)
            winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, DISPLAY_VERSION)
            winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, PUBLISHER)
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, dst_dir)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, uninstall_cmd)
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        reg_ok = True
    except OSError:
        pass

    # 5. 启动安装目录中的托盘与桌面挂件
    tray_started = False
    if start_now:
        try:
            subprocess.Popen(tray_launch_cmd, cwd=dst_dir,
                             env=tc.clean_subprocess_env(),
                             creationflags=0x00000008)
            tray_started = tc.wait_for_tray(5.0)
            tc.promote_tray_icons_to_taskbar(True)
        except Exception:
            pass

    return {
        "install_dir": dst_dir,
        "desktop_shortcut": desk_lnk if desk_ok else "",
        "start_menu_shortcut": menu_lnk if menu_ok else "",
        "autostart_shortcut": boot_lnk if boot_ok else "",
        "registry_registered": reg_ok,
        "tray_started": tray_started,
    }


def uninstall_from_windows() -> dict:
    """从 Windows 卸载快捷方式、开机自启与注册表项。"""
    tc.post_to_tray(tc.MSG_QUIT)
    tc.stop_widget_app()
    tc.set_autostart(False)

    desk_removed = win_shell.remove_shortcut(desktop_shortcut_path())
    menu_removed = win_shell.remove_shortcut(start_menu_shortcut_path())

    reg_removed = False
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REG_KEY)
        reg_removed = True
    except OSError:
        pass

    return {
        "desktop_removed": desk_removed,
        "start_menu_removed": menu_removed,
        "registry_removed": reg_removed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows 安装/卸载工具")
    parser.add_argument("--install", action="store_true", help="一键安装到 Windows")
    parser.add_argument("--in-place", action="store_true", help="使用当前目录原地注册安装")
    parser.add_argument("--uninstall", action="store_true", help="从 Windows 卸载")
    parser.add_argument("--gui", action="store_true", help="弹出图形提示框")
    parser.add_argument("--status", action="store_true", help="查看安装状态")
    args = parser.parse_args()

    if args.status:
        print("是否已安装到 Windows：", "是" if is_installed() else "否")
        print("标准安装目录：", install_dir())
        print("桌面快捷方式：", desktop_shortcut_path(),
              "(已存在)" if os.path.exists(desktop_shortcut_path()) else "(未创建)")
        print("开始菜单快捷方式：", start_menu_shortcut_path(),
              "(已存在)" if os.path.exists(start_menu_shortcut_path()) else "(未创建)")
        return 0

    if args.uninstall:
        res = uninstall_from_windows()
        msg = "已从 Windows 卸载桌面快捷方式、开始菜单项、开机自启与系统应用注册项。"
        print(msg, res)
        if args.gui:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("卸载完成", msg)
            r.destroy()
        return 0

    # 默认执行安装
    res = install_to_windows(in_place=args.in_place, start_now=True)
    summary = (
        f"✅ 已成功安装到 Windows！\n\n"
        f"• 安装位置：{res['install_dir']}\n"
        f"• 桌面快捷方式：{'已创建' if res['desktop_shortcut'] else '失败'}\n"
        f"• 开始菜单快捷方式：{'已创建' if res['start_menu_shortcut'] else '失败'}\n"
        f"• 开机自启（任务管理器可见）：{'已启用' if res['autostart_shortcut'] else '失败'}\n"
        f"• 任务栏电量图标与桌面固定挂件：{'已启动' if res['tray_started'] else '请手动启动'}"
    )
    print(summary)
    if args.gui:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("安装成功", summary)
        r.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
