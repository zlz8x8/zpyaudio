"""日志展示区（规格书 7.1 区域⑤、7.3）。

后台线程（分析/采集）只调用 ``logging``，由 :class:`QtLogHandler` 转成 Qt 信号，
以队列连接投递到 GUI 线程，避免跨线程直接操作控件。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit

try:  # PySide6 的对象有效性检查（对象被 C++ 侧销毁后 emit 会崩溃）
    from shiboken6 import isValid as _is_valid
except Exception:  # pragma: no cover - 理论上 PySide6 一定带 shiboken6

    def _is_valid(obj) -> bool:  # type: ignore[misc]
        return obj is not None

__all__ = ["LogEmitter", "QtLogHandler", "LogView"]

LOG_VIEW_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s"
LOG_VIEW_DATEFMT = "%H:%M:%S"

#: 日志区最多保留的行数
MAX_BLOCKS = 2000


class LogEmitter(QObject):
    """承载日志文本的信号载体。"""

    message = Signal(str)


class QtLogHandler(logging.Handler):
    """把日志记录转发为 Qt 信号。"""

    def __init__(self, level: int = logging.INFO, parent: QObject | None = None) -> None:
        super().__init__(level)
        self.emitter = LogEmitter(parent)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # pragma: no cover - 格式化失败不应影响程序
            self.handleError(record)
            return
        # 控件被销毁（窗口关闭/对象回收）后，底层 C++ 对象已不存在，
        # 此时 emit 会触发访问违例，必须先校验（实测会直接崩进程）。
        if not _is_valid(self.emitter):
            return
        self.emitter.message.emit(text)


class LogView(QPlainTextEdit):
    """只读、自动滚动的日志控件。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self._handler = QtLogHandler(logging.INFO)
        self._handler.setFormatter(logging.Formatter(LOG_VIEW_FORMAT, datefmt=LOG_VIEW_DATEFMT))
        self._handler.emitter.message.connect(
            self.appendPlainText, Qt.ConnectionType.QueuedConnection
        )
        # 即使窗口没走 closeEvent 就被销毁，也要把处理器摘掉
        self.destroyed.connect(self._on_destroyed)

    def _on_destroyed(self, *_args) -> None:
        self.detach()

    def attach_to_root_logger(self) -> None:
        """接入根 logger，开始显示程序日志。"""
        root = logging.getLogger()
        if self._handler not in root.handlers:
            root.addHandler(self._handler)

    def detach(self) -> None:
        """断开日志，避免窗口销毁后仍被回调。"""
        root = logging.getLogger()
        if self._handler in root.handlers:
            root.removeHandler(self._handler)
