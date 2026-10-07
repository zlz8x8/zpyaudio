"""``AudioFrame`` / ``AnalysisSnapshot`` 契约测试（规格书 4.1）。"""

from __future__ import annotations

import numpy as np
import pytest

from zpyaudio.core.frames import MAX_CHANNELS, AnalysisSnapshot, AudioFrame


def test_accepts_1d_block_and_normalizes_shape() -> None:
    frame = AudioFrame(np.zeros(8, dtype=np.float64), 48_000, 1, 0.0)

    assert frame.samples.dtype == np.float32
    assert frame.samples.shape == (8, 1)
    assert frame.frames == 8
    assert frame.duration == pytest.approx(8 / 48_000)
    assert frame.end_timestamp == pytest.approx(8 / 48_000)


def test_clips_out_of_range_values() -> None:
    frame = AudioFrame(np.array([-2.0, -1.0, 0.0, 1.0, 3.0], dtype=np.float32), 48_000, 1, 0.0)

    assert frame.samples[:, 0].tolist() == [-1.0, -1.0, 0.0, 1.0, 1.0]


def test_copies_input_so_caller_data_is_untouched() -> None:
    source = np.full((4, 1), 2.0, dtype=np.float32)
    AudioFrame(source, 48_000, 1, 0.0)

    assert source.tolist() == [[2.0]] * 4, "原始数组不应被裁剪"


def test_rejects_channel_mismatch() -> None:
    with pytest.raises(ValueError, match="channels"):
        AudioFrame(np.zeros((4, 2), dtype=np.float32), 48_000, 1, 0.0)


def test_rejects_too_many_channels() -> None:
    with pytest.raises(ValueError, match="channels"):
        AudioFrame(np.zeros((4, MAX_CHANNELS + 1), dtype=np.float32), 48_000, 3, 0.0)


@pytest.mark.parametrize("sample_rate", [0, -1])
def test_rejects_invalid_sample_rate(sample_rate: int) -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        AudioFrame(np.zeros(4, dtype=np.float32), sample_rate, 1, 0.0)


def test_rejects_non_finite_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        AudioFrame(np.zeros(4, dtype=np.float32), 48_000, 1, float("nan"))


def test_mono_mixes_channels() -> None:
    samples = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    frame = AudioFrame(samples, 48_000, 2, 0.0)

    assert frame.mono().tolist() == pytest.approx([0.5, 0.5])


def test_snapshot_has_pitch_flag() -> None:
    freqs = np.zeros(4)
    db = np.zeros(4)
    assert AnalysisSnapshot(0.0, freqs, db, 0.0, 440.0, 0.8, -20.0).has_pitch
    assert not AnalysisSnapshot(0.0, freqs, db, 0.0, 0.0, 0.0, -90.0).has_pitch
    assert not AnalysisSnapshot(0.0, freqs, db, 0.0, 440.0, 0.0, -20.0).has_pitch
