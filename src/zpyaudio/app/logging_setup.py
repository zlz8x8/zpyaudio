"""日志初始化（规格书 NFR-5 / NFR-9）。

控制台 + 落盘 ``logs/zpyaudio_YYYYmmdd_HHMMSS.log``，格式含毫秒时间戳与线程名，
便于诊断分析线程与 GUI 线程的节拍问题。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

__all__ = ["setup_logging", "parse_level", "use_utf8_stdio", "LOG_FORMAT", "DATE_FORMAT"]

LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 标记本程序安装的 handler，便于重复调用时清理
_HANDLER_FLAG = "_zpyaudio_handler"


def use_utf8_stdio() -> None:
    """把标准输出/错误切到 UTF-8，避免重定向到管道/文件时中文乱码（NFR-5）。

    真实控制台下 Python 3.6+ 本就使用 UTF-8；此处的关键是重定向场景，
    否则会按系统 ANSI 代码页（如 cp936）编码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - 非 TextIOWrapper（如被测试替换）
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 已分离的流
            continue


def parse_level(level: str | int) -> int:
    """把级别名或数字转成 logging 级别；非法值回退 INFO。"""
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_logging(
    log_dir: str | Path,
    level: str | int = "INFO",
    *,
    prefix: str = "zpyaudio",
) -> Path:
    """配置根 logger，返回日志文件路径。可重复调用（会清理旧 handler）。"""
    use_utf8_stdio()
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = directory / f"{prefix}_{timestamp}.log"

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_FLAG, True)

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(formatter)
    setattr(stream_handler, _HANDLER_FLAG, True)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(parse_level(level))
    return log_file
