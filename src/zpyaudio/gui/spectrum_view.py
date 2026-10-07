"""频谱图（规格书 7.1 区域②）。

每帧（1/16 s）用一次 ``setData`` 增量更新，不重建曲线对象（规格书 7.2 绘图约束）。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt

from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.gui.log_axis import DecadeAxisItem

__all__ = ["SpectrumView"]

#: 纵轴下限（dBFS）
DB_FLOOR = -100.0


class SpectrumView(pg.PlotWidget):
    """显示当前帧单边幅度谱（dBFS），可切换线性 / 对数频率轴。"""

    def __init__(self, sample_rate: int, log_x: bool = False, parent=None) -> None:
        super().__init__(parent=parent)
        self._sample_rate = int(sample_rate)
        self._log_x = bool(log_x)
        self._linear_axis = pg.AxisItem("bottom")
        self._linear_axis.setLabel("频率", units="Hz")
        self._log_axis = DecadeAxisItem("bottom")
        self._log_axis.setLabel("频率", units="Hz")
        self.setBackground("w")
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("left", "幅度", units="dBFS")
        self.setYRange(DB_FLOOR, 5.0, padding=0.0)
        self._curve = self.plot(pen=pg.mkPen("#2ca02c", width=1))
        self._peak_line = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#d62728", width=1, style=Qt.PenStyle.DashLine),
        )
        self._peak_line.setVisible(False)
        self.addItem(self._peak_line)
        self._apply_log_mode()

    # ---------------------------------------------------------------- 配置
    @property
    def log_x(self) -> bool:
        """是否使用对数频率轴。"""
        return self._log_x

    def title_text(self) -> str:
        """当前标题文本（峰值 / 主频读数）。"""
        return self.plotItem.titleLabel.text

    def set_log_x(self, enabled: bool) -> None:
        """切换频率轴类型（规格书 7.2「线性/对数频率轴」）。"""
        self._log_x = bool(enabled)
        self._apply_log_mode()

    def set_sample_rate(self, sample_rate: int) -> None:
        """采样率变化后重设频率轴范围。"""
        self._sample_rate = int(sample_rate)
        self._apply_log_mode()

    def _apply_log_mode(self) -> None:
        """切换频率轴：对数模式下数据传 log10（见 gui/log_axis.py）。"""
        low = 20.0
        high = max(self._sample_rate / 2.0, low * 2.0)
        if self._log_x:
            self.setAxisItems({"bottom": self._log_axis})
            self.setXRange(float(np.log10(low)), float(np.log10(high)), padding=0.0)
        else:
            self.setAxisItems({"bottom": self._linear_axis})
            self.setXRange(low, high, padding=0.0)

    # ---------------------------------------------------------------- 更新
    def update_snapshot(self, snapshot: AnalysisSnapshot) -> None:
        """用一帧分析结果刷新频谱与峰值标注。"""
        freqs = np.asarray(snapshot.spectrum_freqs, dtype=np.float64)
        mag_db = np.asarray(snapshot.spectrum_db, dtype=np.float64)
        if freqs.size == 0 or freqs.size != mag_db.size:
            return
        if self._log_x:
            # 对数模式下数据传 log10（坐标轴负责十进制标签），且对数轴不接受 0，丢弃 DC
            mask = freqs > 0.0
            plot_freqs = np.log10(freqs[mask])
            plot_db = mag_db[mask]
            peak_x = float(np.log10(snapshot.peak_freq)) if snapshot.peak_freq > 0.0 else 0.0
        else:
            plot_freqs, plot_db = freqs, mag_db
            peak_x = float(snapshot.peak_freq)
        self._curve.setData(plot_freqs, plot_db)

        peak = float(snapshot.peak_freq)
        peak_db = float(mag_db[int(np.argmin(np.abs(freqs - peak)))])
        self._peak_line.setPos(peak_x)
        self._peak_line.setVisible(peak > 0.0)
        self.setTitle(
            f"峰值 {peak:.1f} Hz / {peak_db:.1f} dBFS"
            + (f"　|　主频 {snapshot.dominant_freq:.1f} Hz（置信度 {snapshot.confidence:.2f}）"
               if snapshot.has_pitch else "　|　主频 —")
        )
