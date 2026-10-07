"""音名换算测试（规格书 v0.5 变更 1）。

判据要点：
* 参考音 A4 = 440 Hz、科学音高记号（C4 = 261.63 Hz）；
* 与 FR-4.3 的 MIDI 公式互为逆运算；
* 低频段（< 250 Hz）标记"仅供参考"，非正/非有限频率不产生音名。
"""

from __future__ import annotations

import pytest

from zpyaudio.core.notes import (
    A4_FREQ,
    LOW_FREQ_NOTE_WARN_HZ,
    NoteInfo,
    format_note,
    freq_to_note,
    note_frequency,
)

# ---------------------------------------------------------------- 基本换算
def test_a4_is_reference_note() -> None:
    info = freq_to_note(A4_FREQ)

    assert info is not None
    assert info.name == "A4"
    assert info.note == "A"
    assert info.octave == 4
    assert info.midi == 69
    assert info.cents == pytest.approx(0.0, abs=1e-6)
    assert not info.low_band


def test_c4_matches_specification() -> None:
    """规格书 FR-4.3：C4（note=60）为 261.63 Hz，误差 < 0.01 Hz。"""
    freq = note_frequency(60)

    assert freq == pytest.approx(261.63, abs=0.01)
    info = freq_to_note(freq)
    assert info is not None
    assert info.name == "C4"
    assert info.midi == 60
    assert info.cents == pytest.approx(0.0, abs=0.02)


def test_1000_hz_is_b5_with_positive_cents() -> None:
    """1 kHz 正弦（验收 A1/A2 的测试信号）落在 B5 上方约 21 音分。"""
    info = freq_to_note(1000.0)

    assert info is not None
    assert info.name == "B5"
    assert info.midi == 83
    assert info.cents == pytest.approx(21.3, abs=0.1)
    assert not info.low_band


def test_sharp_spelling_used() -> None:
    info = freq_to_note(note_frequency(61))  # C#4

    assert info is not None
    assert info.note == "C#"
    assert info.name == "C#4"


def test_octave_numbering_follows_scientific_pitch() -> None:
    """C 音是八度分界：B3（246.94 Hz）与 C4（261.63 Hz）相邻。"""
    assert freq_to_note(note_frequency(59)).name == "B3"  # type: ignore[union-attr]
    assert freq_to_note(note_frequency(60)).name == "C4"  # type: ignore[union-attr]


def test_cents_is_bounded_within_half_semitone() -> None:
    for freq in (55.0, 110.0, 261.63, 440.0, 1000.0, 4186.0):
        info = freq_to_note(freq)
        assert info is not None
        assert -50.0 <= info.cents <= 50.0


# ---------------------------------------------------------------- 边界
@pytest.mark.parametrize("freq", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_frequency_has_no_note(freq: float) -> None:
    assert freq_to_note(freq) is None
    assert format_note(None) == "—"


def test_low_band_is_flagged_below_threshold() -> None:
    low = freq_to_note(LOW_FREQ_NOTE_WARN_HZ - 1.0)
    high = freq_to_note(LOW_FREQ_NOTE_WARN_HZ + 1.0)

    assert low is not None and low.low_band
    assert high is not None and not high.low_band


def test_a4_must_be_positive() -> None:
    with pytest.raises(ValueError, match="a4"):
        freq_to_note(440.0, a4=0.0)
    with pytest.raises(ValueError, match="a4"):
        note_frequency(69, a4=-440.0)


def test_custom_reference_shifts_note_name() -> None:
    """A4 = 442 Hz 时，440 Hz 会落在 A4 下方约 8 音分。"""
    info = freq_to_note(440.0, a4=442.0)

    assert info is not None
    assert info.midi == 69
    assert info.cents == pytest.approx(-7.85, abs=0.1)


# ---------------------------------------------------------------- 文本格式
def test_format_note_with_cents() -> None:
    info = freq_to_note(1000.0)

    assert format_note(info) == "B5 +21.3¢"
    assert format_note(info, with_cents=False) == "B5"


def test_format_note_low_band_hints() -> None:
    info = freq_to_note(note_frequency(36))  # C2 = 65.41 Hz < 250 Hz
    assert info is not None and info.low_band

    plain = format_note(info)
    marked = format_note(info, low_band_hint="mark")
    full = format_note(info, low_band_hint="full")

    assert plain == "C2 +0.0¢"
    assert marked.endswith("*")
    assert "低频仅供参考" in full


def test_format_note_handles_zero_cents_without_negative_zero() -> None:
    info = NoteInfo(name="A4", note="A", octave=4, midi=69, cents=-0.02, low_band=False)

    assert format_note(info) == "A4 +0.0¢"
