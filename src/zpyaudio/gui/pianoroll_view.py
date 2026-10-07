"""钢琴卷帘（规格书 7.1 区域④、3.4 FR-4.2，M5 里程碑）。

横轴时间（秒）、纵轴音高（MIDI 音符号，左轴直接标音名）。每个音符画成一根横条，
条长就是音符时长 —— FR-4.2 的验收判据"音符起止位置正确"就落在 ``x0`` / ``width`` 上。

约定
----
* **只在打开 MIDI 时显示**：可见性由主窗口控制（:meth:`set_document` 不改可见性），
  因为"何时显示"是界面布局问题，不是数据问题；
* GUI 不做文件 IO：本视图只接收 :class:`~zpyaudio.media.midi_loader.MidiDocument`；
* 方法名避开 ``PlotItem`` 上已有的成员（``clear`` 等）—— ``pg.PlotWidget.__getattr__``
  会把同名成员转发出去，子类方法会被**静默遮蔽**（项目里真踩过，见 tests/test_views.py）。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg

from zpyaudio.core.notes import NOTE_NAMES
from zpyaudio.media.midi_loader import MidiDocument

__all__ = ["PianoRollView", "note_label"]

#: 音符条在纵轴上的高度（单位是半音，留一点缝便于分辨相邻音）
BAR_HEIGHT = 0.8

#: 超短音符的最小可视宽度（占全曲时长的比例）
MIN_BAR_FRACTION = 0.001

#: 超过这么多半音时，左轴只标每个八度的 C，避免刻度挤成一团
FULL_LABEL_SEMITONES = 24


def note_label(note: int) -> str:
    """MIDI 音符号 → 音名（如 ``60 → C4``），与 FR-4.3 的八度定义一致。"""
    return f"{NOTE_NAMES[int(note) % 12]}{int(note) // 12 - 1}"


class PianoRollView(pg.PlotWidget):
    """MIDI 音符的钢琴卷帘图。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent)
        self.setBackground("w")
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("bottom", "时间", units="s")
        self.setLabel("left", "音高")
        self.setMouseEnabled(x=True, y=True)

        self._document: MidiDocument | None = None
        self._note_ticks: list[tuple[int, str]] = []
        self._bars = pg.BarGraphItem(
            x0=np.zeros(0),
            y=np.zeros(0),
            width=np.zeros(0),
            height=np.zeros(0),
            brush=pg.mkBrush(31, 119, 180, 200),
            pen=pg.mkPen("#1f77b4"),
        )
        self.addItem(self._bars)
        self.clear_notes()

    # ---------------------------------------------------------------- 查询
    @property
    def document(self) -> MidiDocument | None:
        """当前显示的文档（未打开 MIDI 时为 ``None``）。"""
        return self._document

    @property
    def note_count(self) -> int:
        """当前画出的音符条数量。"""
        return len(self._document.notes) if self._document is not None else 0

    @property
    def note_axis_labels(self) -> tuple[str, ...]:
        """左轴当前显示的音名刻度（供测试与排障读取，不依赖 pyqtgraph 内部字段）。"""
        return tuple(text for _value, text in self._note_ticks)

    # ---------------------------------------------------------------- 更新
    def set_document(self, document: MidiDocument) -> None:
        """画出整份 MIDI 的音符（一次性重建，MIDI 是静态数据）。"""
        self._document = document
        notes = document.notes
        if not notes:
            self.clear_notes()
            return

        duration = max(float(document.duration), 1e-6)
        min_width = max(duration * MIN_BAR_FRACTION, 1e-4)
        starts = np.array([item.start for item in notes], dtype=np.float64)
        pitches = np.array([item.note for item in notes], dtype=np.float64)
        widths = np.array([max(item.duration, min_width) for item in notes], dtype=np.float64)
        self._bars.setOpts(
            x0=starts,
            y=pitches,
            width=widths,
            height=np.full(pitches.shape, BAR_HEIGHT, dtype=np.float64),
        )

        low, high = document.note_range
        self.setYRange(low - 1.0, high + 1.0, padding=0.0)
        self.setXRange(0.0, duration, padding=0.01)
        self._apply_note_axis(low, high)
        self.setTitle(
            f"钢琴卷帘（MIDI 符号数据）：{document.note_count} 个音符 ｜ "
            f"{document.track_count} 轨 ｜ 音域 {note_label(low)}–{note_label(high)} ｜ "
            f"{duration:.2f} s"
        )

    def clear_notes(self) -> None:
        """清空音符条（打开音频文件或停止时调用）。

        注意：**不能命名为 ``clear``** —— 会与 ``PlotItem.clear`` 撞名后被静默遮蔽。
        """
        self._document = None
        self._note_ticks = []
        self._bars.setOpts(
            x0=np.zeros(0),
            y=np.zeros(0),
            width=np.zeros(0),
            height=np.zeros(0),
        )
        self.setTitle("钢琴卷帘（等待打开 MIDI 文件）")

    # ---------------------------------------------------------------- 内部
    def _apply_note_axis(self, low: int, high: int) -> None:
        """左轴按音名标注；音域很宽时只标每个八度的 C。"""
        span = int(high) - int(low)
        step = 1 if span <= FULL_LABEL_SEMITONES else 12
        ticks = [
            (pitch, note_label(pitch))
            for pitch in range(int(low), int(high) + 1)
            if step == 1 or pitch % 12 == 0
        ]
        if not ticks:
            ticks = [(int(low), note_label(low))]
        self._note_ticks = list(ticks)
        self.getAxis("left").setTicks([ticks])
