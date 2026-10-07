"""统一音频数据流与单帧分析快照（规格书 4.1 / 4.4）。

字段约定（规格书 4.1）：

* ``samples`` 一律 ``float32``、C 连续，``(n,)`` 单声道或 ``(n, channels)``；
* 值域 ``[-1.0, 1.0]``，超出即裁剪，不做自动增益；
* ``timestamp`` 为相对数据流起点的**首样本**时刻（秒），由样本计数推导。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["AudioFrame", "AnalysisSnapshot", "MAX_CHANNELS"]

#: 本期声道数上限（规格书 4.1）
MAX_CHANNELS = 2


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """一段带时间戳的音频数据。"""

    samples: np.ndarray
    sample_rate: int
    channels: int
    timestamp: float

    def __post_init__(self) -> None:
        samples = np.array(self.samples, dtype=np.float32, order="C", copy=True)
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        if samples.ndim != 2:
            raise ValueError(f"samples 必须是一维或二维，收到 ndim={samples.ndim}")
        if not 1 <= self.channels <= MAX_CHANNELS:
            raise ValueError(f"channels 必须在 1..{MAX_CHANNELS}，收到 {self.channels}")
        if samples.shape[1] != self.channels:
            raise ValueError(
                f"samples 列数 {samples.shape[1]} 与 channels={self.channels} 不一致"
            )
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正，收到 {self.sample_rate}")
        if not np.isfinite(self.timestamp):
            raise ValueError(f"timestamp 必须有限，收到 {self.timestamp}")
        np.clip(samples, -1.0, 1.0, out=samples)
        object.__setattr__(self, "samples", samples)

    @property
    def frames(self) -> int:
        """样本数（每声道）。"""
        return int(self.samples.shape[0])

    @property
    def duration(self) -> float:
        """时长（秒）。"""
        return self.frames / float(self.sample_rate)

    @property
    def end_timestamp(self) -> float:
        """末样本之后的时刻（秒）。"""
        return self.timestamp + self.duration

    def mono(self) -> np.ndarray:
        """降混为单声道（规格书 4.2 第 3 条）。"""
        if self.channels == 1:
            return self.samples[:, 0]
        return self.samples.mean(axis=1, dtype=np.float32)


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """一次分析（1/16 s 节拍）的输出（规格书 4.4）。"""

    t: float
    spectrum_freqs: np.ndarray
    spectrum_db: np.ndarray
    peak_freq: float
    dominant_freq: float
    confidence: float
    rms_db: float
    method: str = "fft_parabolic"

    @property
    def has_pitch(self) -> bool:
        """是否存在有效主频（静音/置信度为 0 时为 False，规格书 FR-2.6）。"""
        return self.dominant_freq > 0.0 and self.confidence > 0.0
