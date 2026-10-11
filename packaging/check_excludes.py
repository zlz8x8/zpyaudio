"""打包前预检：验证 ``packaging/zpyaudio.spec`` 的 excludes 不会"排除过头"。

背景（实测踩过的坑）：conda 环境里装了大量本程序用不到的包，``pyqtgraph`` →
``MatplotlibWidget`` → matplotlib → pandas → numba/cupy/IPython 这条可选依赖链会把产物
从 ~230 MB 撑到 1.7 GB，因此 spec 里显式排除了一整批包。但**排除过头**的代价很隐蔽：

* 排除 ``numpy.f2py`` → ``scipy.signal`` 导入时触发 ``numpy.__getattr__`` 的惰性导入 → 崩溃；
* 排除 ``_ctypes``/DLL → ``ctypes`` 起不来 → ``scipy`` 直接报 "install seems to be broken"。

冻结后的窗口程序崩溃时只弹一个"Unhandled exception in script"对话框，看不到 traceback，
排错要重新打包（约 80 s）。本脚本用 **import hook 屏蔽同样的模块名**，在源码环境里跑一遍
全部命令行入口，几秒内就能发现"排除了不该排除的模块"。

用法::

    C:\\miniconda3\\envs\\ibase\\python.exe packaging\\check_excludes.py

判据：各入口退出码为 0（``--list-devices`` 在无输入设备的机器上返回 1，会被标 SKIP）。
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "packaging" / "zpyaudio.spec"
BLOCKER_DIR = PROJECT_ROOT / "build" / "importblock"

#: 待检查的命令行入口（与 packaging/verify_portable.ps1 保持一致）
CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("--self-test（离屏 GUI）", ("--self-test", "--self-test-ms", "300")),
    ("--audio-check 2（合成源）", ("--audio-check", "2")),
    ("--list-devices（PortAudio）", ("--list-devices",)),
    ("--midi-check（mido）", ("--midi-check", str(PROJECT_ROOT / "build" / "demo_midi.mid"))),
)

BLOCKER_TEMPLATE = '''"""自动生成（packaging/check_excludes.py）：屏蔽 spec 中 excludes 的模块。"""

import sys
from importlib.abc import MetaPathFinder

BLOCKED = {blocked!r}


class _Blocker(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in BLOCKED:
            raise ImportError(f"blocked by check_excludes.py: {{fullname}}")
        return None


sys.meta_path.insert(0, _Blocker())
'''


def read_excludes() -> list[str]:
    """从 spec 里读出 excludes 列表（字面量求值，不执行 spec）。"""
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "excludes" for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise SystemExit(f"未在 {SPEC_PATH} 中找到 excludes 定义")


def write_blocker(excludes: list[str]) -> Path:
    """生成 sitecustomize.py（只屏蔽非 PySide6 的模块名，Qt 模块名不含点号歧义）。"""
    BLOCKER_DIR.mkdir(parents=True, exist_ok=True)
    blocked = sorted({name for name in excludes if not name.startswith("PySide6")})
    target = BLOCKER_DIR / "sitecustomize.py"
    target.write_text(BLOCKER_TEMPLATE.format(blocked=blocked), encoding="utf-8")
    return target


def run_check(name: str, arguments: tuple[str, ...]) -> tuple[str, int]:
    """在屏蔽环境下跑一个入口，返回 (结论, 退出码)。"""
    if arguments[0] == "--midi-check" and not Path(arguments[1]).is_file():
        return "SKIP", 0
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BLOCKER_DIR)
    env["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "main.py"), *arguments],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        # 日志是 UTF-8，而控制台默认编码是 GBK：不解码指定会抛 UnicodeDecodeError
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode == 0:
        return "PASS", 0
    tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:]
    for line in tail:
        print(f"      {line.strip()}")
    return "FAIL", completed.returncode


def main() -> int:
    excludes = read_excludes()
    blocker = write_blocker(excludes)
    print(f"spec excludes：{len(excludes)} 项")
    print(f"屏蔽脚本：{blocker}")
    print(f"解释器：{sys.executable}\n")

    results = []
    for name, arguments in CHECKS:
        verdict, code = run_check(name, arguments)
        print(f"  [{verdict}] {name}（退出码 {code}）")
        results.append(verdict)

    failed = results.count("FAIL")
    print()
    if failed:
        print(f"预检失败：{failed} 个入口被 excludes 打断——请从 spec 的 excludes 里移除相应模块")
        return 1
    print("预检通过：excludes 未打断任何命令行入口，可以放心打包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
