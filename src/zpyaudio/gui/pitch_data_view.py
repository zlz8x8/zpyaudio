"""主频时序数据输出区（规格书 v0.5 变更 2）。

布局位置：日志区**右侧**，约占窗口总宽度的 1/3（``main_window`` 的底部横向
``QSplitter``）。职责与日志区一样，只做展示：

* 数据由分析线程的 :class:`~zpyaudio.core.pitch_series.PitchSeries` 收集，
  界面用 :meth:`PitchDataView.append_points` 增量追加，**不重算、不做 IO**；
* 行数上限 :data:`DEFAULT_MAX_LINES`（默认 2000），超出后自动丢弃最旧的块，
  保证长时间运行内存可控（NFR-3）；导出 CSV 仍是全量（与显示上限无关）；
* 音名口径跟随"主频中值滤波"开关：开启时用 3 点中值（与主频时序图一致），
  低频段（< 250 Hz）读数加 ``*`` 提示，具体说明见控件上方的标签。
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from zpyaudio.core.notes import format_note
from zpyaudio.core.pitch_series import SeriesPoint, PitchSeries

__all__ = ["PitchDataView", "DEFAULT_MAX_LINES", "COLUMN_HEADER"]

#: 数据区保留的最大行数（含列头）
DEFAULT_MAX_LINES = 2000

#: 列头（等宽字体下与数据行近似对齐）
COLUMN_HEADER = "#    时间/s    频率/Hz  音名        置信度"


class PitchDataView(QPlainTextEdit):
    """只读、自动滚动的"主频时序数据"文本区。"""

    def __init__(
        self,
        *,
        max_lines: int = DEFAULT_MAX_LINES,
        smoothing: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if max_lines <= 1:
            raise ValueError(f"max_lines 必须大于 1，收到 {max_lines}")
        self.setReadOnly(True)
        self.setMaximumBlockCount(int(max_lines))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self._smoothing = bool(smoothing)
        self._points_written = 0
        self.clear_data()

    # ---------------------------------------------------------------- 属性
    @property
    def smoothing(self) -> bool:
        """音名口径是否使用 3 点中值滤波。"""
        return self._smoothing

    @property
    def max_lines(self) -> int:
        """显示行数上限。"""
        return int(self.maximumBlockCount())

    @property
    def row_count(self) -> int:
        """当前行数（含列头）。"""
        return int(self.blockCount())

    @property
    def points_written(self) -> int:
        """累计写入的数据行数（不受显示上限影响）。"""
        return self._points_written

    # ---------------------------------------------------------------- 配置
    def set_smoothing(self, enabled: bool) -> None:
        """切换音名口径（仅影响后续行，历史行保留写入时的口径）。"""
        self._smoothing = bool(enabled)

    def clear_data(self) -> None:
        """清空数据并复位列头（开始新一次采集时调用）。"""
        self.setPlainText(COLUMN_HEADER)
        self.moveCursor(QTextCursor.MoveOperation.End)
        self._points_written = 0

    # ---------------------------------------------------------------- 更新
    def append_points(self, points: Sequence[SeriesPoint]) -> int:
        """追加数据行，返回实际写入的行数（无有效频率的点被跳过）。"""
        lines: list[str] = []
        for item in points:
            freq = item.display_freq(smoothing=self._smoothing)
            info = PitchSeries.note_of(item, smoothing=self._smoothing)
            if info is None:
                continue
            note = format_note(info, low_band_hint="mark" if info.low_band else None)
            lines.append(f"{item.t:9.3f} {freq:10.1f}  {note:<12}{item.confidence:6.2f}")
        if not lines:
            return 0
        self.appendPlainText("\n".join(lines))
        self._points_written += len(lines)
        return len(lines)

    def text_lines(self) -> list[str]:
        """当前显示的所有行（测试与自检用）。"""
        return self.toPlainText().splitlines()
