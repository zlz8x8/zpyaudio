"""M5 GUI 测试：钢琴卷帘 + 符号数据频率曲线 + 打开 MIDI 的完整流程。

对应规格书 3.4 FR-4.2 / FR-4.4 与验收 A7：

* 卷帘的**音符起止位置**直接断言到 ``BarGraphItem`` 的 ``x0`` / ``width``（A7 的判据）；
* 频率曲线必须带"符号数据"标识，且与声学主频互斥（FR-4.4 的判据）；
* 卷帘**只在 MIDI 时显示**：打开音频文件后必须隐藏（规格书 7.1 区域④）。
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip("pytestqt")

from zpyaudio.app.config import AppConfig  # noqa: E402
from zpyaudio.app.controller import AppState  # noqa: E402
from zpyaudio.gui.main_window import MainWindow  # noqa: E402
from zpyaudio.gui.pianoroll_view import PianoRollView, note_label  # noqa: E402
from zpyaudio.gui.pitch_view import SYMBOLIC_LABEL, PitchView  # noqa: E402
from zpyaudio.sources.synthetic_source import SyntheticSource  # noqa: E402

from fakes import FakePlayer  # noqa: E402

FS = 48_000

#: 用例共用的四个音：C4 E4 G4 C5，各 0.5 s，从 0 s 起每 1 s 一个
SCALE = [
    (0.0, 60, 0.5, 100, 0, 0),
    (1.0, 64, 0.5, 100, 0, 0),
    (2.0, 67, 0.5, 100, 0, 0),
    (3.0, 72, 0.5, 100, 0, 0),
]


def write_tone(path, *, seconds: float = 1.0, frequency: float = 1000.0):
    t = np.arange(int(seconds * FS)) / FS
    sf.write(
        str(path),
        (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32),
        FS,
        subtype="PCM_16",
    )
    return path


def synthetic_factory(ring) -> SyntheticSource:
    return SyntheticSource(
        ring, sample_rate=FS, channels=1, frequency=1000.0, amplitude=0.5, block_size=1024
    )


def curve_length(item) -> int:
    """曲线里已画出的点数；``getData()`` 在清空后可能返回 ``None``。"""
    data = item.getData()[0]
    return 0 if data is None else len(data)


@pytest.fixture
def config(work_dir) -> AppConfig:
    cfg = AppConfig()
    cfg.records_dir = str(work_dir / "records")
    cfg.validate()
    return cfg


def make_window(qtbot, config: AppConfig, **kwargs) -> MainWindow:
    logging.getLogger().setLevel(logging.INFO)
    window = MainWindow(
        config,
        source_factory=synthetic_factory,
        player_factory=lambda **options: FakePlayer(**options),
        persist_config=False,
        quiet_errors=True,
        **kwargs,
    )
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture
def midi_path(work_dir, write_midi):
    return write_midi(work_dir / "scale.mid", SCALE)


# ===========================================================================
# 钢琴卷帘（FR-4.2）
# ===========================================================================
def test_note_label_follows_scientific_pitch() -> None:
    assert note_label(60) == "C4"
    assert note_label(69) == "A4"
    assert note_label(61) == "C#4"
    assert note_label(36) == "C2"


def test_piano_roll_bars_match_note_times(qtbot, midi_path) -> None:
    """A7 的核心判据：音符条的起点与长度 == MIDI 里的起止时刻。"""
    from zpyaudio.media.midi_loader import load_midi

    view = PianoRollView()
    qtbot.addWidget(view)
    document = load_midi(midi_path)
    view.set_document(document)

    assert view.note_count == len(SCALE) == 4
    starts = [round(float(value), 3) for value in view._bars.opts["x0"]]
    pitches = [int(value) for value in view._bars.opts["y"]]
    widths = [round(float(value), 3) for value in view._bars.opts["width"]]
    assert starts == [0.0, 1.0, 2.0, 3.0]
    assert pitches == [60, 64, 67, 72]
    assert widths == [pytest.approx(0.5)] * 4


def test_piano_roll_axis_is_labelled_with_note_names(qtbot, midi_path) -> None:
    from zpyaudio.media.midi_loader import load_midi

    view = PianoRollView()
    qtbot.addWidget(view)
    view.set_document(load_midi(midi_path))
    labels = view.note_axis_labels
    assert "C4" in labels and "C5" in labels


def test_piano_roll_clear_notes(qtbot) -> None:
    view = PianoRollView()
    qtbot.addWidget(view)
    assert view.note_count == 0
    assert view.document is None
    view.clear_notes()
    assert view.note_count == 0
    assert len(view._bars.opts["x0"]) == 0


def test_piano_roll_wide_range_labels_only_c_octaves(qtbot, work_dir, write_midi) -> None:
    """音域超过两个八度时只标每个八度的 C，避免刻度挤成一团。"""
    from zpyaudio.media.midi_loader import load_midi

    path = write_midi(work_dir / "wide.mid", [(0.0, 30, 0.5, 100, 0, 0), (1.0, 90, 0.5, 100, 0, 0)])
    view = PianoRollView()
    qtbot.addWidget(view)
    view.set_document(load_midi(path))
    labels = view.note_axis_labels
    assert labels and all(text.startswith("C") for text in labels)


# ===========================================================================
# 符号数据频率曲线（FR-4.3 / FR-4.4）
# ===========================================================================
def test_symbolic_curve_is_labelled_and_hides_acoustics(qtbot, midi_path) -> None:
    from zpyaudio.media.midi_loader import load_midi

    view = PitchView(10.0)
    qtbot.addWidget(view)
    document = load_midi(midi_path)

    drawn = view.show_symbolic(document.notes, source_label="MIDI", duration=document.duration)

    assert drawn == 4
    assert view.symbolic is True
    assert view.symbolic_note_count == 4
    title = view.getPlotItem().titleLabel.text
    assert SYMBOLIC_LABEL in title
    assert "不是声学主频" in title
    # 声学图元必须让位：曲线数据清空、符号曲线有数据
    assert curve_length(view._curve) == 0
    assert curve_length(view._symbolic_curve) > 0


def test_symbolic_curve_frequency_uses_fr43_formula(qtbot, midi_path) -> None:
    """曲线纵轴是 log10(频率)，C4 必须落在 log10(261.63) 上。"""
    import math

    from zpyaudio.media.midi_loader import load_midi

    view = PitchView(10.0)
    qtbot.addWidget(view)
    view.show_symbolic(load_midi(midi_path).notes)
    _x, y = view._symbolic_curve.getData()
    values = [float(value) for value in y if value == value]  # 去掉 NaN 断点
    assert min(values) == pytest.approx(math.log10(261.625565), abs=1e-4)
    assert max(values) == pytest.approx(math.log10(523.251131), abs=1e-4)


def test_symbolic_mode_uses_absolute_time_axis(qtbot, midi_path) -> None:
    """符号模式的横轴是 0…曲长（不是声学模式的"最近 N 秒"）。"""
    from zpyaudio.media.midi_loader import load_midi

    view = PitchView(10.0)
    qtbot.addWidget(view)
    document = load_midi(midi_path)
    view.show_symbolic(document.notes, duration=document.duration)
    (x_min, x_max), _ = view.viewRange()
    # 横轴从 0 覆盖到曲长（两侧留 1% padding，避免首尾音符贴在边框上）
    assert x_min <= 0.0
    assert x_max >= document.duration
    assert (x_max - x_min) == pytest.approx(document.duration, rel=0.05)


def test_clear_symbolic_restores_acoustic_mode(qtbot, midi_path) -> None:
    from zpyaudio.media.midi_loader import load_midi

    view = PitchView(10.0)
    qtbot.addWidget(view)
    view.show_symbolic(load_midi(midi_path).notes)
    view.clear_symbolic()

    assert view.symbolic is False
    assert view.symbolic_note_count == 0
    assert curve_length(view._symbolic_curve) == 0
    assert "符号数据" not in view.getPlotItem().titleLabel.text
    (x_min, x_max), _ = view.viewRange()
    assert x_max == pytest.approx(0.0) and x_min == pytest.approx(-10.0)


def test_acoustic_frame_exits_symbolic_mode(qtbot, midi_path) -> None:
    """一旦有声学帧进来（开始新的采集），自动退回声学模式，避免两套数据混画。"""
    from zpyaudio.core.analyzer import Analyzer, AnalyzerConfig
    from zpyaudio.media.midi_loader import load_midi

    view = PitchView(10.0)
    qtbot.addWidget(view)
    view.show_symbolic(load_midi(midi_path).notes)
    assert view.symbolic is True

    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=4096))
    t = np.arange(4096, dtype=np.float64) / FS
    signal = (0.5 * np.sin(2 * np.pi * 1000.0 * t)).astype(np.float32)
    view.update_snapshot(analyzer.analyze(signal, t=0.0))

    assert view.symbolic is False
    assert view.point_count > 0


# ===========================================================================
# 主窗口：打开 MIDI 的完整流程（A7）
# ===========================================================================
def test_piano_roll_hidden_before_any_midi(qtbot, config) -> None:
    window = make_window(qtbot, config)
    try:
        assert window._piano_roll_shown is False
        assert window.piano_roll.note_count == 0
        assert window.pitch_view.symbolic is False
        assert window.controller.midi_document is None
    finally:
        window.close()


def test_open_midi_shows_piano_roll_and_symbolic_curve(qtbot, config, midi_path) -> None:
    """A7 端到端：打开 MIDI → 卷帘出现且音符数正确、主频区切到符号数据、状态栏给出 MIDI 信息。"""
    window = make_window(qtbot, config)
    try:
        assert window.load_midi(str(midi_path)) is True

        document = window.controller.midi_document
        assert document is not None and document.note_count == 4
        assert window._piano_roll_shown is True
        assert window.piano_roll.note_count == 4
        assert window._splitter.sizes()[2] > 0, "卷帘显示时必须真的分到高度"
        assert window.pitch_view.symbolic is True
        assert SYMBOLIC_LABEL in window.pitch_view.getPlotItem().titleLabel.text
        assert "MIDI 符号数据" in window._status_level.text()
        assert window.controller.state is AppState.IDLE, "MIDI 是符号数据，不进入播放/采集状态"
    finally:
        window.close()


def test_open_audio_file_hides_piano_roll(qtbot, config, midi_path, work_dir) -> None:
    """规格书 7.1：卷帘"仅 MIDI 文件时显示"，打开音频后必须隐藏。"""
    window = make_window(qtbot, config)
    try:
        window.load_midi(str(midi_path))
        assert window._piano_roll_shown is True

        tone = write_tone(work_dir / "tone.wav", seconds=0.5)
        window.play_file(str(tone))

        assert window.controller.midi_document is None
        assert window._piano_roll_shown is False
        assert window._splitter.sizes()[2] == 0
        assert window.piano_roll.note_count == 0
        assert window.pitch_view.symbolic is False
        assert window.controller.state is AppState.PLAYING
    finally:
        window.close()


def test_open_midi_while_monitoring_stops_first(qtbot, config, midi_path) -> None:
    window = make_window(qtbot, config)
    try:
        window.start_monitoring()
        assert window.controller.state is AppState.MONITORING

        assert window.load_midi(str(midi_path)) is True
        assert window.controller.state is AppState.IDLE
        assert window.controller.midi_document is not None
        assert window._piano_roll_shown is True
    finally:
        window.close()


def test_record_after_midi_clears_symbolic_mode(qtbot, config, midi_path) -> None:
    window = make_window(qtbot, config)
    try:
        window.load_midi(str(midi_path))
        window.start_monitoring()
        assert window.controller.midi_document is None
        assert window._piano_roll_shown is False
        assert window.pitch_view.symbolic is False
    finally:
        window.close()


def test_corrupt_midi_reports_error_and_stays_idle(qtbot, config, work_dir, caplog) -> None:
    """A8 同类：坏文件要有中文提示、回到 IDLE、可继续操作（不崩）。"""
    broken = work_dir / "broken.mid"
    broken.write_bytes(b"not a midi file at all" * 4)
    window = make_window(qtbot, config)
    try:
        with caplog.at_level(logging.ERROR):
            assert window.load_midi(str(broken)) is False
        assert any("打开 MIDI 失败" in record.message for record in caplog.records)
        assert window.controller.state is AppState.IDLE
        assert window._piano_roll_shown is False
        assert window.controller.midi_document is None
    finally:
        window.close()


def test_restore_defaults_clears_midi(qtbot, config, midi_path) -> None:
    window = make_window(qtbot, config)
    try:
        window.load_midi(str(midi_path))
        window.restore_defaults()
        assert window.controller.midi_document is None
        assert window._piano_roll_shown is False
        assert window.pitch_view.symbolic is False
    finally:
        window.close()
