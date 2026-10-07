"""频率 → 音名换算（规格书 v0.5 变更 1）。

口径
----
* **十二平均律**，参考音 **A4 = 440 Hz**（`A4_FREQ`），音名用科学音高记号；
* 半音编号与规格书 FR-4.3 的 ``f = 440 × 2^((note-69)/12)`` 完全一致，
  因此 M5 的 MIDI 音符频率曲线可直接复用 :func:`note_frequency`；
* 音名**不是**基频/音高本身，只是把当前主频按十二平均律就近命名的结果，
  因此同时给出音分偏差 ``cents``（正=偏高，负=偏低）。

低频段的可信度限制（必须让使用者看到）
--------------------------------------
频率分辨率 Δf = fs / N（默认 48 kHz / 4096 → 11.7 Hz）粗于低音区的半音间距：
C2 = 65.4 Hz 附近半音仅约 3.9 Hz、C3 = 130.8 Hz 附近约 7.8 Hz。
此时"最近的音名"可能整体偏一个半音，所以低于 :data:`LOW_FREQ_NOTE_WARN_HZ`
（默认 250 Hz）的读数会被标记 ``low_band=True``，由界面提示"仅供参考"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "A4_FREQ",
    "A4_MIDI",
    "NOTE_NAMES",
    "LOW_FREQ_NOTE_WARN_HZ",
    "NoteInfo",
    "freq_to_note",
    "note_frequency",
    "format_note",
]

#: 参考音 A4 的频率（Hz）
A4_FREQ = 440.0

#: 参考音 A4 的 MIDI 音符号（规格书 FR-4.3）
A4_MIDI = 69

#: 十二个半音音名（升号写法）
NOTE_NAMES: tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

#: 低频提示门限：低于该频率时音名可能整体偏一个半音（见模块 docstring）
LOW_FREQ_NOTE_WARN_HZ = 250.0


@dataclass(frozen=True, slots=True)
class NoteInfo:
    """一次频率→音名的换算结果。"""

    name: str
    """含八度的音名，如 ``B5``。"""

    note: str
    """不含八度的音名，如 ``B``。"""

    octave: int
    """八度编号（科学音高记号，C4 = 261.63 Hz）。"""

    midi: int
    """就近的 MIDI 音符号（A4 = 69）。"""

    cents: float
    """相对就近音名的音分偏差，范围 ``(-50, +50]``。"""

    low_band: bool
    """是否落在低频提示区间（< :data:`LOW_FREQ_NOTE_WARN_HZ`）。"""


def note_frequency(midi: int, *, a4: float = A4_FREQ) -> float:
    """MIDI 音符号 → 频率（Hz），与规格书 FR-4.3 同一公式。"""
    if a4 <= 0.0:
        raise ValueError(f"a4 必须为正，收到 {a4}")
    return float(a4) * 2.0 ** ((int(midi) - A4_MIDI) / 12.0)


def freq_to_note(freq: float, *, a4: float = A4_FREQ) -> NoteInfo | None:
    """频率（Hz）→ 就近音名；``freq`` 非正或非有限时返回 ``None``。"""
    if a4 <= 0.0:
        raise ValueError(f"a4 必须为正，收到 {a4}")
    value = float(freq)
    if not math.isfinite(value) or value <= 0.0:
        return None
    midi_float = A4_MIDI + 12.0 * math.log2(value / float(a4))
    midi = int(round(midi_float))
    cents = (midi_float - midi) * 100.0
    note = NOTE_NAMES[midi % 12]
    octave = midi // 12 - 1
    return NoteInfo(
        name=f"{note}{octave}",
        note=note,
        octave=octave,
        midi=midi,
        cents=cents,
        low_band=value < LOW_FREQ_NOTE_WARN_HZ,
    )


def format_note(
    info: NoteInfo | None,
    *,
    with_cents: bool = True,
    low_band_hint: str | None = None,
) -> str:
    """格式化为界面/日志用文本，如 ``B5 +20.8¢``。

    ``low_band_hint``：``None``（不提示）、``"mark"``（追加 ``*``）、
    ``"full"``（追加"（低频仅供参考）"）；仅在 ``info.low_band`` 为真时生效。
    无有效音名时返回 ``"—"``。
    """
    if info is None:
        return "—"
    text = info.name
    if with_cents:
        cents = round(float(info.cents), 1)
        if cents == 0.0:
            cents = 0.0
        text += f" {cents:+.1f}¢"
    if info.low_band:
        if low_band_hint == "mark":
            text += "*"
        elif low_band_hint == "full":
            text += f"（低频仅供参考 < {LOW_FREQ_NOTE_WARN_HZ:.0f} Hz）"
    return text
