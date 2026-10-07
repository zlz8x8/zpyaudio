"""主频轨迹后处理测试（规格书 4.4 平滑与跳变检测）。"""

from __future__ import annotations

import numpy as np
import pytest

from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.pitch_track import HARMONIC_RATIOS, PitchTrack


def snapshot(t: float, freq: float, *, confidence: float = 0.9) -> AnalysisSnapshot:
    """构造只关心主频的快照。"""
    freqs = np.array([0.0, 1000.0, 2000.0])
    db = np.zeros(3)
    return AnalysisSnapshot(
        t=t,
        spectrum_freqs=freqs,
        spectrum_db=db,
        peak_freq=freq,
        dominant_freq=freq,
        confidence=confidence if freq > 0 else 0.0,
        rms_db=-20.0 if freq > 0 else -90.0,
    )


def test_median_filter_removes_single_frame_outlier() -> None:
    """中间一帧被误判为 2 倍频时，3 点中值应把它压掉。"""
    track = PitchTrack()

    track.push(snapshot(0.0, 1000.0))
    track.push(snapshot(0.0625, 1000.0))
    point = track.push(snapshot(0.125, 2000.0))

    assert point is not None
    assert point.freq == pytest.approx(1000.0)
    assert point.raw_freq == pytest.approx(2000.0)


def test_median_filter_keeps_sustained_change() -> None:
    """持续的改变不会被中值滤波吃掉。"""
    track = PitchTrack()
    track.push(snapshot(0.0, 1000.0))
    track.push(snapshot(0.0625, 1500.0))
    track.push(snapshot(0.125, 1500.0))

    point = track.push(snapshot(0.1875, 1500.0))

    assert point is not None
    assert point.freq == pytest.approx(1500.0)


def feed(track: PitchTrack, freq: float, count: int = 3, start_t: float = 0.0):
    """连续推入若干帧同频快照，返回最后一帧的输出点。"""
    point = None
    for index in range(count):
        point = track.push(snapshot(start_t + index * 0.0625, freq))
    return point


@pytest.mark.parametrize("ratio", [0.5, 2.0, 3.0, 1.5, 1 / 3])
def test_harmonic_jumps_are_not_suspect(ratio: float) -> None:
    """整数倍/简单分数关系属于正常倍频跳变，不标可疑。"""
    track = PitchTrack()
    feed(track, 1000.0)

    point = feed(track, 1000.0 * ratio, start_t=0.2)

    assert point is not None
    assert point.freq == pytest.approx(1000.0 * ratio)
    assert point.suspect is False


def test_non_harmonic_jump_is_suspect() -> None:
    """1700/1000 = 1.7，既超过 12% 也不是整数倍/简单分数关系 → 可疑。

    中值滤波会让跳变点延后一帧出现，因此检查整段过渡里是否出现过可疑标记。
    """
    track = PitchTrack()
    feed(track, 1000.0)

    points = [track.push(snapshot(0.2 + index * 0.0625, 1700.0)) for index in range(3)]

    assert [point.freq for point in points if point] == pytest.approx([1000.0, 1700.0, 1700.0])
    assert any(point.suspect for point in points if point), "跳变应被标记为可疑"
    assert points[-1].suspect is False, "跳变只在过渡帧标一次，稳定后应恢复正常"


def test_small_change_is_not_suspect() -> None:
    track = PitchTrack()
    track.push(snapshot(0.0, 1000.0))

    point = track.push(snapshot(0.0625, 1050.0))  # +5%

    assert point is not None
    assert point.suspect is False


def test_silence_returns_none_and_resets_history() -> None:
    track = PitchTrack()
    track.push(snapshot(0.0, 1000.0))
    track.push(snapshot(0.0625, 1000.0))

    assert track.push(snapshot(0.125, 0.0)) is None
    assert track.history == ()
    assert track.last is None

    # 静音后第一帧不应与静音前的值做中值
    point = track.push(snapshot(0.1875, 2000.0))
    assert point is not None
    assert point.freq == pytest.approx(2000.0)


def test_reset_clears_state() -> None:
    track = PitchTrack()
    track.push(snapshot(0.0, 1000.0))

    track.reset()

    assert track.last is None
    assert track.history == ()


def test_confidence_and_time_are_passed_through() -> None:
    track = PitchTrack()

    point = track.push(snapshot(1.25, 440.0, confidence=0.42))

    assert point is not None
    assert point.t == pytest.approx(1.25)
    assert point.confidence == pytest.approx(0.42)


def test_harmonic_relation_helper() -> None:
    track = PitchTrack()

    for ratio in HARMONIC_RATIOS:
        assert track.is_harmonic_relation(ratio) is True
    assert track.is_harmonic_relation(1.7) is False
    assert track.is_harmonic_relation(0.0) is False


def test_window_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window"):
        PitchTrack(window=0)


def test_median_window_is_configurable() -> None:
    track = PitchTrack(window=5)
    for index, freq in enumerate([1000.0, 1000.0, 1000.0, 2000.0, 1000.0]):
        track.push(snapshot(index * 0.0625, freq))

    point = track.push(snapshot(0.3125, 1000.0))

    assert point is not None
    assert point.freq == pytest.approx(1000.0)
