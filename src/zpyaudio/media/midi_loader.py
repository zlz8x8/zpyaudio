"""MIDI 符号解析（规格书 3.4 FR-4.1，M5 里程碑）。

MIDI 是**符号数据**，没有音频波形：这里只把音符事件解释出来，不做任何 PCM 合成
（决策 D3；合成留给 M7 的 :mod:`zpyaudio.media.midi_render`）。

时间口径（关键，容易踩）
------------------------
``mido`` 的 ``Message.time`` 是 **delta**，单位取决于怎么读：

* 逐轨迭代 ``mid.tracks[i]`` → 单位是 **tick**；
* ``for msg in mid``（mido 内部先 merge，再按 tempo map 换算）→ 单位是 **秒**，
  但这样**拿不到轨道号**了。

FR-4.1 要求事件里带 ``track``，所以本模块走第一条路：自己把每个轨道的时间轴
按 tempo map 从 tick 换算成秒（:func:`notes_from_midi` / :func:`notes_from_track`）。

其它口径
--------
* ``note_on`` 且 ``velocity == 0`` 按 MIDI 惯例视为 ``note_off``；
* 同一 ``(channel, note)`` 上重叠的多次 ``note_on`` 用**队列**逐个配对，
  避免后一个把前一个顶掉（长音/踏板段落很常见，丢一个音就会算错音符数）；
* 轨道结束时仍未闭合的音符**不丢弃**，按轨末闭合（宁可长一点，也不要凭空少一个音）；
* ``type 2`` 的各轨是**互相独立**的序列，因此每轨用**自己的** tempo map 换算；
  ``type 0/1`` 的 tempo map 是全局的（惯例放在第 0 轨），也会扫描所有轨，
  与 ``mido`` 合并迭代的口径一致。
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import mido

from zpyaudio.core.notes import freq_to_note, note_frequency

__all__ = [
    "DEFAULT_TEMPO_US",
    "MIDI_SUFFIXES",
    "MidiDocument",
    "MidiLoadError",
    "NoteEvent",
    "load_midi",
    "notes_from_midi",
    "notes_from_track",
    "tempo_map_of_track",
    "tempo_map_of_tracks",
]

logger = logging.getLogger(__name__)

#: 支持的 MIDI 后缀（规格书 7.2 文件过滤器）
MIDI_SUFFIXES: frozenset[str] = frozenset({".mid", ".midi"})

#: MIDI 规范默认速度：500000 µs/拍 = 120 BPM
DEFAULT_TEMPO_US = 500_000


class MidiLoadError(RuntimeError):
    """MIDI 解析失败（消息面向使用者，中文）。"""


@dataclass(frozen=True, slots=True)
class NoteEvent:
    """一个音符事件（字段顺序即规格书 FR-4.1 的 ``(start, end, note, velocity, channel, track)``）。"""

    start: float
    """起始时刻（秒）。"""

    end: float
    """结束时刻（秒）。"""

    note: int
    """MIDI 音符号（A4 = 69）。"""

    velocity: int
    """力度（1–127）。"""

    channel: int
    """MIDI 通道（0–15）。"""

    track: int
    """轨道序号（从 0 起，对应 ``mido.MidiFile.tracks`` 的下标）。"""

    @property
    def duration(self) -> float:
        """时长（秒），不会为负。"""
        return max(0.0, self.end - self.start)

    @property
    def frequency(self) -> float:
        """音符频率（Hz），FR-4.3 的 ``f = 440 × 2^((note-69)/12)``。"""
        return note_frequency(self.note)

    @property
    def name(self) -> str:
        """音名（如 ``C4``）；与声学主频共用 :mod:`zpyaudio.core.notes` 的口径。"""
        info = freq_to_note(self.frequency)
        return info.name if info is not None else "—"


@dataclass(frozen=True, slots=True)
class MidiDocument:
    """一次 MIDI 解析的完整结果（符号数据，不含 PCM）。"""

    path: Path
    notes: tuple[NoteEvent, ...]
    duration: float
    """曲目时长（秒）：取 ``mido`` 的文件长度与最后一个音符结束时刻的较大者。"""

    track_count: int
    midi_type: int
    """MIDI 文件类型：0 / 1 / 2。"""

    tempo_bpm: float
    """首个 ``set_tempo`` 换算出的 BPM（无 tempo 事件时按规范取 120）。"""

    @property
    def note_count(self) -> int:
        """音符总数。"""
        return len(self.notes)

    @property
    def channels(self) -> tuple[int, ...]:
        """出现过的通道号（升序）。"""
        return tuple(sorted({item.channel for item in self.notes}))

    @property
    def note_range(self) -> tuple[int, int]:
        """最低/最高音符号；无音符时返回 ``(0, 0)``。"""
        if not self.notes:
            return (0, 0)
        values = [item.note for item in self.notes]
        return (min(values), max(values))

    def describe(self) -> str:
        """一行中文摘要（日志、状态栏、CLI 共用）。"""
        low, high = self.note_range
        return (
            f"{self.path.name}：{self.note_count} 个音符 / {self.track_count} 轨 / "
            f"音域 {low}–{high} / 时长 {self.duration:.2f} s / "
            f"{self.tempo_bpm:.1f} BPM / type {self.midi_type}"
        )


# --------------------------------------------------------------------- 时间轴
def _segments(
    tempo_map: Sequence[tuple[int, int]], ticks_per_beat: int
) -> list[tuple[int, float, float]]:
    """把 tempo map 预计算成 ``(起始 tick, 起始秒, 每 tick 秒数)`` 分段表。"""
    tpb = max(1, int(ticks_per_beat))
    changes = sorted((int(tick), int(tempo)) for tick, tempo in tempo_map if int(tempo) > 0)
    if not changes or changes[0][0] != 0:
        changes.insert(0, (0, DEFAULT_TEMPO_US))

    segments: list[tuple[int, float, float]] = []
    prev_tick = 0
    prev_seconds = 0.0
    prev_tempo = changes[0][1]
    for tick, tempo in changes:
        if tick > prev_tick:
            per_tick = prev_tempo / 1_000_000.0 / tpb
            segments.append((prev_tick, prev_seconds, per_tick))
            prev_seconds += (tick - prev_tick) * per_tick
            prev_tick = tick
        # 同一 tick 上多条 set_tempo：以最后一条为准（后写覆盖）
        prev_tempo = tempo
    segments.append((prev_tick, prev_seconds, prev_tempo / 1_000_000.0 / tpb))
    return segments


def _tick_to_seconds(segments: Sequence[tuple[int, float, float]], tick: int) -> float:
    """绝对 tick → 秒。"""
    value = max(0, int(tick))
    starts = [item[0] for item in segments]
    index = max(0, bisect_right(starts, value) - 1)
    start_tick, start_seconds, per_tick = segments[index]
    return start_seconds + (value - start_tick) * per_tick


def tempo_map_of_track(track: Iterable[mido.Message]) -> list[tuple[int, int]]:
    """单轨的 ``(绝对 tick, tempo µs/拍)`` 列表（按 tick 升序）。"""
    events: list[tuple[int, int]] = []
    ticks = 0
    for message in track:
        ticks += max(0, int(getattr(message, "time", 0) or 0))
        if message.type == "set_tempo":
            events.append((ticks, int(message.tempo)))
    events.sort()
    return events


def tempo_map_of_tracks(tracks: Iterable[Iterable[mido.Message]]) -> list[tuple[int, int]]:
    """多轨合并后的 tempo map（type 0/1 的 tempo 是全局的）。"""
    events: list[tuple[int, int]] = []
    for track in tracks:
        events.extend(tempo_map_of_track(track))
    events.sort()
    return events


# --------------------------------------------------------------------- 解析
def notes_from_track(
    track: Iterable[mido.Message],
    *,
    track_index: int = 0,
    ticks_per_beat: int = 480,
    tempo_map: Sequence[tuple[int, int]] | None = None,
) -> list[NoteEvent]:
    """把**单个轨道**的 tick 事件流解析成 :class:`NoteEvent` 列表。

    传入的 ``track`` 会被物化成列表（需要遍历两次：一次取 tempo、一次取音符）。
    """
    messages = list(track)
    if tempo_map is None:
        tempo_map = tempo_map_of_track(messages)
    segments = _segments(tempo_map, ticks_per_beat)

    ticks = 0
    opened: dict[tuple[int, int], deque[tuple[float, int]]] = {}
    events: list[NoteEvent] = []
    for message in messages:
        ticks += max(0, int(getattr(message, "time", 0) or 0))
        now = _tick_to_seconds(segments, ticks)
        kind = message.type
        if kind == "note_on" and int(getattr(message, "velocity", 0)) > 0:
            key = (int(getattr(message, "channel", 0)), int(message.note))
            opened.setdefault(key, deque()).append((now, int(message.velocity)))
        elif kind == "note_off" or (
            kind == "note_on" and int(getattr(message, "velocity", 0)) == 0
        ):
            key = (int(getattr(message, "channel", 0)), int(message.note))
            queue = opened.get(key)
            if queue:
                start, velocity = queue.popleft()
                events.append(
                    NoteEvent(
                        start=start,
                        end=now,
                        note=key[1],
                        velocity=velocity,
                        channel=key[0],
                        track=int(track_index),
                    )
                )

    # 轨末仍未闭合的音符：按轨末闭合，不丢弃
    track_end = _tick_to_seconds(segments, ticks)
    for (channel, note), queue in opened.items():
        while queue:
            start, velocity = queue.popleft()
            events.append(
                NoteEvent(
                    start=start,
                    end=max(track_end, start),
                    note=note,
                    velocity=velocity,
                    channel=channel,
                    track=int(track_index),
                )
            )

    events.sort(key=lambda item: (item.start, item.note, item.track))
    return events


def notes_from_midi(midi: mido.MidiFile) -> list[NoteEvent]:
    """把一个 :class:`mido.MidiFile` 解析成按起始时刻排序的音符事件列表。"""
    tracks = list(midi.tracks)
    global_map = None if int(midi.type) == 2 else tempo_map_of_tracks(tracks)
    events: list[NoteEvent] = []
    for index, track in enumerate(tracks):
        events.extend(
            notes_from_track(
                track,
                track_index=index,
                ticks_per_beat=int(midi.ticks_per_beat),
                tempo_map=None if global_map is None else global_map,
            )
        )
    events.sort(key=lambda item: (item.start, item.note, item.track))
    return events


def load_midi(path: str | Path) -> MidiDocument:
    """读取并解析 MIDI 文件（FR-4.1）。

    Raises:
        MidiLoadError: 文件不存在、后缀不是 MIDI、无法解析，或里面没有音符事件。
    """
    target = Path(path)
    if not target.exists():
        raise MidiLoadError(f"文件不存在：{target}")
    if not target.is_file():
        raise MidiLoadError(f"不是文件：{target}")
    suffix = target.suffix.lower()
    if suffix not in MIDI_SUFFIXES:
        allowed = " / ".join(sorted(MIDI_SUFFIXES))
        raise MidiLoadError(f"不是 MIDI 文件（后缀 {suffix or '（无）'}，支持 {allowed}）：{target}")

    try:
        midi = mido.MidiFile(str(target))
    except Exception as exc:  # noqa: BLE001 - mido 的异常类型不统一（OSError/EOFError/ValueError…）
        raise MidiLoadError(f"无法解析 MIDI 文件：{exc}") from exc

    notes = notes_from_midi(midi)
    if not notes:
        raise MidiLoadError("该 MIDI 里没有音符事件（可能只有控制/元数据），无法绘制钢琴卷帘")

    tempo_changes = tempo_map_of_tracks(midi.tracks)
    tempo_bpm = 60_000_000.0 / tempo_changes[0][1] if tempo_changes else 120.0
    duration = max(float(midi.length), max(item.end for item in notes))
    document = MidiDocument(
        path=target,
        notes=tuple(notes),
        duration=duration,
        track_count=len(midi.tracks),
        midi_type=int(midi.type),
        tempo_bpm=float(tempo_bpm),
    )
    logger.info("MIDI 解析完成：%s", document.describe())
    if document.midi_type == 2:
        logger.warning("该 MIDI 是 type 2（各轨互相独立），已按轨分别换算时间后合并显示")
    return document
