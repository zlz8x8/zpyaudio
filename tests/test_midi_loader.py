"""MIDI 符号解析测试（M5：FR-4.1 / FR-4.3 / FR-4.5，验收 A7 的数据面）。

重点守住三件事：

1. **时间口径**：``mido`` 逐轨迭代给出的是 **tick**，本模块必须自己按 tempo map
   换算成秒；换算出错在界面上表现为"钢琴卷帘整体被压缩/拉长"，很难肉眼发现；
2. **音符配对**：``note_on velocity=0`` 是 ``note_off``；同音高重叠要按队列配对；
   轨末未闭合的音符不能丢；
3. **FR-4.1 的验收方式**：解析出的音符数必须等于 ``mido`` 里 ``note_on`` 的条数。
"""

from __future__ import annotations

import pytest

mido = pytest.importorskip("mido")

from zpyaudio.core.notes import note_frequency  # noqa: E402
from zpyaudio.media.midi_loader import (  # noqa: E402
    MidiDocument,
    MidiLoadError,
    NoteEvent,
    load_midi,
    notes_from_midi,
    notes_from_track,
    tempo_map_of_track,
)
from zpyaudio.media.midi_render import MIDI_RENDER_HINT, MidiRenderer  # noqa: E402

#: 120 BPM / 480 tpb 下 1 秒 = 960 tick（``write_midi`` 的口径）
SECOND_IN_TICKS = 960


def _file(*messages: object, ticks_per_beat: int = 480, midi_type: int = 1) -> mido.MidiFile:
    """用手写的消息构造一个单轨 MIDI。"""
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=midi_type)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    for message in messages:
        track.append(message)
    return midi


# --------------------------------------------------------------------- 基本解析
def test_note_timing_is_converted_to_seconds() -> None:
    """tick → 秒必须按 tempo 换算：0 tick→0.0 s，960 tick→1.0 s。"""
    midi = _file(
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_off", note=60, velocity=0, time=SECOND_IN_TICKS),
    )
    notes = notes_from_midi(midi)
    assert len(notes) == 1
    item = notes[0]
    assert item.start == pytest.approx(0.0)
    assert item.end == pytest.approx(1.0)
    assert item.duration == pytest.approx(1.0)
    assert (item.note, item.velocity, item.channel, item.track) == (60, 100, 0, 0)


def test_note_count_matches_mido_note_on_count() -> None:
    """FR-4.1 的验收条件：解析结果与 ``mido`` 的 note_on 条数一致。"""
    midi = _file(
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.Message("note_on", note=60, velocity=90, time=0),
        mido.Message("note_off", note=60, velocity=0, time=240),
        mido.Message("note_on", note=64, velocity=90, time=0),
        mido.Message("note_on", note=67, velocity=90, time=240),
        mido.Message("note_off", note=64, velocity=0, time=240),
        mido.Message("note_off", note=67, velocity=0, time=0),
    )
    expected = sum(
        1
        for track in midi.tracks
        for message in track
        if message.type == "note_on" and message.velocity > 0
    )
    assert len(notes_from_midi(midi)) == expected == 3


def test_note_on_with_zero_velocity_is_note_off() -> None:
    """MIDI 惯例：``note_on velocity=0`` 等价于 ``note_off``。"""
    midi = _file(
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_on", note=60, velocity=0, time=SECOND_IN_TICKS),
    )
    notes = notes_from_midi(midi)
    assert len(notes) == 1
    assert notes[0].end == pytest.approx(1.0)


def test_overlapping_same_pitch_notes_are_paired_in_order() -> None:
    """同音高重叠：用队列配对，后一个 note_on 不得把前一个顶掉。"""
    midi = _file(
        mido.Message("note_on", note=60, velocity=80, time=0),
        mido.Message("note_on", note=60, velocity=100, time=SECOND_IN_TICKS),
        mido.Message("note_off", note=60, velocity=0, time=SECOND_IN_TICKS),
        mido.Message("note_off", note=60, velocity=0, time=SECOND_IN_TICKS),
    )
    notes = notes_from_midi(midi)
    assert [(round(n.start, 3), round(n.end, 3), n.velocity) for n in notes] == [
        (0.0, 2.0, 80),
        (1.0, 3.0, 100),
    ]


def test_unclosed_note_is_closed_at_track_end() -> None:
    """轨末未闭合的音符不丢弃，按轨末闭合。"""
    midi = _file(
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_on", note=64, velocity=100, time=SECOND_IN_TICKS),
    )
    notes = notes_from_midi(midi)
    assert len(notes) == 2
    assert all(item.end >= item.start for item in notes)
    assert max(item.end for item in notes) == pytest.approx(1.0)


def test_notes_are_sorted_by_start() -> None:
    midi = _file(
        mido.Message("note_on", note=72, velocity=100, time=0),
        mido.Message("note_on", note=60, velocity=100, time=0),
        mido.Message("note_off", note=60, velocity=0, time=SECOND_IN_TICKS),
        mido.Message("note_off", note=72, velocity=0, time=0),
    )
    notes = notes_from_midi(midi)
    starts = [item.start for item in notes]
    assert starts == sorted(starts)


def test_tempo_changes_are_applied() -> None:
    """速度变化后 tick→秒 的斜率必须跟着变（否则卷帘会整体错位）。"""
    midi = _file(
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),  # 120 BPM
        mido.Message("note_on", note=60, velocity=100, time=0),  # tick 0 → 0.00 s
        mido.Message("note_on", note=62, velocity=100, time=480),  # tick 480 → 0.50 s
        mido.MetaMessage("set_tempo", tempo=250_000, time=0),  # 240 BPM
        mido.Message("note_on", note=64, velocity=100, time=480),  # tick 960 → 0.75 s
        mido.Message("note_off", note=60, velocity=0, time=0),
        mido.Message("note_off", note=62, velocity=0, time=0),
        mido.Message("note_off", note=64, velocity=0, time=0),
    )
    starts = {item.note: round(item.start, 4) for item in notes_from_midi(midi)}
    assert starts[60] == pytest.approx(0.0)
    assert starts[62] == pytest.approx(0.5)
    assert starts[64] == pytest.approx(0.75)


def test_tempo_map_of_track() -> None:
    midi = _file(
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.Message("note_on", note=60, velocity=100, time=480),
        mido.MetaMessage("set_tempo", tempo=400_000, time=480),
    )
    assert tempo_map_of_track(midi.tracks[0]) == [(0, 500_000), (960, 400_000)]


def test_multitrack_records_track_index() -> None:
    """FR-4.1 要求事件带 ``track``：多轨文件必须逐轨分开解析。"""
    midi = mido.MidiFile(ticks_per_beat=480, type=1)
    for index, note in enumerate((60, 67)):
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
        track.append(mido.Message("note_on", note=note, velocity=100, channel=index, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=SECOND_IN_TICKS))
    notes = notes_from_midi(midi)
    assert [(item.note, item.track, item.channel) for item in notes] == [(60, 0, 0), (67, 1, 1)]


def test_type2_file_is_parsed_per_track() -> None:
    """type 2 各轨互相独立：每轨用**自己的** tempo map，两轨都解析出来。"""
    midi = mido.MidiFile(ticks_per_beat=480, type=2)
    for note, tempo in ((60, 500_000), (72, 250_000)):
        track = mido.MidiTrack()
        midi.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
        track.append(mido.Message("note_on", note=note, velocity=100, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=480))
    notes = notes_from_midi(midi)
    assert [(item.note, item.track) for item in notes] == [(60, 0), (72, 1)]


def test_notes_from_track_can_take_an_explicit_tempo_map() -> None:
    track = mido.MidiTrack()
    track.append(mido.Message("note_on", note=60, velocity=1, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    notes = notes_from_track(track, track_index=3, ticks_per_beat=480, tempo_map=[(0, 500_000)])
    assert [(item.track, item.note) for item in notes] == [(3, 60)]


# --------------------------------------------------------------------- 事件派生
def test_note_event_derived_fields() -> None:
    item = NoteEvent(start=1.0, end=2.5, note=60, velocity=64, channel=2, track=1)
    assert item.duration == pytest.approx(1.5)
    assert item.frequency == pytest.approx(261.625565, abs=1e-5)
    assert item.name == "C4"
    assert NoteEvent(start=1.0, end=0.5, note=60, velocity=1, channel=0, track=0).duration == 0.0


def test_a7_c4_frequency() -> None:
    """验收 A7 的数字：C4（note 60）= 261.63 Hz（FR-4.3，误差 < 0.01 Hz）。"""
    assert note_frequency(60) == pytest.approx(261.63, abs=0.01)
    assert NoteEvent(start=0.0, end=1.0, note=60, velocity=1, channel=0, track=0).frequency == (
        pytest.approx(261.63, abs=0.01)
    )


# --------------------------------------------------------------------- 文件级
def test_load_midi_document_fields(work_dir, write_midi) -> None:
    path = write_midi(
        work_dir / "scale.mid",
        [
            (0.0, 60, 0.5, 100, 0, 0),
            (0.5, 62, 0.5, 100, 0, 0),
            (1.0, 64, 0.5, 100, 0, 0),
        ],
    )
    document = load_midi(path)
    assert isinstance(document, MidiDocument)
    assert document.path == path
    assert document.note_count == 3
    assert document.track_count == 1
    assert document.midi_type == 1
    assert document.tempo_bpm == pytest.approx(120.0)
    assert document.duration == pytest.approx(1.5, abs=0.05)
    assert document.note_range == (60, 64)
    assert document.channels == (0,)
    assert "scale.mid" in document.describe()


def test_load_midi_missing_file(work_dir) -> None:
    with pytest.raises(MidiLoadError, match="不存在"):
        load_midi(work_dir / "nope.mid")


def test_load_midi_rejects_other_suffix(work_dir, write_midi) -> None:
    path = write_midi(work_dir / "song.txt", [(0.0, 60, 0.5, 100, 0, 0)])
    with pytest.raises(MidiLoadError, match="不是 MIDI 文件"):
        load_midi(path)


def test_load_midi_corrupt_file(work_dir) -> None:
    path = work_dir / "broken.mid"
    path.write_bytes(b"this is definitely not a midi file" * 3)
    with pytest.raises(MidiLoadError, match="无法解析"):
        load_midi(path)


def test_load_midi_without_notes_raises(work_dir) -> None:
    midi = _file(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    path = work_dir / "empty.mid"
    midi.save(str(path))
    with pytest.raises(MidiLoadError, match="没有音符事件"):
        load_midi(path)


def test_load_midi_rejects_directory(work_dir) -> None:
    target = work_dir / "sub.mid"
    target.mkdir()
    with pytest.raises(MidiLoadError, match="不是文件"):
        load_midi(target)


# --------------------------------------------------------------------- FR-4.5
def test_midi_renderer_is_reserved_interface(work_dir, write_midi) -> None:
    """FR-4.5：``MidiRenderer`` 接口存在、可构造，但调用即抛"未实现"。"""
    renderer = MidiRenderer(sample_rate=48_000)
    assert renderer.available is False
    assert "未实现" in renderer.describe()

    document = load_midi(write_midi(work_dir / "one.mid", [(0.0, 60, 0.5, 100, 0, 0)]))
    with pytest.raises(NotImplementedError) as info:
        renderer.render(document)
    assert MIDI_RENDER_HINT in str(info.value)


def test_midi_renderer_validates_sample_rate() -> None:
    with pytest.raises(ValueError, match="sample_rate"):
        MidiRenderer(sample_rate=0)
