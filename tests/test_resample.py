"""采样率与声道规整测试（规格书 4.2）。"""

from __future__ import annotations

import numpy as np
import pytest

from zpyaudio.core.analyzer import Analyzer, AnalyzerConfig
from zpyaudio.core.resample import prepare_block, resample, to_mono


def test_to_mono_passthrough_for_1d() -> None:
    data = np.arange(4, dtype=np.float32)

    assert to_mono(data).tolist() == data.tolist()


def test_to_mono_averages_channels() -> None:
    data = np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)

    assert to_mono(data).tolist() == pytest.approx([0.5, 0.5])


def test_prepare_block_repeats_mono_to_channels() -> None:
    prepared = prepare_block(np.zeros(4, dtype=np.float32), channels=2)

    assert prepared.shape == (4, 2)


def test_prepare_block_downmixes_to_mono() -> None:
    prepared = prepare_block(np.ones((4, 2), dtype=np.float32), channels=1)

    assert prepared.shape == (4, 1)
    assert prepared[:, 0].tolist() == pytest.approx([1.0] * 4)


def test_resample_identity_returns_copy() -> None:
    data = np.zeros(8, dtype=np.float32)

    out = resample(data, 48_000, 48_000)

    assert out is not data
    assert np.array_equal(out, data)


@pytest.mark.parametrize(
    ("src_rate", "dst_rate"),
    [(44_100, 48_000), (48_000, 16_000), (22_050, 48_000)],
)
def test_resample_preserves_frequency(src_rate: int, dst_rate: int, make_sine) -> None:
    """重采样后主频不变（用分析器校验，跨模块验证规格书 4.2）。"""
    tone = make_sine(1000.0, seconds=1.0, sample_rate=src_rate, amplitude=0.5)

    converted = resample(tone, src_rate, dst_rate)

    assert converted.shape[0] == pytest.approx(tone.shape[0] * dst_rate / src_rate, rel=0.01)
    analyzer = Analyzer(AnalyzerConfig(sample_rate=dst_rate, window_size=4096))
    snapshot = analyzer.analyze(converted, t=0.0)
    assert snapshot.dominant_freq == pytest.approx(1000.0, abs=2.0)


def test_resample_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError, match="采样率"):
        resample(np.zeros(8, dtype=np.float32), 0, 48_000)
