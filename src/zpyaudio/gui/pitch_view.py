"""主频时序图（规格书 7.1 区域③、7.2、4.4）。

* 生产路径由 :meth:`PitchView.update_points` 驱动：数据来自分析线程收集的
  :class:`~zpyaudio.core.pitch_series.PitchSeries`，与右侧数据区、CSV 同源；
* 横轴为相对时间（最近 N 秒，默认 10 s，可选 5/10/30/60 s）；
* 纵轴为**对数**频率轴（pyqtgraph 对数模式要求传入 log10 值，轴标签自动还原为 Hz）；
* 点颜色透明度随置信度变化，可疑跳变点用红色空心圈标出；
* 无有效主频的帧插入 NaN 断线，避免"静音段被连成直线"；
* 标题同时给出主频与音名（v0.5 变更 1），低频段音名带 ``*`` 提示。

符号数据模式（M5 / FR-4.3、FR-4.4）
----------------------------------
打开 MIDI 时**复用本区域**显示"音符频率曲线"（:meth:`show_symbolic`）：

* 曲线是**符号数据**（每个音符一条等频横线，频率取自 ``core.notes.note_frequency``），
  与麦克风/文件分析出来的**声学主频**是两回事，因此标题会明确标注"符号数据"；
* 两种模式互斥：进入符号模式时隐藏声学曲线/散点；一旦又有声学帧进来
  （说明开始了新的采集或播放），自动退回声学模式，避免两套语义混在一张图里；
* 横轴在符号模式下是**绝对时间 0…曲长**（不是"最近 N 秒"）。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush

from zpyaudio.app.controller import HOP_SECONDS
from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.notes import format_note, freq_to_note
from zpyaudio.core.pitch_series import SeriesPoint
from zpyaudio.core.pitch_track import PitchTrack
from zpyaudio.gui.log_axis import DecadeAxisItem

__all__ = ["PitchView", "DEFAULT_PITCH_SECONDS", "FREQ_MIN", "FREQ_MAX", "SYMBOLIC_LABEL"]

#: 主频视图默认时间窗（规格书 7.2：默认 10 s）
DEFAULT_PITCH_SECONDS = 10.0

#: 频率显示范围（Hz）
FREQ_MIN = 50.0
FREQ_MAX = 5000.0

#: 符号数据模式在标题里的固定标识（FR-4.4：必须能与声学主频区分开）
SYMBOLIC_LABEL = "符号数据"

#: 置信度调色板档数（避免每帧创建大量 QBrush）
_CONFIDENCE_LEVELS = 8


class PitchView(pg.PlotWidget):
    """主频时序图。数据由 GUI 定时器从控制器快照队列推入。"""

    def __init__(
        self,
        seconds: float = DEFAULT_PITCH_SECONDS,
        *,
        fmin: float = FREQ_MIN,
        fmax: float = FREQ_MAX,
        smoothing: bool = True,
        hop_seconds: float = HOP_SECONDS,
        parent=None,
    ) -> None:
        super().__init__(parent=parent, axisItems={"left": DecadeAxisItem("left")})
        self._hop_seconds = float(hop_seconds)
        self._fmin = float(fmin)
        self._fmax = float(fmax)
        self._smoothing = bool(smoothing)
        self._track = PitchTrack()
        # (t 绝对时刻, freq 或 NaN, confidence, suspect)
        self._points: deque[tuple[float, float, float, bool]] = deque()
        #: 最近绘制的分析帧序号（含静音帧），用于按序列点绘制时补断点
        self._last_frame_index: int | None = None
        self._seconds = float(seconds)

        self.setBackground("w")
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("bottom", "时间", units="s")
        self.setLabel("left", "频率", units="Hz")
        # 纵轴为对数：数据统一传 log10 值，坐标轴负责还原十进制标签（见 gui/log_axis.py）
        self.setYRange(float(np.log10(self._fmin)), float(np.log10(self._fmax)), padding=0.0)

        self._curve = self.plot(pen=pg.mkPen("#1f77b4", width=1), connect="finite")
        self._brushes: list[QBrush] = [
            pg.mkBrush(31, 119, 180, 60 + int(195 * i / (_CONFIDENCE_LEVELS - 1)))
            for i in range(_CONFIDENCE_LEVELS)
        ]
        self._points_item = pg.ScatterPlotItem(size=5, pen=None)
        self.addItem(self._points_item)
        self._suspect_item = pg.ScatterPlotItem(
            size=11,
            pen=pg.mkPen("#d62728", width=2),
            brush=pg.mkBrush(255, 255, 255, 0),
            symbol="o",
        )
        self.addItem(self._suspect_item)

        # 符号数据（MIDI 音符频率曲线）：平时隐藏，与声学曲线互斥（FR-4.4）
        self._symbolic_curve = self.plot(pen=pg.mkPen("#d62728", width=1.6), connect="finite")
        self._symbolic_curve.setVisible(False)
        self._symbolic = False
        self._symbolic_duration = 0.0
        self._symbolic_notes = 0

        self.set_seconds(self._seconds)

    # ---------------------------------------------------------------- 配置
    @property
    def seconds(self) -> float:
        """当前时间窗（秒）。"""
        return self._seconds

    @property
    def smoothing(self) -> bool:
        """是否启用 3 点中值滤波。"""
        return self._smoothing

    @property
    def point_count(self) -> int:
        """当前窗口内的点数（含断点占位）。"""
        return len(self._points)

    @property
    def last_freq(self) -> float:
        """最近一个有效主频（Hz），无数据时为 0。"""
        for _, freq, _, _ in reversed(self._points):
            if freq > 0.0 and np.isfinite(freq):
                return float(freq)
        return 0.0

    @property
    def symbolic(self) -> bool:
        """当前是否处于符号数据模式（显示 MIDI 音符频率曲线，FR-4.4）。"""
        return self._symbolic

    @property
    def symbolic_note_count(self) -> int:
        """符号模式下画出的音符数（非符号模式恒为 0）。"""
        return self._symbolic_notes if self._symbolic else 0

    def set_seconds(self, seconds: float) -> None:
        """设置时间窗（保留窗口内已有数据）。"""
        seconds = float(seconds)
        if seconds <= 0.0:
            raise ValueError(f"seconds 必须为正，收到 {seconds}")
        self._seconds = seconds
        capacity = int(seconds / self._hop_seconds) + 2
        kept = list(self._points)[-capacity:]
        self._points = deque(kept, maxlen=capacity)
        self._apply_x_range()
        self._redraw()

    def set_smoothing(self, enabled: bool) -> None:
        """开关中值滤波（开关时清空历史，避免新旧口径混在同一张图里）。"""
        enabled = bool(enabled)
        if enabled == self._smoothing:
            return
        self._smoothing = enabled
        self._track.reset()

    def reset_track(self) -> None:
        """清空轨迹（停止/切换数据源时调用）。

        注意：**不能命名为 ``clear``**。``pg.PlotWidget.__getattr__`` 会把
        ``PlotItem`` 的同名成员转发出去，子类同名方法会被静默遮蔽（实测
        ``PitchView.clear`` 实际调到了 ``PlotItem.clear``，导致数据没被清空）。
        """
        self._points.clear()
        self._track.reset()
        self._last_frame_index = None
        self._curve.setData([], [])
        self._points_item.setData([])
        self._suspect_item.setData([])

    # ---------------------------------------------------------------- 符号数据
    def show_symbolic(
        self,
        notes,
        *,
        source_label: str = "MIDI",
        duration: float | None = None,
    ) -> int:
        """在主频区域画出 MIDI 的**音符频率曲线**（FR-4.3 / FR-4.4）。

        每个音符画成一段等频横线：``(start, f) → (end, f)``，音符之间用 NaN 断开，
        所以图上看到的是"阶梯"而不是一条连起来的折线。

        Args:
            notes: :class:`~zpyaudio.media.midi_loader.NoteEvent` 序列。
            source_label: 标题里的来源标识（默认 ``MIDI``）。
            duration: 横轴跨度（秒）；``None`` 时取最后一个音符的结束时刻。

        Returns:
            实际画出的音符数。
        """
        ordered = sorted(notes, key=lambda item: (item.start, item.note))
        self._enter_symbolic()

        xs: list[float] = []
        ys: list[float] = []
        freqs: list[float] = []
        for item in ordered:
            freq = float(item.frequency)
            if freq <= 0.0 or not math.isfinite(freq):
                continue
            value = math.log10(freq)
            end = max(float(item.end), float(item.start))
            xs.extend((float(item.start), end, math.nan))
            ys.extend((value, value, math.nan))
            freqs.append(freq)

        self._symbolic_curve.setData(xs, ys)
        self._symbolic_notes = len(freqs)
        span = float(duration) if duration is not None else max(
            (float(item.end) for item in ordered), default=0.0
        )
        self._symbolic_duration = max(span, 1e-3)
        self._apply_x_range()
        if freqs:
            low = math.log10(min(freqs))
            high = math.log10(max(freqs))
            margin = max((high - low) * 0.08, 0.02)
            self.setYRange(low - margin, high + margin, padding=0.0)
        self.setTitle(
            f"{SYMBOLIC_LABEL}（{source_label} 音符频率曲线）：{self._symbolic_notes} 个音符 ｜ "
            f"f = 440×2^((n-69)/12) ｜ 这是符号数据，不是声学主频"
        )
        return self._symbolic_notes

    def clear_symbolic(self) -> None:
        """退出符号数据模式，恢复声学主频曲线。"""
        if not self._symbolic:
            return
        self._symbolic = False
        self._symbolic_notes = 0
        self._symbolic_duration = 0.0
        self._symbolic_curve.setData([], [])
        self._symbolic_curve.setVisible(False)
        self._curve.setVisible(True)
        self._points_item.setVisible(True)
        self._suspect_item.setVisible(True)
        self._apply_x_range()
        self.setTitle("主频 —")
        self._redraw()

    # ---------------------------------------------------------------- 更新
    def update_snapshot(self, snapshot: AnalysisSnapshot) -> None:
        """推入一帧分析结果并重绘（单帧路径，测试与嵌入式场景使用）。"""
        if self._symbolic:
            # 有声学帧进来 = 开始了新的采集/播放 → 自动退回声学模式（避免两套语义混画）
            self.clear_symbolic()
        point = self._track.push(snapshot)
        if point is None:
            self._append(snapshot.t, float("nan"), 0.0, False)
        elif self._smoothing:
            self._append(point.t, point.freq, point.confidence, point.suspect)
        else:
            self._append(point.t, point.raw_freq, point.confidence, False)
        self._redraw()

    def update_points(
        self,
        points: Sequence[SeriesPoint],
        *,
        total_frames: int | None = None,
    ) -> None:
        """按主频时序序列增量绘制（生产路径，v0.5 变更 2）。

        与数据区、CSV 共用分析线程收集的同一份序列，保证图上的读数一致；
        ``total_frames`` 为序列已推入的分析帧总数（含静音帧），多于最后绘制的
        帧序号时在末尾补一个断点，使静音期间曲线断开（FR-2.6 / A6）。
        """
        if not points and total_frames is None:
            return
        if self._symbolic:
            self.clear_symbolic()
        last_frame = self._last_frame_index
        for item in points:
            if last_frame is not None and item.frame_index > last_frame + 1:
                self._append_gap(item.t)
            freq = item.display_freq(smoothing=self._smoothing)
            suspect = item.suspect if self._smoothing else False
            self._append(item.t, freq, item.confidence, suspect)
            last_frame = item.frame_index
        if total_frames is not None and last_frame is not None and total_frames > last_frame:
            self._append_gap(self._points[-1][0] if self._points else 0.0)
            last_frame = int(total_frames)
        self._last_frame_index = last_frame
        self._redraw()

    # ---------------------------------------------------------------- 内部
    def _enter_symbolic(self) -> None:
        """切到符号模式：清掉声学轨迹并隐藏声学图元（两种语义互斥）。"""
        self._symbolic = True
        self._points.clear()
        self._track.reset()
        self._last_frame_index = None
        self._curve.setData([], [])
        self._points_item.setData([])
        self._suspect_item.setData([])
        self._curve.setVisible(False)
        self._points_item.setVisible(False)
        self._suspect_item.setVisible(False)
        self._symbolic_curve.setVisible(True)

    def _apply_x_range(self) -> None:
        """按当前模式设置横轴：声学是"最近 N 秒"，符号数据是"0…曲长"（FR-4.4）。"""
        if self._symbolic:
            self.setXRange(0.0, max(self._symbolic_duration, 1e-3), padding=0.01)
        else:
            self.setXRange(-self._seconds, 0.0, padding=0.0)

    def _append_gap(self, t: float) -> None:
        """插入 NaN 断点（静音或帧序号跳变）。"""
        self._append(float(t), float("nan"), 0.0, False)

    def _append(self, t: float, freq: float, confidence: float, suspect: bool) -> None:
        self._points.append((float(t), float(freq), float(confidence), bool(suspect)))

    def _redraw(self) -> None:
        if self._symbolic:
            return
        if not self._points:
            return
        latest = self._points[-1][0]
        xs: list[float] = []
        ys: list[float] = []
        sx: list[float] = []
        sy: list[float] = []
        brushes: list[QBrush] = []
        last_freq = 0.0
        last_confidence = 0.0
        for t, freq, confidence, _suspect in self._points:
            x = t - latest
            xs.append(x)
            if freq > 0.0 and np.isfinite(freq):
                level = int(round(min(max(confidence, 0.0), 1.0) * (_CONFIDENCE_LEVELS - 1)))
                y = float(np.log10(freq))
                ys.append(y)
                sx.append(x)
                sy.append(y)
                brushes.append(self._brushes[level])
                last_freq, last_confidence = freq, confidence
            else:
                ys.append(float("nan"))
        self._curve.setData(xs, ys)
        self._points_item.setData(sx, sy, brush=brushes)
        suspects = [
            (t - latest, float(np.log10(freq)))
            for t, freq, _, suspect in self._points
            if suspect and freq > 0.0 and np.isfinite(freq)
        ]
        self._suspect_item.setData(
            [item[0] for item in suspects], [item[1] for item in suspects]
        )
        title = "主频 —"
        if last_freq > 0.0:
            info = freq_to_note(last_freq)
            note_text = ""
            if info is not None:
                note_text = f" ＝ {format_note(info, low_band_hint='mark' if info.low_band else None)}"
            title = f"主频 {last_freq:.1f} Hz{note_text}（置信度 {last_confidence:.2f}）"
            if self._smoothing:
                title += " ｜ 3 点中值滤波"
            if suspects:
                title += f" ｜ 可疑跳变 {len(suspects)}"
        self.setTitle(title)
