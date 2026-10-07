"""波形时序图（规格书 7.1 区域①）。

只做绘图：数据由 GUI 定时器从环形缓冲取（规格书 5.2 硬性约束 1）。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg

from zpyaudio.core.resample import to_mono

__all__ = ["WaveformView"]


class WaveformView(pg.PlotWidget):
    """滚动显示最近 ``seconds`` 秒的波形。"""

    def __init__(self, sample_rate: int, seconds: float = 0.5, parent=None) -> None:
        super().__init__(parent=parent)
        self._sample_rate = int(sample_rate)
        self._autoscale = False
        self.setBackground("w")
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setLabel("bottom", "时间", units="s")
        self.setLabel("left", "幅度")
        self.setYRange(-1.05, 1.05, padding=0.0)
        self.setMouseEnabled(x=True, y=False)
        self._curve = self.plot(pen=pg.mkPen("#1f77b4", width=1))
        self._curve.setClipToView(True)
        self._curve.setDownsampling(auto=True, method="peak")
        self._max_samples = 2
        self._time_axis = np.zeros(2, dtype=np.float32)
        self.set_seconds(seconds)

    # ---------------------------------------------------------------- 配置
    @property
    def seconds(self) -> float:
        """当前显示时间窗（秒）。"""
        return self._max_samples / float(self._sample_rate)

    def set_seconds(self, seconds: float) -> None:
        """设置显示时间窗（FR-2.1：0.1/0.25/0.5/1/2 s）。"""
        seconds = float(seconds)
        if seconds <= 0.0:
            raise ValueError(f"seconds 必须为正，收到 {seconds}")
        self._max_samples = max(int(seconds * self._sample_rate), 2)
        # x 轴相对「现在」：最新样本在 0，历史为负
        self._time_axis = (
            np.arange(self._max_samples, dtype=np.float32) - (self._max_samples - 1)
        ) / np.float32(self._sample_rate)
        self.setXRange(-self.seconds, 0.0, padding=0.0)

    def set_sample_rate(self, sample_rate: int) -> None:
        """采样率变化后重建时间轴。"""
        seconds = self.seconds
        self._sample_rate = int(sample_rate)
        self.set_seconds(seconds)

    def set_autoscale(self, enabled: bool) -> None:
        """自动量程（规格书 7.2 波形区控件）。

        关闭时固定 ``±1.05``（满量程），开启时纵轴跟随数据自动缩放。
        """
        self._autoscale = bool(enabled)
        if self._autoscale:
            self.enableAutoRange(y=True)
        else:
            # 注意：ViewBox.disableAutoRange() 不接受 x=/y= 关键字（pyqtgraph 0.14）
            self.disableAutoRange()
            self.setXRange(-self.seconds, 0.0, padding=0.0)
            self.setYRange(-1.05, 1.05, padding=0.0)

    @property
    def autoscale(self) -> bool:
        """是否启用纵轴自动量程。"""
        return self._autoscale

    # ---------------------------------------------------------------- 更新
    def update_from_ring(self, ring) -> bool:
        """从环形缓冲取最新数据并刷新；数据不足返回 ``False``。"""
        data = ring.read_latest(self._max_samples)
        if data is None:
            return False
        self.update_samples(to_mono(data))
        return True

    def update_samples(self, samples: np.ndarray) -> None:
        """直接刷新给定样本（自动对齐到时间窗右端）。"""
        mono = np.asarray(samples, dtype=np.float32).reshape(-1)
        n = min(mono.size, self._max_samples)
        if n == 0:
            return
        self._curve.setData(self._time_axis[-n:], mono[-n:])
