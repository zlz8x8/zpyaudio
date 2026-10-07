"""GUI 媒体功能与异常处理测试（M3/M4：A4、A5、A8、FR-6.3）。

播放走 :class:`FakePlayer`，因此无需声卡即可验证界面与控制器联动。
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtCore import QPoint, Qt

pytest.importorskip("pytestqt")

from zpyaudio.app.config import AppConfig  # noqa: E402
from zpyaudio.app.controller import AppState  # noqa: E402
from zpyaudio.gui.main_window import MainWindow  # noqa: E402
from zpyaudio.gui.progress_panel import format_seconds  # noqa: E402
from zpyaudio.sources.synthetic_source import SyntheticSource  # noqa: E402

from fakes import FakePlayer  # noqa: E402

FS = 48_000


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
def window(qtbot, config: AppConfig):
    """离屏主窗口；结束时显式关闭，确保定时器与日志处理器都被释放。"""
    win = make_window(qtbot, config)
    yield win
    win.close()


# ---------------------------------------------------------------- 控件与状态
def test_buttons_follow_state_machine(window: MainWindow) -> None:
    bar = window.control_bar

    assert bar.start_button.isEnabled() and bar.record_button.isEnabled()
    assert bar.open_button.isEnabled()
    assert not bar.pause_button.isEnabled() and not bar.stop_button.isEnabled()

    window.start_monitoring()
    assert window.controller.state is AppState.MONITORING
    assert not bar.start_button.isEnabled() and not bar.record_button.isEnabled()
    assert bar.pause_button.isEnabled() and bar.stop_button.isEnabled()
    assert not bar.fft_combo.isEnabled(), "运行中不允许改分析参数"

    window.stop()
    assert window.controller.state is AppState.IDLE
    assert bar.start_button.isEnabled() and bar.fft_combo.isEnabled()
    assert not bar.stop_button.isEnabled()


def test_menus_are_present_and_toggle_views(window: MainWindow) -> None:

    titles = [action.text() for action in window.menuBar().actions()]
    assert "文件(&F)" in titles and "视图(&V)" in titles and "帮助(&H)" in titles

    window._action_log_x.setChecked(True)

    assert window.config.spectrum_log_x is True
    assert window.view_options.spectrum_log_check.isChecked()
    assert window.spectrum_view.log_x is True


# ---------------------------------------------------------------- 录制（A4）
def test_recording_from_gui_writes_file(qtbot, window: MainWindow) -> None:

    window.start_recording()
    assert window.controller.state is AppState.RECORDING
    qtbot.wait(500)

    # 录制无总时长：进度条为不确定态，标签显示已录时长
    assert window.progress_panel.bar.maximum() == 0
    assert window.progress_panel.label.text().startswith("00:0")

    window.stop()

    stats = window.controller.last_recording_stats
    assert stats is not None and stats.path.exists()
    assert stats.duration > 0.2
    assert window.progress_panel.label.text() == "00:00.0 / --:--"
    assert window.controller.state is AppState.IDLE


def test_pause_during_recording_toggles_button(qtbot, window: MainWindow) -> None:
    window.start_recording()
    qtbot.wait(200)

    window.toggle_pause()

    assert window.controller.state is AppState.PAUSED
    assert window.control_bar.pause_button.text() == "继续"

    window.toggle_pause()

    assert window.controller.state is AppState.RECORDING
    assert window.control_bar.pause_button.text() == "暂停"
    window.stop()


# ---------------------------------------------------------------- 播放（A5）
def test_playback_updates_progress_bar(qtbot, window: MainWindow, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=1.0)

    window.play_file(str(path))

    assert window.controller.state is AppState.PLAYING
    qtbot.wait(400)

    assert window.progress_panel.bar.value() > 0
    assert "/" in window.progress_panel.label.text()
    assert window.progress_panel.bar.maximum() == 1000

    window.stop()
    assert window.controller.state is AppState.IDLE
    assert window.progress_panel.bar.value() == 0


def test_seek_from_progress_bar(qtbot, window: MainWindow, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=2.0)
    window.play_file(str(path))
    window.controller.pause()

    window.progress_panel.seekRequested.emit(1.2)

    assert window.controller.file_source is not None
    assert window.controller.file_source.position_seconds == pytest.approx(1.2, abs=0.05)
    window.stop()


def test_progress_bar_click_seeks(qtbot, window: MainWindow, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=2.0)
    window.play_file(str(path))
    window.controller.pause()
    bar = window.progress_panel.bar
    bar.setFixedWidth(200)
    bar.set_duration(2.0)

    qtbot.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(100, bar.height() // 2))

    assert window.controller.file_source is not None
    assert window.controller.file_source.position_seconds == pytest.approx(1.0, abs=0.2)
    window.stop()


def test_playback_finishes_and_resets(qtbot, window: MainWindow, work_dir) -> None:
    path = write_tone(work_dir / "short.wav", seconds=0.25)
    window.play_file(str(path))

    qtbot.waitUntil(lambda: window.controller.state is AppState.IDLE, timeout=4000)

    assert window.progress_panel.bar.value() == 0


# ---------------------------------------------------------------- 异常（A8）
def test_missing_file_is_reported_without_dialog(window: MainWindow, caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="zpyaudio.gui.main_window"):
        window.play_file("不存在的目录/不存在的文件.wav")

    assert window.controller.state is AppState.IDLE
    assert "打开文件失败" in caplog.text


def test_corrupt_file_is_reported(qtbot, window: MainWindow, work_dir, caplog) -> None:
    broken = work_dir / "broken.mp3"
    broken.write_bytes(b"definitely not audio")

    with caplog.at_level(logging.ERROR, logger="zpyaudio.gui.main_window"):
        window.play_file(str(broken))

    assert window.controller.state is AppState.IDLE
    assert "打开文件失败" in caplog.text


def test_corrupt_midi_file_is_reported(qtbot, window: MainWindow, work_dir, caplog) -> None:
    """M5 起 MIDI 走符号分析分支：**坏文件**仍要给中文提示并留在空闲态。

    （旧版本是"一律拒绝 MIDI"，M5 已实现钢琴卷帘；"能打开"的正向路径见
    ``tests/test_gui_midi.py``，这里只守故障注入。）
    """
    midi = work_dir / "song.mid"
    midi.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60")

    with caplog.at_level(logging.ERROR, logger="zpyaudio.gui.main_window"):
        window.play_file(str(midi))

    assert window.controller.state is AppState.IDLE
    assert "打开 MIDI 失败" in caplog.text
    assert window.controller.midi_document is None
    assert window._piano_roll_shown is False


def test_unwritable_record_dir_keeps_idle(qtbot, window: MainWindow, work_dir, caplog) -> None:
    """磁盘/权限故障：报错但不崩溃，状态回到空闲（A8）。"""
    blocker = work_dir / "blocker"
    blocker.write_text("我是文件不是目录", encoding="utf-8")
    window.config.records_dir = str(blocker / "records")

    with caplog.at_level(logging.ERROR, logger="zpyaudio.gui.main_window"):
        window.start_recording()

    assert window.controller.state is AppState.IDLE
    assert "开始录制失败" in caplog.text


# ---------------------------------------------------------------- 恢复默认（FR-6.3）
def test_restore_defaults_resets_config_and_views(window: MainWindow) -> None:
    window.config.fft_size = 1024
    window.config.waveform_seconds = 1.0
    window.config.spectrum_log_x = True
    window.view_options.spectrum_log_check.setChecked(True)

    window.restore_defaults()

    assert window.config.fft_size == 4096
    assert window.config.waveform_seconds == 0.5
    assert window.config.spectrum_log_x is False
    assert window.control_bar.fft_combo.currentData() == 4096
    assert window.waveform_view.seconds == pytest.approx(0.5)
    assert window.spectrum_view.log_x is False
    assert window.controller.analyzer.config.window_size == 4096


def test_restore_defaults_stops_running_task(qtbot, window: MainWindow) -> None:
    window.start_recording()
    qtbot.wait(200)

    window.restore_defaults()

    assert window.controller.state is AppState.IDLE


# ---------------------------------------------------------------- 进度面板
@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "00:00.0"), (5.25, "00:05.2"), (65.3, "01:05.3"), (-1.0, "00:00.0")],
)
def test_format_seconds(seconds: float, expected: str) -> None:
    assert format_seconds(seconds) == expected
