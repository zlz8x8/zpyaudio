"""加窗 + FFT + 谱峰 + 主频（规格书 4.3 / 4.4）。

本模块不依赖 GUI，可在分析线程中直接调用（规格书 5.1）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.signal import get_window

from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.pitch import PITCH_METHODS, estimate_from_spectrum, estimate_yin

__all__ = ["AnalyzerConfig", "Analyzer", "WINDOW_FUNCTIONS", "FFT_SIZES"]

logger = logging.getLogger(__name__)

#: 可选窗函数（规格书 FR-2.5）
WINDOW_FUNCTIONS: tuple[str, ...] = ("hann", "hamming", "blackman")

#: 可选 FFT 长度（规格书 FR-2.5）
FFT_SIZES: tuple[int, ...] = (1024, 2048, 4096)

_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class AnalyzerConfig:
    """分析参数（默认值取自规格书 4.3 / 4.4）。"""

    sample_rate: int = 48_000
    window_size: int = 4096
    window: str = "hann"
    zero_pad: int = 1
    pitch_method: str = "fft_parabolic"
    fmin: float = 50.0
    fmax: float = 5000.0
    rms_gate_db: float = -60.0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正，收到 {self.sample_rate}")
        if self.window_size <= 0:
            raise ValueError(f"window_size 必须为正，收到 {self.window_size}")
        if self.window not in WINDOW_FUNCTIONS:
            raise ValueError(f"不支持的窗函数 {self.window!r}，可选：{', '.join(WINDOW_FUNCTIONS)}")
        if self.zero_pad not in (1, 2):
            raise ValueError(f"zero_pad 只支持 1 或 2，收到 {self.zero_pad}")
        if self.pitch_method not in PITCH_METHODS:
            raise ValueError(
                f"不支持的主频算法 {self.pitch_method!r}，可选：{', '.join(PITCH_METHODS)}"
            )
        if not 0.0 < self.fmin < self.fmax:
            raise ValueError(f"要求 0 < fmin < fmax，收到 fmin={self.fmin}, fmax={self.fmax}")

    @property
    def nfft(self) -> int:
        """实际 FFT 点数（补零后）。"""
        return self.window_size * self.zero_pad

    @property
    def bin_hz(self) -> float:
        """频率分辨率 Δf = fs / N。"""
        return self.sample_rate / self.nfft

    @property
    def window_seconds(self) -> float:
        """窗长时间（秒）。"""
        return self.window_size / self.sample_rate

    @property
    def effective_fmax(self) -> float:
        """主频搜索上限：min(fmax, 0.45 * fs)（规格书 4.4）。"""
        return float(min(self.fmax, 0.45 * self.sample_rate, 0.5 * self.sample_rate))


class Analyzer:
    """一次分析 = 加窗 → rFFT → 单边幅度（dBFS）→ 谱峰 / 主频。"""

    def __init__(self, config: AnalyzerConfig | None = None) -> None:
        self.config = config or AnalyzerConfig()
        cfg = self.config
        self._window = get_window(cfg.window, cfg.window_size, fftbins=True).astype(np.float64)
        coherent_gain = float(self._window.sum())
        if coherent_gain <= 0.0:
            raise ValueError("窗函数相干增益异常，请检查 window 配置")
        self._coherent_gain = coherent_gain
        self._freqs = np.fft.rfftfreq(cfg.nfft, 1.0 / cfg.sample_rate)
        # 单边幅度归一：正弦幅度 A 对应 0 dBFS 峰值（DC 与 Nyquist 不加倍）
        scale = np.full(self._freqs.size, 2.0 / coherent_gain, dtype=np.float64)
        scale[0] = 1.0 / coherent_gain
        if cfg.nfft % 2 == 0:
            scale[-1] = 1.0 / coherent_gain
        self._scale = scale

    # ---------------------------------------------------------------- 属性
    @property
    def frame_samples(self) -> int:
        """一次分析需要的样本数（= 窗长 = hop 采样数来源）。"""
        return self.config.window_size

    @property
    def bin_hz(self) -> float:
        """频率分辨率（Hz）。"""
        return self.config.bin_hz

    @property
    def freqs(self) -> np.ndarray:
        """频率轴（只读）。"""
        out = self._freqs.copy()
        out.setflags(write=False)
        return out

    # ---------------------------------------------------------------- 计算
    @staticmethod
    def rms_db(block: np.ndarray) -> float:
        """样本的 RMS 电平（dBFS），用于静音门限（规格书 4.4）。"""
        x = np.asarray(block, dtype=np.float64).reshape(-1)
        if x.size == 0:
            return -np.inf
        rms = float(np.sqrt(np.mean(x * x)))
        return float(20.0 * np.log10(max(rms, _EPS)))

    def _prepare(self, block: np.ndarray) -> np.ndarray:
        """规整为长度 = 窗长的 float64 一维数组；不足补零，超出取最新。"""
        x = np.asarray(block, dtype=np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1)
        x = x.reshape(-1)
        n = self.config.window_size
        if x.size > n:
            logger.debug("分析块长 %d > 窗长 %d，取最新 %d 个样本", x.size, n, n)
            x = x[-n:]
        elif x.size < n:
            x = np.pad(x, (0, n - x.size))
        return x

    def spectrum(self, block: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回 ``(freqs, mag_db)``，单边幅度谱，单位 dBFS。"""
        x = self._prepare(block)
        spectrum = np.fft.rfft(x * self._window, n=self.config.nfft)
        mag = np.abs(spectrum) * self._scale
        return self._freqs, 20.0 * np.log10(np.maximum(mag, _EPS))

    def analyze(self, block: np.ndarray, *, t: float = 0.0, is_final: bool = False) -> AnalysisSnapshot:
        """产出一次分析快照。

        ``is_final`` 仅用于标记数据流末块（此时块内不足部分按 0 补齐，规格书 4.1）。
        """
        x = self._prepare(block)
        rms_db = self.rms_db(x)
        freqs, mag_db = self.spectrum(x)

        if mag_db.size > 1:
            peak_idx = int(np.argmax(mag_db[1:])) + 1
        else:
            peak_idx = 0
        peak_freq = float(freqs[peak_idx])

        cfg = self.config
        if rms_db < cfg.rms_gate_db:
            # 静音：不产生虚假主频（验收项 A6 / FR-2.6）
            dominant_freq, confidence, method = 0.0, 0.0, cfg.pitch_method
        elif cfg.pitch_method == "yin":
            # P3 是时域算法，直接用波形（规格书 4.4）
            estimate = estimate_yin(
                x, cfg.sample_rate, fmin=cfg.fmin, fmax=cfg.effective_fmax
            )
            dominant_freq, confidence, method = estimate.freq, estimate.confidence, estimate.method
        else:
            # P1 / P2 作用在频谱上
            estimate = estimate_from_spectrum(
                freqs,
                mag_db,
                method=cfg.pitch_method,
                fmin=cfg.fmin,
                fmax=cfg.effective_fmax,
            )
            dominant_freq, confidence, method = estimate.freq, estimate.confidence, estimate.method

        return AnalysisSnapshot(
            t=float(t),
            spectrum_freqs=freqs,
            spectrum_db=mag_db,
            peak_freq=peak_freq,
            dominant_freq=dominant_freq,
            confidence=confidence,
            rms_db=rms_db,
            method=method,
        )
