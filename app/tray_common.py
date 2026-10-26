# -*- coding: utf-8 -*-
"""tray_common.py —— 托盘程序与界面共用的配置 / 开机自启 / 进程间通信

托盘程序（tray_app.py）和简洁界面（my_devices.py）是两个进程，
共用这里的配置文件与窗口消息，界面里改设置后能立刻通知托盘生效。
"""

from __future__ import annotations

import ctypes
from copy import deepcopy
import json
import os
import sys
import tempfile
import time
from ctypes import wintypes
from typing import Optional

import win_link
import win_shell

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


BASE_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if is_frozen()
    else os.path.dirname(os.path.abspath(__file__))
)


def _app_data_dir() -> str:
    """将运行期 PID 与配置文件存放在系统隐藏的 AppData 目录，
    避免把 .exe 放在桌面直接双击运行时在桌面生成 tray_pid.txt / tray_config.json。
    """
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    d = os.path.join(local_appdata, "Programs", "DeviceBatteryViewer")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


DATA_DIR = _app_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "tray_config.json")
PID_FILE = os.path.join(DATA_DIR, "tray_pid.txt")

APP_NAME = "DeviceBatteryTray"
WND_CLASS = "DeviceBatteryTrayWnd"
MUTEX_NAME = "Global\\DeviceBatteryTray_single_instance"

# 界面标题（托盘用它来找到已打开的窗口）
UI_TITLE = "我的设备电量"
WIDGET_TITLE = "DeviceBatteryDesktopWidget"

DEFAULT_CONFIG = {
    "theme": "light",          # 主界面、挂件及程序通知的外观主题
    "interval": 60,            # 托盘轮询间隔（秒）
    "autostart": True,         # 开机自启
    "taskbar_show": True,      # 显示任务栏设备电量图标；关闭时移除这些图标
    "taskbar_devices": None,   # None 自动显示全部；[] 不显示；列表为选定设备 key
    "widget_devices": None,    # 与任务栏独立的设备选择
    "device_names": {},        # 已选择设备的名称，断连后仍可调整选择
    "hidden_devices": [],      # 全局隐藏：设备页、任务栏、挂件和电量提醒
    "desktop_widget": True,    # 开启桌面固定电量挂件（桌面小组件）
    "widget_x": -1,            # 桌面挂件 X 坐标（-1 表示屏幕右上角默认位置）
    "widget_y": 60,            # 桌面挂件 Y 坐标
    "widget_mode": "desktop",  # "desktop"=固定在桌面底层(Win+D不最小化), "topmost"=始终置顶
    "widget_locked": False,    # 锁定桌面挂件位置（防误触）
    "widget_compact": False,   # 桌面挂件紧凑胶囊模式（True=窄幅单行，False=完整卡片）
    "widget_alpha": 92,        # 桌面挂件透明度百分比 (60~100)
    "widget_size_mode": "auto", # auto 随内容；fixed 使用像素宽高
    "widget_width": 340,
    "widget_height": 240,
    "notify_enabled": True,    # 开启 Windows 系统通知低电量预警
    "notify_threshold": 20,    # 低电量预警阈值 (%)
    "custom_models": {},       # 用户手动指定的设备型号映射 {key: "完整型号名称"}
    "last_readings": {},       # 外设休眠保活缓存 {key: {"level": 93, "mv": 4085, "time": "13:05", "extra": {...}}}
}

INTERVAL_CHOICES = [(30, "30 秒"), (60, "1 分钟"), (120, "2 分钟"),
                    (300, "5 分钟"), (600, "10 分钟"), (1800, "30 分钟")]

THRESHOLD_CHOICES = [(10, "10%"), (15, "15%"), (20, "20%"), (30, "30%"),
                     (50, "50%"), (95, "95%(实测)")]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOTIFY_ICON_SETTINGS_KEY = r"Control Panel\NotifyIconSettings"


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

USER32.FindWindowW.restype = ctypes.c_void_p
USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                ctypes.c_size_t, ctypes.c_ssize_t]
USER32.RegisterWindowMessageW.restype = wintypes.UINT
USER32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def load_config() -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_CONFIG:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    clean = deepcopy({k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG})
    temporary = None
    try:
        # Readers in the UI, tray and widget must see a complete old or new
        # document, never the empty/partial file from an in-place write.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=os.path.dirname(CONFIG_PATH),
                                         prefix="tray_config_", suffix=".tmp",
                                         delete=False) as f:
            temporary = f.name
            json.dump(clean, f, ensure_ascii=False, indent=2)
        # A Windows reader can briefly prevent replacement while its handle
        # is open. Leave the existing settings intact if all retries fail.
        for attempt in range(10):
            try:
                os.replace(temporary, CONFIG_PATH)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.02)
    except OSError:
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


# --------------------------------------------------------------------------
# 开机自启
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 开机自启：启动文件夹里的快捷方式（.lnk），不再用注册表
# --------------------------------------------------------------------------
def pythonw_path() -> str:
    if is_frozen():
        return sys.executable
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def build_subcommand(mode_flag: str, script_name: str,
                     extra_args: Optional[list[str]] = None) -> list[str]:
    """构建子进程启动命令行：打包为 .exe 时使用 [exe, mode_flag, ...]，源码模式使用 [pythonw, script, ...]。"""
    extra = list(extra_args or [])
    if is_frozen():
        return [sys.executable, mode_flag] + extra
    return [pythonw_path(), os.path.join(BASE_DIR, script_name)] + extra


def clean_subprocess_env() -> dict:
    """清理 PyInstaller 单文件引导器注入的临时环境变量（_PYI_* / _MEI* / TCL_LIBRARY 等），
    确保衍生出的后台子进程（--tray / --widget / --ui / --toast）拥有独立运行时，
    不会因父进程退出清理临时目录而报 'Failed to start embedded python interpreter!'。
    """
    env = os.environ.copy()
    if is_frozen():
        for k in list(env.keys()):
            ku = k.upper()
            if (
                ku.startswith("_PYI_")
                or ku.startswith("_MEI")
                or ku in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONPATH", "PYTHONHOME")
            ):
                env.pop(k, None)
    return env


def tray_script() -> str:
    return os.path.join(BASE_DIR, "tray_app.py")


def autostart_command() -> str:
    if is_frozen():
        return f'"{sys.executable}" --tray'
    return f'"{pythonw_path()}" "{tray_script()}"'


STARTUP_LNK_NAME = "电量托盘.lnk"

# 任务管理器/系统用这个键记录启动项的"启用/禁用"状态
#   值名 = 启动文件夹里的文件名；数据 12 字节，首字节 02=启用、03=禁用
STARTUP_APPROVED_FOLDER = (r"Software\Microsoft\Windows\CurrentVersion"
                           r"\Explorer\StartupApproved\StartupFolder")


def startup_dir() -> str:
    return win_shell.startup_dir()


def startup_lnk() -> str:
    """启动文件夹里的快捷方式路径。"""
    return os.path.join(win_shell.startup_dir(), STARTUP_LNK_NAME)


# 以前版本写在注册表里的项，启动时顺手清掉（迁移用）
LEGACY_RUN_VALUE = APP_NAME


def legacy_autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, LEGACY_RUN_VALUE)
            return bool(val)
    except OSError:
        return False


def _clear_legacy_autostart() -> bool:
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, LEGACY_RUN_VALUE)
            return True
    except OSError:
        return False


def _startup_approved() -> Optional[bool]:
    """任务管理器里的启用状态：True 启用 / False 禁用 / None 没记录（默认启用）"""
    import winreg
    base = os.path.basename(startup_lnk())
    names = (base, os.path.splitext(base)[0])       # 两种命名都兼容
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                            0, winreg.KEY_READ) as k:
            for name in names:
                try:
                    data, _ = winreg.QueryValueEx(k, name)
                except OSError:
                    continue
                if isinstance(data, (bytes, bytearray)) and data:
                    return data[0] != 0x03          # 0x03 = 已禁用
    except OSError:
        pass
    return None


def _set_startup_approved(enabled: bool) -> None:
    """写启用/禁用状态——写完之后任务管理器里看到的开关状态是一致的。"""
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                                0, winreg.KEY_SET_VALUE) as k:
            data = bytes([0x02 if enabled else 0x03]) + bytes(11)
            winreg.SetValueEx(k, os.path.basename(startup_lnk()), 0,
                              winreg.REG_BINARY, data)
    except OSError:
        pass


def _clear_startup_approved() -> None:
    import winreg
    base = os.path.basename(startup_lnk())
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                                0, winreg.KEY_SET_VALUE) as k:
            for name in (base, os.path.splitext(base)[0]):
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    """开机自启是否生效：快捷方式在、且没被任务管理器禁用。"""
    if win_shell.shortcut_exists(startup_lnk()):
        return _startup_approved() is not False
    return legacy_autostart_enabled()       # 兼容旧版注册表项


def set_autostart(enabled: bool) -> bool:
    """开关开机自启（兼容打包后的独立 .exe 与源码模式）。"""
    if enabled:
        if is_frozen():
            ok = win_shell.create_shortcut(
                startup_lnk(), sys.executable,
                arguments="--tray",
                workdir=BASE_DIR,
                description="无线外设电量托盘与任务栏监视器",
                icon=sys.executable)
        else:
            ok = win_shell.create_shortcut(
                startup_lnk(), pythonw_path(),
                arguments=f'"{tray_script()}"',
                workdir=BASE_DIR,
                description="无线外设电量托盘与任务栏监视器")
        _set_startup_approved(True)
    else:
        _clear_startup_approved()
        ok = win_shell.remove_shortcut(startup_lnk()) or \
            not win_shell.shortcut_exists(startup_lnk())
    _clear_legacy_autostart()               # 迁移：清掉旧的注册表项
    return ok


def autostart_status() -> dict:
    """给界面/命令行看的自启状态摘要。"""
    lnk = startup_lnk()
    approved = _startup_approved()
    return {
        "enabled": is_autostart_enabled(),
        "method": "启动文件夹快捷方式（任务管理器可见）",
        "shortcut": lnk,
        "shortcut_exists": win_shell.shortcut_exists(lnk),
        "taskmanager_state": ("已启用" if approved is not False else "已被任务管理器禁用")
                              if approved is not None else "未记录（默认启用）",
        "target": autostart_command(),
        "legacy_registry": legacy_autostart_enabled(),
    }


# --------------------------------------------------------------------------
# 桌面链接（软连接：目录 junction / 文件符号链接）
# --------------------------------------------------------------------------
DESKTOP_LINK_NAME = "设备电量查看器"          # 桌面上的项目文件夹链接名
LAUNCHER_NAMES = ["我的设备电量.bat", "电量托盘.bat"]


def desktop_dir() -> str:
    return win_shell.desktop_dir()


def desktop_link_status() -> dict:
    """桌面链接现状：{'folder': (路径, 类型), 'links': {名字: (路径, 类型)}}"""
    d = win_shell.desktop_dir()
    info = {
        "folder": (os.path.join(d, DESKTOP_LINK_NAME),
                   win_link.link_kind(os.path.join(d, DESKTOP_LINK_NAME))),
        "links": {},
    }
    for name in LAUNCHER_NAMES:
        p = os.path.join(d, name)
        info["links"][name] = (p, win_link.link_kind(p))
    return info


def install_desktop_links() -> dict:
    """建桌面链接：项目文件夹的目录链接 + 启动脚本的软链接。

    返回 {'folder': 类型, 'links': {名字: 类型}, 'errors': [消息]}
    """
    d = win_shell.desktop_dir()
    out: dict = {"folder": "", "links": {}, "errors": []}

    try:
        out["folder"] = win_link.create_dir_link(
            BASE_DIR, os.path.join(d, DESKTOP_LINK_NAME))
    except OSError as e:
        out["errors"].append(str(e))

    for name in LAUNCHER_NAMES:
        src = os.path.join(BASE_DIR, name)
        if not os.path.isfile(src):
            out["errors"].append(f"项目里没有 {name}，跳过")
            continue
        try:
            out["links"][name] = win_link.create_file_link(
                src, os.path.join(d, name))
        except OSError as e:
            out["errors"].append(str(e))
    return out


# --------------------------------------------------------------------------
# 进程间通信（窗口消息，双方都注册同名的 message）
# --------------------------------------------------------------------------
MSG_QUIT = "DeviceBatteryTray_Quit"             # 让托盘退出
MSG_RELOAD = "DeviceBatteryTray_Reload"         # 让托盘重读配置
MSG_TEST_NOTIFY = "DeviceBatteryTray_TestNotify"  # 让托盘立即弹出一条测试系统通知

UI_HOME_PROPERTY = 'DeviceBatteryViewer.OpenHome'
USER32.SetPropW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p]
USER32.SetPropW.restype = wintypes.BOOL
USER32.RemovePropW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
USER32.RemovePropW.restype = ctypes.c_void_p


def request_ui_home(hwnd):
    """A one-shot flag on the existing UI window; no persistent configuration."""
    return bool(USER32.SetPropW(hwnd, UI_HOME_PROPERTY, 1))


def consume_ui_home():
    hwnd = ui_hwnd()
    return bool(hwnd and USER32.RemovePropW(hwnd, UI_HOME_PROPERTY))


def promote_tray_icons_to_taskbar(enable: bool = True) -> int:
    """将本程序的通知区域数字图标设置为在 Windows 任务栏直接显示（IsPromoted=1），
    避免被折叠隐藏进任务栏右下角的 ^ 溢出面板中。返回成功更新的图标项数量。
    """
    import winreg
    updated = 0
    val_dword = 1 if enable else 0
    # Never change unrelated Python utilities or brand-driver tray icons.
    own_paths = {os.path.normcase(os.path.abspath(sys.executable))}
    hwnd = tray_hwnd()
    if hwnd:
        tray_pid = wintypes.DWORD(0)
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(tray_pid))
        running_image = process_image(tray_pid.value)
        if running_image:
            own_paths.add(os.path.normcase(os.path.abspath(running_image)))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICON_SETTINGS_KEY,
                            0, winreg.KEY_READ) as root:
            idx = 0
            subkeys = []
            while True:
                try:
                    subkeys.append(winreg.EnumKey(root, idx))
                    idx += 1
                except OSError:
                    break
        for sub in subkeys:
            sub_path = f"{NOTIFY_ICON_SETTINGS_KEY}\\{sub}"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_path,
                                    0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                    exe_path = ""
                    tooltip = ""
                    try:
                        exe_path, _ = winreg.QueryValueEx(k, "ExecutablePath")
                    except OSError:
                        pass
                    try:
                        tooltip, _ = winreg.QueryValueEx(k, "InitialTooltip")
                    except OSError:
                        pass
                    normalized = os.path.normcase(os.path.abspath(os.path.expandvars(str(exe_path))))
                    shared_python = os.path.basename(normalized) in ('python.exe', 'pythonw.exe')
                    is_ours = normalized in own_paths and (
                        not shared_python or str(tooltip).startswith('外设电量 · '))
                    if is_ours:
                        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, val_dword)
                        updated += 1
            except OSError:
                continue
    except OSError:
        pass
    return updated


def tray_hwnd() -> int:
    h = USER32.FindWindowW(WND_CLASS, None)
    return int(h) if h else 0


def is_tray_running() -> bool:
    return tray_hwnd() != 0


def post_to_tray(msg_name: str) -> bool:
    hwnd = tray_hwnd()
    if not hwnd:
        return False
    msg = USER32.RegisterWindowMessageW(msg_name)
    return bool(USER32.PostMessageW(hwnd, msg, 0, 0))


def ui_hwnd() -> int:
    h = USER32.FindWindowW(None, UI_TITLE)
    return int(h) if h else 0


def widget_hwnd() -> int:
    h = USER32.FindWindowW(None, WIDGET_TITLE)
    return int(h) if h else 0


def is_widget_running() -> bool:
    return widget_hwnd() != 0


def start_widget_app() -> bool:
    """启动固定在桌面的电量挂件（desktop_widget.py 或 exe --widget）。"""
    import subprocess
    if is_widget_running():
        return True
    if not is_frozen():
        script = os.path.join(BASE_DIR, "desktop_widget.py")
        if not os.path.isfile(script):
            return False
    try:
        subprocess.Popen(build_subcommand("--widget", "desktop_widget.py"),
                         cwd=BASE_DIR, env=clean_subprocess_env(),
                         creationflags=0x00000008)
        return True
    except Exception:
        return False


USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
USER32.GetWindowThreadProcessId.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD),
]


def stop_widget_app() -> bool:
    """关闭固定在桌面的电量挂件（解决 Tkinter overrideredirect 窗口忽略 WM_CLOSE 的问题）。"""
    import time
    hwnd = widget_hwnd()
    if not hwnd:
        return False

    pid = wintypes.DWORD(0)
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    USER32.PostMessageW(hwnd, WM_DESTROY, 0, 0)

    if pid.value and int(pid.value) != os.getpid():
        kill_pid(int(pid.value))

    deadline = time.time() + 1.0
    while time.time() < deadline and widget_hwnd():
        h2 = widget_hwnd()
        if h2:
            p2 = wintypes.DWORD(0)
            USER32.GetWindowThreadProcessId(h2, ctypes.byref(p2))
            if p2.value and int(p2.value) != os.getpid():
                kill_pid(int(p2.value))
        time.sleep(0.05)
    return not is_widget_running()


def sync_widget_state(cfg: Optional[dict] = None) -> None:
    """根据配置自动启动或关闭桌面电量挂件。"""
    c = cfg or load_config()
    if c.get("desktop_widget", True):
        if not is_widget_running():
            start_widget_app()
    else:
        if is_widget_running():
            stop_widget_app()


def start_tray_app(wait_timeout: float = 6.0) -> bool:
    """启动托盘程序；返回托盘窗口是否真的起来了。"""
    import subprocess
    if is_tray_running():
        return True
    cleanup_zombie()
    try:
        subprocess.Popen(build_subcommand("--tray", "tray_app.py"),
                         cwd=BASE_DIR, env=clean_subprocess_env(),
                         creationflags=0x00000008)
    except Exception:
        return False
    return wait_for_tray(wait_timeout)



# --------------------------------------------------------------------------
# 进程辅助：用于识别 / 清理僵尸托盘进程
# --------------------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259

KERNEL32.OpenProcess.restype = ctypes.c_void_p
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.GetExitCodeProcess.restype = wintypes.BOOL
KERNEL32.GetExitCodeProcess.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(wintypes.DWORD)]
KERNEL32.TerminateProcess.restype = wintypes.BOOL
KERNEL32.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
KERNEL32.QueryFullProcessImageNameW.restype = wintypes.BOOL
KERNEL32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]

_NTDLL = ctypes.WinDLL("ntdll")
_NTDLL.NtQueryInformationProcess.restype = ctypes.c_long
_NTDLL.NtQueryInformationProcess.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG)]
_PROCESS_COMMAND_LINE_INFORMATION = 60


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


def process_cmdline(pid: int) -> str:
    """取进程命令行（用于确认它确实是我们的托盘程序）。"""
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = 4096
        buf = ctypes.create_string_buffer(size)
        need = wintypes.ULONG(0)
        st = _NTDLL.NtQueryInformationProcess(
            h, _PROCESS_COMMAND_LINE_INFORMATION, buf, size, ctypes.byref(need))
        if st < 0 and need.value:
            size = int(need.value) + 16
            buf = ctypes.create_string_buffer(size)
            st = _NTDLL.NtQueryInformationProcess(
                h, _PROCESS_COMMAND_LINE_INFORMATION, buf, size,
                ctypes.byref(need))
        if st < 0:
            return ""
        us = _UNICODE_STRING.from_buffer(buf)
        if not us.Buffer or not us.Length:
            return ""
        return ctypes.wstring_at(us.Buffer, us.Length // 2)
    except Exception:
        return ""
    finally:
        KERNEL32.CloseHandle(h)


def write_pid(pid: Optional[int] = None) -> None:
    """把自己的 PID 写下来（进程意外卡死时，下一个实例能据此清掉它）。"""
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(pid if pid is not None else os.getpid()))
    except OSError:
        pass


def read_pid() -> int:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def remove_pid() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD(0)
        if not KERNEL32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        KERNEL32.CloseHandle(h)


def process_image(pid: int) -> str:
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if KERNEL32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        KERNEL32.CloseHandle(h)


def kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    h = KERNEL32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(KERNEL32.TerminateProcess(h, 1))
    finally:
        KERNEL32.CloseHandle(h)


def cleanup_zombie(wait: float = 2.0) -> bool:
    """清理僵尸托盘进程：上一个实例还活着（占着单实例锁），但窗口已经销毁。

    双重确认后才动手，避免 PID 复用误杀无辜进程：
      1) 镜像名以 "python" 开头；
      2) 命令行里确实有 tray_app。
    任一不满足就只清掉自己的 pid 文件，绝不碰那个进程。
    """
    import time
    pid = read_pid()
    if pid in (0, os.getpid()):
        return False
    if not pid_alive(pid):
        remove_pid()
        return False
    image = os.path.basename(process_image(pid)).lower()
    cmd = process_cmdline(pid).lower()
    if not ((image.startswith("python") and "tray_app" in cmd) or "--tray" in cmd):
        remove_pid()
        return False
    if not kill_pid(pid):
        return False
    deadline = time.time() + max(wait, 0.2)
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.05)
    remove_pid()
    return True


def wait_for_tray(timeout: float = 6.0) -> bool:
    """等托盘窗口出现（启动是否成功只能靠它判断）。"""
    import time
    deadline = time.time() + max(timeout, 0)
    while time.time() < deadline:
        if is_tray_running():
            return True
        time.sleep(0.15)
    return is_tray_running()
