# -*- mode: python ; coding: utf-8 -*-
"""zpyaudio Windows 便携版打包配置（M6 / NFR-7）。

构建（在仓库根执行，或直接用 ``packaging/build_portable.ps1``）::

    C:\\miniconda3\\envs\\ibase\\python.exe -m PyInstaller --clean --noconfirm packaging\\zpyaudio.spec

产物（**onedir 文件夹形态**，非安装包）::

    dist/zpyaudio-0.1.0-win.amd64-portable/
        zpyaudio.exe          可执行程序（GUI 子系统，双击不弹控制台）
        _internal/            Python 运行时 + Qt + numpy/scipy 等依赖
        PORTABLE-README.txt   使用说明（由 build_portable.ps1 复制到根）

与其他里程碑的约定：

* **不打包 ffmpeg**：ffmpeg 是外部程序，便携版启动后按 FR-6.4 在 PATH 与常见安装位置
  自动检测，检测不到仍可用 wav/flac/ogg 与 MIDI；用户可在「文件 → 设置 ffmpeg 路径…」指定；
* **便携**：配置/日志/录制目录写在 exe 同级目录（``app/config.py::resolve_project_root()``），
  不写注册表、不写用户 AppData；
* ``console=False``：双击即 GUI。命令行自检（``--self-test`` / ``--audio-check`` 等）仍然可用，
  结论写进 exe 同级的 ``logs/*.log``，进程退出码同样有效。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

#: spec 所在目录（packaging/）与仓库根
SPEC_DIR = Path(SPECPATH).resolve()  # noqa: F821 - SPECPATH 由 PyInstaller 注入
ROOT = SPEC_DIR.parent

# 版本号取单一来源：src/zpyaudio/__init__.py
sys.path.insert(0, str(ROOT / "src"))
from zpyaudio import __version__  # noqa: E402

APP_NAME = "zpyaudio"
DIST_NAME = f"{APP_NAME}-{__version__}-win.amd64-portable"

#: 动态导入的模块必须显式声明（mido 的后端是运行时挑选的；本包自身也整体收集一遍）
hiddenimports = sorted({*collect_submodules("mido"), *collect_submodules("zpyaudio")})

#: 明确用不到的重量级 Qt 模块：排除后只影响这些能力，不影响波形/频谱/主频/MIDI 界面。
#: 保留 QtSvg（pyqtgraph 图标）与 QtNetwork（Qt 插件常用依赖）。
excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # --- 第三方库的"可选功能"链 ------------------------------------------------
    # pyqtgraph 顶层会尝试 import MatplotlibWidget → matplotlib → pandas →
    # numba/llvmlite、cupy、IPython/jedi/tornado、lxml、Pillow…，在 conda 环境里
    # 这些包真实存在，会把产物从 ~250 MB 撑到 **1.7 GB**。本程序只用
    # pyqtgraph 的 PlotWidget/BarGraphItem/ScatterPlotItem/InfiniteLine，
    # 与上述可选后端无关，因此整条链排除（缺了它们只会在 pyqtgraph 内部走
    # ImportError 分支，功能不受影响）。
    "matplotlib",
    "matplotlib_inline",
    "pandas",
    "numba",
    "llvmlite",
    "cupy",
    "cupy_backends",
    "cupyx",
    "cuda",
    "IPython",
    "ipykernel",
    "ipython_genutils",
    "jupyter_client",
    "jupyter_core",
    "jedi",
    "parso",
    "traitlets",
    "tornado",
    "zmq",
    "comm",
    "debugpy",
    "stack_data",
    "executing",
    "asttokens",
    "pure_eval",
    "lxml",
    "bs4",
    "soupsieve",
    "PIL",
    "pygments",
    "tkinter",
    "_tkinter",
    "sqlite3",
    # 注意：不要再排除 distutils —— PyInstaller 把 setuptools._distutils 别名到 distutils，
    # 同时"排除 + 别名"会抛 ValueError: Target module "distutils" already imported as ExcludedModule。
    "setuptools",
    # 也不要排除 numpy.f2py：scipy.signal → scipy._external.array_api_compat 会
    # `from numpy import *`，触发 numpy.__getattr__ 里的 `import numpy.f2py`，
    # 排除后 scipy.signal 直接 ImportError（实测踩过）。
    # 只在源码环境用到
    "pytest",
    "pytestqt",
    "PyInstaller",
]

a = Analysis(  # noqa: F821 - PyInstaller 注入
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# --- conda 环境的 C 运行时 DLL -------------------------------------------------
# conda 把 Python 扩展的依赖 DLL 放在 <env>\Library\bin（不在 Windows 默认搜索路径里），
# PyInstaller 解析 .pyd 的依赖时**看不到**它们，于是打进 _ctypes.pyd 却没有 ffi-8.dll，
# 冻结后 `import ctypes` 直接 "DLL load failed while importing _ctypes"（实测踩过：
# scipy → ctypes → 整个程序起不来）。这里按"产物里实际有哪个 .pyd"补齐对应 DLL。
CONDA_DLL_DIRS = (Path(sys.base_prefix) / "Library" / "bin", Path(sys.base_prefix) / "DLLs")

CONDA_DLL_REQUIREMENTS = {
    "_ctypes": ("ffi*.dll",),
    "pyexpat": ("libexpat.dll", "expat.dll"),
    "_bz2": ("libbz2.dll",),
    "_hashlib": ("libcrypto-3-x64.dll",),
    "_ssl": ("libcrypto-3-x64.dll", "libssl-3-x64.dll"),
    "_sqlite3": ("sqlite3.dll",),
}


def _conda_dll_binaries(binaries) -> list[tuple[str, str, str]]:  # noqa: ANN001
    """产物里出现了某个扩展时，把它在 conda 环境里的依赖 DLL 一并带上。

    ``a.binaries`` 的条目是 ``(目标相对路径, 源路径, 类型)``（PyInstaller 6 的 TOC 约定），
    DLL 统一放到包内容目录根下（与 ``python314.dll`` 同级，在扩展模块的搜索路径里）。
    """
    bundled_stems = {Path(entry[0]).stem.lower() for entry in binaries}
    extra: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for stem, patterns in CONDA_DLL_REQUIREMENTS.items():
        if stem not in bundled_stems:
            continue
        for pattern in patterns:
            for directory in CONDA_DLL_DIRS:
                if not directory.is_dir():
                    continue
                for dll in sorted(directory.glob(pattern)):
                    if dll.name.lower() in seen:
                        continue
                    seen.add(dll.name.lower())
                    extra.append((dll.name, str(dll), "BINARY"))
    return extra


_python_runtime_dlls = _conda_dll_binaries(a.binaries)
a.binaries += _python_runtime_dlls
print(
    "[spec] 补充 conda 运行时 DLL："
    + (", ".join(dest for dest, _src, _kind in _python_runtime_dlls) or "（无）")
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI 子系统：双击不弹黑窗（自检结论看 logs/*.log 与退出码）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(SPEC_DIR / "version_info.txt"),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=DIST_NAME,
)
