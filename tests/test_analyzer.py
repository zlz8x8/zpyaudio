"""分析器测试：核心是验收项 A1（1 kHz 谱峰精度）与 A6（静音不产生虚假主频）。"""

from __future__ import annotations

import numpy as np
import pytest

from zpyaudio.core.analyzer import FFT_SIZES, WINDOW_FUNCTIONS, Analyzer, AnalyzerConfig

FS = 48_000
N = 4096


@pytest.fixture
def analyzer() -> Analyzer:
    return Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N, window="hann"))


# ---------------------------------------------------------------- 验收项 A1
def test_a1_peak_within_one_bin_and_interpolated_within_2hz(analyzer: Analyzer, make_sine) -> None:
    """A1：谱峰偏差 ≤ 1 个 bin（11.7 Hz），插值后 ≤ 2 Hz。"""
    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.5), t=0.0)

    assert analyzer.bin_hz == pytest.approx(11.71875)
    assert abs(snapshot.peak_freq - 1000.0) <= analyzer.bin_hz
    assert abs(snapshot.dominant_freq - 1000.0) <= 2.0
    assert snapshot.confidence > 0.9


def test_interpolation_improves_over_raw_bin(analyzer: Analyzer, make_sine) -> None:
    """1000 Hz 落在 85.33 号 bin：插值必须比裸 bin 更接近真值。"""
    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.5), t=0.0)

    bin_error = abs(snapshot.peak_freq - 1000.0)
    interpolated_error = abs(snapshot.dominant_freq - 1000.0)
    assert interpolated_error < bin_error
    assert interpolated_error < 1.0


def test_bin_centered_tone_has_correct_dbfs(analyzer: Analyzer, make_sine) -> None:
    """bin 中心频率的正弦，峰值幅度应等于设定幅度（0.5 → -6.02 dBFS）。"""
    frequency = 85 * FS / N  # 996.09 Hz，正好落在 bin 中心
    snapshot = analyzer.analyze(make_sine(frequency, seconds=0.5, amplitude=0.5), t=0.0)

    peak_db = float(np.max(snapshot.spectrum_db))
    assert peak_db == pytest.approx(20 * np.log10(0.5), abs=0.2)
    assert snapshot.dominant_freq == pytest.approx(frequency, abs=0.5)


@pytest.mark.parametrize(
    ("fft_size", "expected_bin_hz"),
    [(1024, 46.875), (2048, 23.4375), (4096, 11.71875)],
)
def test_bin_resolution_table(fft_size: int, expected_bin_hz: float) -> None:
    """规格书 4.3 的 Δf = fs / N 对照表。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=fft_size))

    assert analyzer.bin_hz == pytest.approx(expected_bin_hz)


@pytest.mark.parametrize("window", WINDOW_FUNCTIONS)
@pytest.mark.parametrize("fft_size", FFT_SIZES)
def test_all_window_and_fft_options_find_tone(window: str, fft_size: int, make_sine) -> None:
    """FR-2.5：任意窗函数 / FFT 长度组合下主频仍然正确。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=fft_size, window=window))

    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.5), t=0.0)

    assert abs(snapshot.dominant_freq - 1000.0) <= 2.0


def test_zero_padding_doubles_resolution(make_sine) -> None:
    """补零只提高插值分辨率，不改变峰位（规格书 4.3 注释）。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N, zero_pad=2))
    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.5), t=0.0)

    assert analyzer.bin_hz == pytest.approx(5.859375)
    assert abs(snapshot.dominant_freq - 1000.0) <= 2.0


# ---------------------------------------------------------------- 验收项 A6
def test_a6_silence_produces_no_pitch(analyzer: Analyzer) -> None:
    """A6 / FR-2.6：静音时无有效主频，但频谱仍然输出。"""
    snapshot = analyzer.analyze(np.zeros(N, dtype=np.float32), t=0.0)

    assert snapshot.dominant_freq == 0.0
    assert snapshot.confidence == 0.0
    assert not snapshot.has_pitch
    assert snapshot.rms_db < -100.0
    assert snapshot.spectrum_db.size == N // 2 + 1


def test_tone_below_gate_is_rejected(analyzer: Analyzer, make_sine) -> None:
    """幅度低到门限以下时同样不报主频。"""
    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.5, amplitude=1e-4), t=0.0)

    assert snapshot.rms_db < analyzer.config.rms_gate_db
    assert snapshot.dominant_freq == 0.0


# ---------------------------------------------------------------- 其他
def test_rms_db_of_known_amplitude(analyzer: Analyzer, make_sine) -> None:
    tone = make_sine(1000.0, seconds=0.5, amplitude=0.5)

    # 正弦 RMS = A / √2 → 20log10(0.3536) = -9.03 dBFS
    assert Analyzer.rms_db(tone) == pytest.approx(-9.03, abs=0.05)


def test_short_block_is_zero_padded(analyzer: Analyzer, make_sine) -> None:
    snapshot = analyzer.analyze(make_sine(1000.0, seconds=0.05), t=0.0)

    assert snapshot.spectrum_freqs.size == N // 2 + 1
    assert snapshot.dominant_freq == pytest.approx(1000.0, abs=2.0)


def test_long_block_uses_latest_samples(analyzer: Analyzer, make_sine) -> None:
    """块长超过窗长时取最新样本（规格书 4.1 末块约定）。"""
    long_block = make_sine(1000.0, seconds=0.5)

    snapshot = analyzer.analyze(long_block, t=1.0, is_final=True)

    assert snapshot.t == 1.0
    assert snapshot.dominant_freq == pytest.approx(1000.0, abs=2.0)


def test_dominant_freq_respects_search_band(make_sine) -> None:
    """fmax 之外的谱峰不参与主频（规格书 4.4 搜索范围）。"""
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N, fmax=2000.0))

    snapshot = analyzer.analyze(make_sine(3000.0, seconds=0.5), t=0.0)

    assert snapshot.peak_freq == pytest.approx(3000.0, abs=12.0)
    assert snapshot.dominant_freq <= 2000.0


def test_stereo_block_is_downmixed(analyzer: Analyzer, make_sine) -> None:
    mono = make_sine(1000.0, seconds=0.5)
    stereo = np.repeat(mono[:, None], 2, axis=1)

    snapshot = analyzer.analyze(stereo, t=0.0)

    assert snapshot.dominant_freq == pytest.approx(1000.0, abs=2.0)


# ---------------------------------------------------------------- 配置校验
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"sample_rate": 0}, "sample_rate"),
        ({"window_size": 0}, "window_size"),
        ({"window": "kaiser"}, "窗函数"),
        ({"zero_pad": 3}, "zero_pad"),
        ({"pitch_method": "magic"}, "主频算法"),
        ({"fmin": 100.0, "fmax": 50.0}, "fmin"),
    ],
)
def test_analyzer_config_rejects_invalid_values(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        AnalyzerConfig(**kwargs)


def test_config_derived_properties() -> None:
    config = AnalyzerConfig(sample_rate=FS, window_size=N, fmax=23_000.0)

    assert config.nfft == N
    assert config.window_seconds == pytest.approx(N / FS)
    assert config.effective_fmax == pytest.approx(0.45 * FS)  # fmax 被 0.45·fs 收紧
    assert Analyzer(config).frame_samples == N


def test_effective_fmax_keeps_user_limit_when_lower() -> None:
    assert AnalyzerConfig(sample_rate=FS, fmax=5000.0).effective_fmax == pytest.approx(5000.0)
