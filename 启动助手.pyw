# -*- coding: utf-8 -*-
r"""启动助手.pyw —— 双击这个文件就能打开图形界面(不弹安全警告,也不闪控制台窗口)。

为什么不直接用 启动助手.bat 双击:
  这台机器上装了带进程注入的防护软件(实测对话框宿主进程里挂着
  C:\Program Files (x86)\Kaspersky Lab\Kaspersky 21.26\x64\com_antivirus.dll),
  它对**本项目目录树下**的脚本类文件(.bat / .cmd / .vbs / .js)双击启动会弹出
  「打开文件 - 安全警告 / 无法验证发布者」;而 .pyw / .py 不在它拦的类型里。
  (Windows 自己的区域查询返回 zone=0 本地计算机,这些文件也没有下载标记,所以
  "解除锁定"、低风险文件类型名单都无效。)

本文件和 启动助手.bat 做同一件事:挑一个 Python 3.9+ 的 pythonw.exe,用它跑 gui.py。
命令行里也照旧可以用 启动助手.bat。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

MIN_MAJOR, MIN_MINOR = 3, 9
HERE = pathlib.Path(__file__).resolve().parent
GUI = HERE / "gui.py"

_VERSION_CHECK = ("import sys; raise SystemExit(0 if sys.version_info >= (%d, %d) else 1)"
                  % (MIN_MAJOR, MIN_MINOR))


def _fail(message: str) -> int:
    """没有控制台,用消息框告诉用户(失败时至少能看见原因)。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "启动助手", 0x10)
    except Exception:
        pass
    return 1


def _pythonw_ok(exe: str) -> bool:
    if not exe or not os.path.isfile(exe):
        return False
    try:
        done = subprocess.run([exe, "-c", _VERSION_CHECK], capture_output=True, timeout=30)
    except Exception:
        return False
    return done.returncode == 0


def _sibling_pythonw(python_exe: str) -> str:
    p = pathlib.Path(python_exe)
    return str(p.with_name("pythonw.exe" if os.name == "nt" else "pythonw"))


def _candidates():
    """按优先级列出候选 pythonw.exe(都要求 Python 3.9+)。"""
    out = []

    def add(path):
        if path and path not in out:
            out.append(path)

    # 1) 现在这个进程的解释器(双击 .pyw 通常是 C:\Windows\pyw.exe 选中的 pythonw)
    if sys.executable and sys.executable.lower().endswith("pythonw.exe"):
        add(sys.executable)
    # 2) 项目自带的虚拟环境(仅当它够新;本机这份是 3.8,会被版本检查刷掉)
    add(str(HERE / "venv" / "Scripts" / "pythonw.exe"))
    # 3) 常见的本机安装位置
    local = os.environ.get("LOCALAPPDATA") or ""
    for ver in ("Python313", "Python312", "Python311", "Python310"):
        if local:
            add(os.path.join(local, "Programs", "Python", ver, "pythonw.exe"))
    # 4) PATH 上的 pythonw / python
    for which in ("pythonw", "python"):
        found = shutil.which(which)
        if found:
            add(_sibling_pythonw(found) if which == "python" else found)
    return out


def main() -> int:
    if not GUI.is_file():
        return _fail("找不到 gui.py:\n%s\n\n请确认本文件与 gui.py 在同一个目录下。" % GUI)
    tried = []
    for exe in _candidates():
        tried.append(exe)
        if _pythonw_ok(exe):
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            subprocess.Popen([exe, str(GUI)], cwd=str(HERE), close_fds=True, creationflags=flags)
            return 0
    return _fail("没找到 Python 3.9+ 的 pythonw.exe,界面起不来。\n\n"
                 "候选(都不满足,顺次检查):\n  " + "\n  ".join(tried) +
                 "\n\n请安装 Python 3.9+,或在终端里跑:python gui.py")


if __name__ == "__main__":
    sys.exit(main())
