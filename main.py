#!/usr/bin/env python
"""zpyaudio 启动脚本。

规格书 5.1：根目录 ``main.py`` 负责把 ``src/`` 加入 ``sys.path`` 后启动 GUI，
同时也是后续 PyInstaller 的打包入口。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from zpyaudio.app.cli import main  # noqa: E402  (必须在 sys.path 调整之后导入)

if __name__ == "__main__":
    raise SystemExit(main())
