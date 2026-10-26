# -*- coding: utf-8 -*-
"""win_link.py —— 创建软连接（符号链接 / 目录 junction / 文件硬链接）

Windows 的**符号链接**（symlink）创建需要「管理员」或「开发者模式」；
不支持时可以降级，行为上等价、且免权限：

* 目录 → **junction**（`mklink /J`，NTFS 原生，任何版本都支持）
* 文件 → **硬链接**（NTFS 原生，同一分区内等价，删除任一处不影响另一处）

本模块按可用性自动降级，并如实返回实际用了哪种链接。
"""

from __future__ import annotations

import ctypes
import os
import subprocess

IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

CREATE_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def link_kind(path: str) -> str:
    """返回 'symlink' / 'junction' / 'hardlink' / 'dir' / 'file' / ''（不存在）"""
    if not os.path.lexists(path):
        return ""
    try:
        st = os.lstat(path)
    except OSError:
        return ""
    tag = getattr(st, "st_reparse_tag", 0)
    if tag == IO_REPARSE_TAG_SYMLINK:
        return "symlink"
    if tag == IO_REPARSE_TAG_MOUNT_POINT:
        return "junction"
    if os.path.isdir(path):
        return "dir"
    if st.st_nlink > 1:
        return "hardlink"
    return "file"


def link_target(path: str) -> str:
    """取链接指向的目标（读不到就返回空串）。"""
    try:
        return os.path.realpath(path)
    except Exception:
        return ""


def _mklink(flag: str, link: str, target: str) -> bool:
    """调用系统的 mklink（/J 建 junction，/H 建硬链接），免管理员权限。"""
    cmd_exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "cmd.exe")
    if not os.path.exists(cmd_exe):
        cmd_exe = "cmd.exe"
    try:
        r = subprocess.run([cmd_exe, "/c", "mklink", "/" + flag, link, target],
                           capture_output=True, text=True, encoding="mbcs",
                           errors="replace", creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0 and os.path.lexists(link)
    except Exception:
        return False


def _already_ok(link: str, target: str):
    """已经是指向该目标的链接就返回它的类型，否则 None（用于幂等重建）。"""
    kind = link_kind(link)
    if kind not in ("symlink", "junction", "hardlink"):
        return None
    try:
        if os.path.samefile(link, target):
            return kind
    except OSError:
        pass
    return None


def create_dir_link(target: str, link: str) -> str:
    """给目录建软连接，返回 'symlink' 或 'junction'；失败抛 OSError。"""
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        raise OSError(f"目标不是目录：{target}")

    same = _already_ok(link, target)
    if same:                                # 已经是正确的链接，不动它
        return same
    _clear_slot(link)

    try:                                    # 首选：真符号链接
        os.symlink(target, link, target_is_directory=True)
        if link_kind(link) == "symlink":
            return "symlink"
    except OSError:
        pass

    if _mklink("J", link, target):           # 退路：junction（免权限）
        return "junction"
    raise OSError(f"创建目录链接失败：{link} -> {target}")


def create_file_link(target: str, link: str) -> str:
    """给文件建软连接，返回 'symlink' 或 'hardlink'；失败抛 OSError。"""
    target = os.path.abspath(target)
    if not os.path.isfile(target):
        raise OSError(f"目标不是文件：{target}")

    same = _already_ok(link, target)
    if same:                                # 已经是正确的链接，不动它
        return same
    _clear_slot(link)

    try:                                    # 首选：真符号链接
        os.symlink(target, link)
        if link_kind(link) == "symlink":
            return "symlink"
    except OSError:
        pass

    try:                                    # 退路：硬链接（同分区、免权限）
        os.link(target, link)
        if link_kind(link) == "hardlink":
            return "hardlink"
    except OSError as e:
        raise OSError(f"创建文件链接失败：{link} -> {target}（{e}）")

    raise OSError(f"创建文件链接失败：{link} -> {target}")


def _clear_slot(path: str) -> None:
    """腾出位置：已存在的链接直接删；真实文件/文件夹绝不擅自删除。"""
    kind = link_kind(path)
    if kind == "":
        return
    if kind in ("symlink", "junction", "hardlink"):
        if not remove_link(path) and os.path.lexists(path):
            raise OSError(f"无法移除已存在的链接：{path}")
        return
    raise OSError(f"该位置已存在同名的{kind}，为安全起见没有动它：{path}")


def remove_link(path: str) -> bool:
    """只删掉链接本身，不动目标。"""
    if not os.path.lexists(path):
        return True
    kind = link_kind(path)
    try:
        if kind in ("symlink", "junction") and os.path.isdir(path):
            os.rmdir(path)                  # 目录链接：rmdir 只摘链接
        else:
            os.remove(path)                 # 文件链接 / 硬链接：删的是链接项
        return True
    except OSError:
        return False


KIND_LABEL = {
    "symlink": "符号链接（真软连接）",
    "junction": "目录联接 junction",
    "hardlink": "硬链接",
    "dir": "普通文件夹",
    "file": "普通文件",
    "": "不存在",
}
