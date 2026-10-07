"""主频估计算法测试（规格书 4.4 三个档位）。"""

from __future__ import annotations

import numpy as np
import pytest

from zpyaudio.core.analyzer import Analyzer, AnalyzerConfig
from zpyaudio.core.pitch import (
    PITCH_METHODS,
    estimate_from_spectrum,
    estimate_pitch,
    estimate_yin,
    parabolic_offset,
)

FS = 48_000
N = 4096


def harmonic_tone(
    f0: float,
    *,
    seconds: float = 0.5,
    sample_rate: int = FS,
    amplitudes: tuple[float, ...] = (0.1, 0.5, 0.3, 0.2),
) -> np.ndarray:
    """构造含强泛音的信号：默认二次谐波比基频高 14 dB。"""
    t = np.arange(int(round(seconds * sample_rate)), dtype=np.float64) / sample_rate
    signal = np.zeros_like(t)
    for index, amplitude in enumerate(amplitudes, start=1):
        signal += amplitude * np.sin(2.0 * np.pi * f0 * index * t)
    return signal.astype(np.float32)


# ---------------------------------------------------------------- P1 插值
def test_parabolic_offset_recovers_known_vertex() -> None:
    vertex = 0.3
    values = [-(x - vertex) ** 2 for x in (-1.0, 0.0, 1.0)]

    assert parabolic_offset(*values) == pytest.approx(vertex, abs=1e-9)


def test_parabolic_offset_is_clamped_and_flat_safe() -> None:
    assert parabolic_offset(0.0, 0.0, 0.0) == 0.0
    assert parabolic_offset(-1.0, 0.0, -1.0) == pytest.approx(0.0)
    assert abs(parabolic_offset(-100.0, 0.0, 0.0)) <= 0.5


def test_parabolic_offset_works_for_minimum() -> None:
    """YIN 的差分函数取极小值，抛物线插值同样适用。"""
    vertex = -0.2
    values = [(x - vertex) ** 2 for x in (-1.0, 0.0, 1.0)]

    assert parabolic_offset(*values) == pytest.approx(vertex, abs=1e-9)


def test_p1_matches_tone(make_sine) -> None:
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    freqs, mag_db = analyzer.spectrum(make_sine(1000.0, seconds=0.5))

    estimate = estimate_from_spectrum(freqs, mag_db, fmin=50.0, fmax=5000.0)

    assert estimate.method == "fft_parabolic"
    assert estimate.freq == pytest.approx(1000.0, abs=1.0)
    assert estimate.confidence == pytest.approx(1.0, abs=0.05)


def test_p1_flat_spectrum_has_zero_confidence() -> None:
    freqs = np.linspace(0.0, 24_000.0, 2049)
    mag_db = np.full(freqs.size, -60.0)

    estimate = estimate_from_spectrum(freqs, mag_db, fmin=50.0, fmax=5000.0)

    assert estimate.confidence == pytest.approx(0.0)
    assert 50.0 <= estimate.freq <= 5000.0


def test_band_limits_are_respected() -> None:
    freqs = np.linspace(0.0, 24_000.0, 2049)
    mag_db = np.full(freqs.size, -80.0)
    mag_db[400] = 0.0  # 约 4687 Hz，超出下方 fmax

    estimate = estimate_from_spectrum(freqs, mag_db, fmin=50.0, fmax=2000.0)

    assert estimate.freq <= 2000.0


def test_empty_band_returns_no_pitch() -> None:
    freqs = np.linspace(0.0, 24_000.0, 2049)
    mag_db = np.full(freqs.size, -60.0)

    estimate = estimate_from_spectrum(freqs, mag_db, fmin=25_000.0, fmax=30_000.0)

    assert estimate.freq == 0.0
    assert estimate.confidence == 0.0


def test_length_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="长度"):
        estimate_from_spectrum(np.zeros(4), np.zeros(5))


# ---------------------------------------------------------------- P2 谐波积谱
def test_hps_finds_fundamental_when_second_harmonic_is_stronger() -> None:
    """P1 会把最强泛音当主频；P2 应还原真实基频（规格书 4.4 P2 的存在理由）。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    signal = harmonic_tone(200.0)
    freqs, mag_db = analyzer.spectrum(signal)

    p1 = estimate_from_spectrum(freqs, mag_db, method="fft_parabolic", fmin=50.0, fmax=5000.0)
    p2 = estimate_from_spectrum(freqs, mag_db, method="hps", fmin=50.0, fmax=5000.0)

    assert p1.freq == pytest.approx(400.0, abs=3.0), "谱峰法应被二次谐波带偏"
    assert p2.freq == pytest.approx(200.0, abs=2.0), "谐波积谱应还原基频"
    assert p2.method == "hps"
    assert p2.confidence > 0.5


def test_hps_does_not_report_subharmonic_for_pure_tone() -> None:
    """纯音的 h 次谐波不能伪装成基频（基频门限的作用）。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    freqs, mag_db = analyzer.spectrum(harmonic_tone(1000.0, amplitudes=(0.5,)))

    estimate = estimate_from_spectrum(freqs, mag_db, method="hps", fmin=50.0, fmax=5000.0)

    assert estimate.freq == pytest.approx(1000.0, abs=2.0)


def test_hps_respects_band() -> None:
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    freqs, mag_db = analyzer.spectrum(harmonic_tone(3000.0, amplitudes=(0.5, 0.3)))

    estimate = estimate_from_spectrum(freqs, mag_db, method="hps", fmin=50.0, fmax=2000.0)

    assert estimate.freq <= 2000.0


# ---------------------------------------------------------------- P3 YIN
def test_yin_matches_pure_tone(make_sine) -> None:
    estimate = estimate_yin(make_sine(220.0, seconds=0.2), FS)

    assert estimate.method == "yin"
    assert estimate.freq == pytest.approx(220.0, abs=0.5)
    assert estimate.confidence > 0.9


def test_yin_beats_fft_bin_width_at_low_frequency() -> None:
    """短窗下 Δf=46.9 Hz，YIN 仍能把 80 Hz 定到 1 Hz 以内（规格书 4.4 P3）。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=1024))
    signal = harmonic_tone(80.0, amplitudes=(0.5,))

    assert analyzer.bin_hz == pytest.approx(46.875)
    estimate = estimate_yin(signal[:1024], FS, fmin=50.0, fmax=5000.0)

    assert estimate.freq == pytest.approx(80.0, abs=1.0)
    assert abs(estimate.freq - 80.0) < analyzer.bin_hz / 10.0


def test_yin_finds_fundamental_of_harmonic_tone() -> None:
    estimate = estimate_yin(harmonic_tone(200.0)[:N], FS, fmin=50.0, fmax=5000.0)

    assert estimate.freq == pytest.approx(200.0, abs=1.0)


def test_yin_has_low_confidence_for_noise() -> None:
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(N).astype(np.float32)

    estimate = estimate_yin(noise, FS, fmin=50.0, fmax=5000.0)

    assert estimate.confidence < 0.6


def test_yin_silence_has_zero_confidence() -> None:
    estimate = estimate_yin(np.zeros(N, dtype=np.float32), FS)

    assert estimate.confidence == pytest.approx(0.0)


def test_yin_rejects_too_short_block() -> None:
    estimate = estimate_yin(np.zeros(32, dtype=np.float32), FS)

    assert estimate.freq == 0.0
    assert estimate.confidence == 0.0


def test_yin_handles_block_shorter_than_search_range() -> None:
    """块长不足以覆盖 tau_max 时应自动收窄搜索上限，而不是抛异常。"""
    estimate = estimate_yin(np.sin(np.arange(600) * 0.1).astype(np.float32), FS)

    assert estimate.freq >= 0.0


# ---------------------------------------------------------------- 统一入口
def test_spectrum_entry_rejects_yin_with_hint() -> None:
    with pytest.raises(ValueError, match="estimate_yin"):
        estimate_from_spectrum(np.zeros(8), np.zeros(8), method="yin")


def test_unknown_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知主频算法"):
        estimate_pitch(np.zeros(8), np.zeros(8), method="nope")


def test_pitch_alias_matches_spectrum_entry(make_sine) -> None:
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    freqs, mag_db = analyzer.spectrum(make_sine(1000.0, seconds=0.5))

    alias = estimate_pitch(freqs, mag_db)
    direct = estimate_from_spectrum(freqs, mag_db)

    assert alias == direct


def test_method_catalog_matches_spec() -> None:
    assert PITCH_METHODS == ("fft_parabolic", "hps", "yin")


# ---------------------------------------------------------------- 分析器分派
@pytest.mark.parametrize(
    ("method", "expected"),
    [("fft_parabolic", 400.0), ("hps", 200.0), ("yin", 200.0)],
)
def test_analyzer_dispatches_by_method(method: str, expected: float) -> None:
    analyzer = Analyzer(
        AnalyzerConfig(sample_rate=FS, window_size=N, pitch_method=method)
    )

    snapshot = analyzer.analyze(harmonic_tone(200.0))

    assert snapshot.method == method
    assert snapshot.dominant_freq == pytest.approx(expected, abs=3.0)
