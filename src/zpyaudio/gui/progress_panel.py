"""进度条与时间标签（规格书 7.1 区域⑥、FR-3.2）。

* 播放：显示 ``当前 / 总时长``，点击进度条可定位（seek）；
* 录制：无总时长，显示已录时长并使用不确定态进度条。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

__all__ = ["ProgressPanel", "SeekBar", "format_seconds"]

#: 进度条内部刻度（提供 0.1% 分辨率）
_SCALE = 1000


def format_seconds(seconds: float) -> str:
    """把秒格式化成 ``mm:ss.S``（规格书 7.3）。"""
    if seconds is None or seconds < 0:
        seconds = 0.0
    minutes, rest = divmod(float(seconds), 60.0)
    return f"{int(minutes):02d}:{rest:04.1f}"


class SeekBar(QProgressBar):
    """可点击定位的进度条。"""

    seekRequested = Signal(float)  # 目标位置（秒）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRange(0, _SCALE)
        self.setTextVisible(False)
        self._duration: float | None = None
        self._seekable = False

    def set_duration(self, duration: float | None) -> None:
        """设置总时长；``None`` 表示不可定位（如录制中）。"""
        self._duration = duration if duration and duration > 0 else None
        self._seekable = self._duration is not None
        self.setRange(0, _SCALE)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._seekable else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._seekable and event.button() == Qt.MouseButton.LeftButton:
            ratio = event.position().x() / max(1.0, float(self.width()))
            target = max(0.0, min(1.0, ratio)) * float(self._duration)
            self.seekRequested.emit(target)
            return
        super().mousePressEvent(event)


class ProgressPanel(QWidget):
    """进度条 + ``当前/总时长`` 标签。"""

    seekRequested = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.bar = SeekBar(self)
        self.label = QLabel("00:00.0 / --:--", self)
        self.label.setMinimumWidth(160)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 4)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.label)
        self.bar.seekRequested.connect(self.seekRequested)

    def reset(self) -> None:
        """回到空闲态。"""
        self.bar.setValue(0)
        self.bar.setRange(0, _SCALE)
        self.bar.set_duration(None)
        self.label.setText("00:00.0 / --:--")

    def update_position(self, position: float, duration: float | None) -> None:
        """刷新进度与时间标签。"""
        if duration and duration > 0:
            self.bar.set_duration(duration)
            ratio = max(0.0, min(1.0, position / duration))
            self.bar.setValue(int(round(ratio * _SCALE)))
            self.label.setText(f"{format_seconds(position)} / {format_seconds(duration)}")
        else:
            # 录制：无总时长 → 不确定态进度条 + 已录时长
            self.bar.set_duration(None)
            self.bar.setRange(0, 0)
            self.label.setText(f"{format_seconds(position)}")
