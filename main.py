"""入口：python main.py

职责（必须都在导入 tkinter 之前完成）：
1. 校验运行在项目虚拟环境里，避免误用系统 Python；
2. 声明 DPI 感知，解决高分屏下界面被位图拉伸导致的模糊；
3. 把临时目录/工作目录收敛到本目录的 .runtime/ 下；
4. 补 TCL_LIBRARY（部分解压版 Python 找不到 init.tcl）。
"""
import os
import sys


def _ensure_venv():
    """如果当前不在虚拟环境里，尝试切换到 .venv 下的 Python 重跑。"""
    if sys.prefix != sys.base_prefix:
        return  # 已经在虚拟环境里

    here = os.path.dirname(os.path.abspath(__file__))
    exe = 'Scripts/python.exe' if sys.platform == 'win32' else 'bin/python'

    # 先找程序目录下的 .venv，再找上级目录的
    for root in (here, os.path.dirname(here)):
        candidate = os.path.join(root, '.venv', *exe.split('/'))
        if os.path.exists(candidate):
            print(f'切换到虚拟环境: {candidate}')
            os.execv(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]])
            return

    print('警告：没找到 .venv，将用当前 Python 运行。')
    print('建议先执行: python -m venv .venv && pip install -r requirements.txt')


def _enable_dpi_awareness():
    """声明进程 DPI 感知，避免 Windows 对窗口做位图放大（界面发虚的根因）。

    用 SYSTEM_DPI_AWARE：Tk 本身不支持 per-monitor 缩放，
    per-monitor-v2 反而会让 Tk 按 96dpi 绘制后被系统再次缩放。
    """
    if sys.platform != 'win32':
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _system_dpi() -> int:
    """读取系统真实 DPI（如 125% 缩放 -> 120）。"""
    if sys.platform != 'win32':
        return 96
    import ctypes
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return int(dpi)
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return int(dpi)
    except Exception:
        pass
    return 96


def _fix_tcl_env():
    if os.environ.get('TCL_LIBRARY') and os.environ.get('TK_LIBRARY'):
        return
    for base in {sys.base_prefix, sys.prefix, os.path.dirname(sys.executable)}:
        tcl_dir = os.path.join(base, 'tcl')
        tcl_lib = os.path.join(tcl_dir, 'tcl8.6')
        tk_lib = os.path.join(tcl_dir, 'tk8.6')
        if os.path.isdir(tcl_lib) and os.path.isdir(tk_lib):
            os.environ.setdefault('TCL_LIBRARY', tcl_lib)
            os.environ.setdefault('TK_LIBRARY', tk_lib)
            return


_ensure_venv()
_enable_dpi_awareness()
# Tk 不认 Windows 的缩放比例，手动把真实 DPI 传给它，字号才不会又小又糊
os.environ['JM_SYSTEM_DPI'] = str(_system_dpi())

# 模块都在 src/ 下，先把 src 加进搜索路径
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'src'))

# 必须在 import jmcomic / tempfile 之前
import settings  # noqa: E402

settings.apply_runtime_env()

_fix_tcl_env()

from ui import main  # noqa: E402

if __name__ == '__main__':
    main()
