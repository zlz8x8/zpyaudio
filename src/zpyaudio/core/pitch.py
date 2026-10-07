"""主频（dominant frequency）估计（规格书 4.4）。

档位
----
P1 ``fft_parabolic``
    频谱峰值 + 对数幅度抛物线插值。**默认**。
    因 Δf = fs/N = 11.7 Hz（48 kHz / 4096）粗于 ±5 Hz 的主频精度要求，
    必须做谱线插值（规格书 4.3）。
P2 ``hps``
    谐波积谱（Harmonic Product Spectrum）：把频谱按倍数压缩相乘，
    抑制"最强泛音被误判为基频"，适合含强泛音的单音（人声、弦乐）。
P3 ``yin``
    时域自相关 / YIN，用累积均值归一化差分函数找周期，
    低频与低信噪比下明显优于谱峰法（同窗长下不受 Δf 限制）。

统一入口
--------
``fft_parabolic`` 与 ``hps`` 作用在频谱上，用 :func:`estimate_from_spectrum`；
``yin`` 作用在时域波形上，用 :func:`estimate_yin`。
:class:`~zpyaudio.core.analyzer.Analyzer` 按配置自动选择。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

__all__ = [
    "PITCH_METHODS",
    "PitchMethod",
    "PitchEstimate",
    "parabolic_offset",
    "estimate_from_spectrum",
    "estimate_pitch",
    "estimate_yin",
]

PitchMethod = Literal["fft_parabolic", "hps", "yin"]

#: 可选主频算法（与规格书 4.4 档位表一致）
PITCH_METHODS: tuple[str, ...] = ("fft_parabolic", "hps", "yin")

#: 置信度归一化跨度：峰值高出谱底噪该值即为 1.0
CONFIDENCE_SPAN_DB = 40.0

#: HPS 参与相乘的谐波个数（规格书 4.4：2–5 次谐波）
HPS_HARMONICS = 5

#: HPS 基频门限：候选基频必须高于（全局峰值 − 该值）才被考虑，
#: 否则纯音的次谐波会与真实基频并列（HPS 的经典退化情形）
HPS_FUNDAMENTAL_GATE_DB = 35.0

#: YIN 绝对阈值（规格书 4.4：自相关 / YIN）
YIN_THRESHOLD = 0.15


@dataclass(frozen=True, slots=True)
class PitchEstimate:
    """主频估计结果。``freq == 0`` 表示无有效主频。"""

    freq: float
    confidence: float
    method: str


def parabolic_offset(y_left: float, y_mid: float, y_right: float) -> float:
    """三点抛物线插值的峰值偏移量，单位为 bin（或采样点），范围 ``[-0.5, 0.5]``。

    对极大值与极小值同样适用（差分为负/正时公式一致）。
    """
    denom = y_left - 2.0 * y_mid + y_right
    if abs(denom) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (y_left - y_right) / denom, -0.5, 0.5))


# --------------------------------------------------------------------- 频谱法
def estimate_from_spectrum(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    *,
    method: str = "fft_parabolic",
    fmin: float = 50.0,
    fmax: float = 5000.0,
    conf_span_db: float = CONFIDENCE_SPAN_DB,
) -> PitchEstimate:
    """在受限频段内用频谱法估计主频（``mag_db`` 为单边幅度谱 dBFS）。"""
    if method == "fft_parabolic":
        return _fft_parabolic(freqs, mag_db, fmin=fmin, fmax=fmax, conf_span_db=conf_span_db)
    if method == "hps":
        return _hps(freqs, mag_db, fmin=fmin, fmax=fmax, conf_span_db=conf_span_db)
    if method == "yin":
        raise ValueError("YIN 是时域算法，请改用 estimate_yin(block, sample_rate, ...)")
    raise ValueError(f"未知主频算法 {method!r}，可选：{', '.join(PITCH_METHODS)}")


def estimate_pitch(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    *,
    method: str = "fft_parabolic",
    fmin: float = 50.0,
    fmax: float = 5000.0,
    conf_span_db: float = CONFIDENCE_SPAN_DB,
) -> PitchEstimate:
    """:func:`estimate_from_spectrum` 的兼容别名（P1/P2）。"""
    return estimate_from_spectrum(
        freqs, mag_db, method=method, fmin=fmin, fmax=fmax, conf_span_db=conf_span_db
    )


def _band_and_bin_hz(
    freqs: np.ndarray, mag_db: np.ndarray, fmin: float, fmax: float
) -> tuple[np.ndarray, float]:
    if freqs.size != mag_db.size:
        raise ValueError("freqs 与 mag_db 长度不一致")
    if freqs.size < 3:
        return np.empty(0, dtype=int), 0.0
    band = np.flatnonzero((freqs >= fmin) & (freqs <= fmax))
    return band, float(freqs[1] - freqs[0])


def _confidence(peak_db: float, mag_db: np.ndarray, conf_span_db: float) -> float:
    """峰值与谱底噪中位数之差归一化（规格书 4.4）。"""
    floor_db = float(np.median(mag_db))
    return float(np.clip((peak_db - floor_db) / conf_span_db, 0.0, 1.0))


def _fft_parabolic(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    *,
    fmin: float,
    fmax: float,
    conf_span_db: float,
) -> PitchEstimate:
    method = "fft_parabolic"
    band, bin_hz = _band_and_bin_hz(freqs, mag_db, fmin, fmax)
    if band.size == 0:
        return PitchEstimate(0.0, 0.0, method)

    peak = int(band[int(np.argmax(mag_db[band]))])
    if 0 < peak < mag_db.size - 1:
        offset = parabolic_offset(
            float(mag_db[peak - 1]), float(mag_db[peak]), float(mag_db[peak + 1])
        )
        freq = float(freqs[peak]) + offset * bin_hz
    else:
        freq = float(freqs[peak])
    return PitchEstimate(
        freq=max(freq, 0.0),
        confidence=_confidence(float(mag_db[peak]), mag_db, conf_span_db),
        method=method,
    )


def _hps(
    freqs: np.ndarray,
    mag_db: np.ndarray,
    *,
    fmin: float,
    fmax: float,
    conf_span_db: float,
    harmonics: int = HPS_HARMONICS,
    fundamental_gate_db: float = HPS_FUNDAMENTAL_GATE_DB,
) -> PitchEstimate:
    """谐波积谱：score(f0) = Σ_h 20log10|X(h·f0)|。

    等价于线性幅度域相乘（对数域相加），并对**候选基频自身**设门限，
    避免纯音的次谐波与真实基频并列。
    """
    method = "hps"
    band, bin_hz = _band_and_bin_hz(freqs, mag_db, fmin, fmax)
    if band.size == 0:
        return PitchEstimate(0.0, 0.0, method)

    size = mag_db.size
    floor = float(np.min(mag_db)) - 10.0
    peak_db = float(np.max(mag_db))
    gate = peak_db - fundamental_gate_db

    scores = np.full(size, floor * harmonics, dtype=np.float64)
    for h in range(1, harmonics + 1):
        idx = np.rint(band * h).astype(int)
        valid = idx <= size - 1
        scores[band] += np.where(valid, mag_db[np.clip(idx, 0, size - 1)], floor)

    # 候选基频自身必须有能量（否则纯音的 h 次谐波会伪装成基频）
    candidates = band[mag_db[band] >= gate]
    if candidates.size == 0:
        return PitchEstimate(0.0, 0.0, method)

    peak = int(candidates[int(np.argmax(scores[candidates]))])
    if 0 < peak < size - 1:
        offset = parabolic_offset(
            float(scores[peak - 1]), float(scores[peak]), float(scores[peak + 1])
        )
        freq = float(freqs[peak]) + offset * bin_hz
    else:
        freq = float(freqs[peak])

    floor_score = float(np.median(scores[candidates]))
    confidence = float(np.clip((float(scores[peak]) - floor_score) / conf_span_db, 0.0, 1.0))
    return PitchEstimate(freq=max(freq, 0.0), confidence=confidence, method=method)


# --------------------------------------------------------------------- 时域法
def estimate_yin(
    block: np.ndarray,
    sample_rate: int,
    *,
    fmin: float = 50.0,
    fmax: float = 5000.0,
    threshold: float = YIN_THRESHOLD,
) -> PitchEstimate:
    """YIN 主频估计（规格书 4.4 P3）。

    步骤：差分函数 → 累积均值归一化（CMND）→ 绝对阈值 + 局部极小 → 抛物线插值。
    差分函数用「能量前缀和 + 互相关」计算，避免逐 lag 双重循环。
    """
    method = "yin"
    x = np.asarray(block, dtype=np.float64).reshape(-1)
    if x.ndim != 1:  # pragma: no cover - reshape 后必然一维
        x = x.reshape(-1)
    n = x.size
    if n < 64 or sample_rate <= 0:
        return PitchEstimate(0.0, 0.0, method)

    tau_min = max(2, int(np.floor(sample_rate / float(fmax))))
    tau_max = int(np.ceil(sample_rate / float(fmin))) + 1
    if tau_max >= n - 16:
        tau_max = max(tau_min + 1, n // 2)
    if tau_max <= tau_min:
        return PitchEstimate(0.0, 0.0, method)

    window = n - tau_max
    x = x - float(np.mean(x))

    # d(tau) = Σ_{j<W} (x[j] - x[j+tau])² = e1 + e2(tau) - 2·R(tau)
    # numpy.correlate(a, v) 的定义是 c[k] = Σ_n a[n+k]·v[n]，
    # 因此长数组必须放在第一个参数，得到 R(tau) = Σ_{j<W} x[j]·x[j+tau]
    correlation = np.correlate(x, x[:window], mode="valid")  # 长度 n-W+1 = tau_max+1
    squared = np.concatenate([[0.0], np.cumsum(x * x)])
    taus = np.arange(correlation.size)
    e1 = squared[window]
    e2 = squared[taus + window] - squared[taus]
    difference = np.maximum(e1 + e2 - 2.0 * correlation, 0.0)

    # 累积均值归一化差分
    dprime = np.ones_like(difference)
    running = np.cumsum(difference[1:])
    with np.errstate(divide="ignore", invalid="ignore"):
        dprime[1:] = difference[1:] * np.arange(1, difference.size) / running
    dprime[0] = 1.0
    dprime = np.nan_to_num(dprime, nan=1.0, posinf=1.0)

    lo = tau_min
    hi = min(tau_max, dprime.size - 2)
    if hi <= lo:
        return PitchEstimate(0.0, 0.0, method)

    # YIN 搜索：找到第一个低于绝对阈值的 tau，然后继续下降到局部极小
    # （只判断「小于前一点」会停在下降沿上，导致主频偏高约 10%）
    chosen = 0
    for tau in range(lo, hi + 1):
        if dprime[tau] < threshold:
            while tau + 1 <= hi and dprime[tau + 1] < dprime[tau]:
                tau += 1
            chosen = tau
            break
    if chosen == 0:
        chosen = lo + int(np.argmin(dprime[lo : hi + 1]))

    offset = parabolic_offset(
        float(dprime[chosen - 1]), float(dprime[chosen]), float(dprime[chosen + 1])
    )
    tau = chosen + offset
    if tau <= 0:
        return PitchEstimate(0.0, 0.0, method)
    freq = float(sample_rate) / tau
    confidence = float(np.clip(1.0 - float(dprime[chosen]), 0.0, 1.0))
    return PitchEstimate(freq=freq, confidence=confidence, method=method)
