"""GUI 冒烟测试（M0 验收：程序能起窗口；M1：波形与频谱有数据）。

全部以离屏平台运行（``conftest.py`` 已设置 ``QT_QPA_PLATFORM=offscreen``）。
"""

from __future__ import annotations

import logging
import re

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QSplitter  # noqa: E402

from zpyaudio.app.config import AppConfig  # noqa: E402
from zpyaudio.app.controller import AppState  # noqa: E402
from zpyaudio.gui.main_window import MainWindow  # noqa: E402
from zpyaudio.gui.pitch_data_view import DEFAULT_MAX_LINES  # noqa: E402
from zpyaudio.sources.synthetic_source import SyntheticSource  # noqa: E402

FS = 48_000


def synthetic_factory(ring) -> SyntheticSource:
    return SyntheticSource(
        ring, sample_rate=FS, channels=1, frequency=1000.0, amplitude=0.5, block_size=1024
    )


@pytest.fixture
def window(qtbot, default_config: AppConfig):
    """离屏主窗口，日志级别调低以便断言日志区内容，且不落盘 config.json。"""
    logging.getLogger().setLevel(logging.INFO)
    win = MainWindow(default_config, source_factory=synthetic_factory, persist_config=False)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.close()


def test_window_builds_expected_widgets(window: MainWindow) -> None:
    """M0 验收：主窗口可正常创建。"""
    assert window.waveform_view is not None
    assert window.spectrum_view is not None
    assert window.log_view is not None
    assert window.pitch_data_view is not None
    assert window.control_bar.start_button.isEnabled()
    assert not window.control_bar.stop_button.isEnabled()
    assert "空闲" in window._status_state.text()


def test_pitch_data_view_sits_right_of_log(window: MainWindow) -> None:
    """规格书 v0.5 变更 2：日志右侧为 2:1 的横向分割区。"""
    bottom = window.log_view.parentWidget().parentWidget()

    assert isinstance(bottom, QSplitter)
    assert bottom.orientation() == Qt.Orientation.Horizontal
    assert bottom.count() == 2
    assert bottom.widget(0) is window.log_view.parentWidget()
    assert bottom.widget(1) is window.pitch_data_view.parentWidget()
    assert bottom.sizes()[1] < bottom.sizes()[0], "数据区应窄于日志区（约 1/3）"
    assert window.pitch_data_view.max_lines == DEFAULT_MAX_LINES
    assert window.pitch_data_view.row_count == 1, "初始只有列头"


def test_control_bar_lists_fft_and_window_options(window: MainWindow) -> None:
    fft_items = [
        window.control_bar.fft_combo.itemData(i)
        for i in range(window.control_bar.fft_combo.count())
    ]
    window_items = [
        window.control_bar.window_combo.itemData(i)
        for i in range(window.control_bar.window_combo.count())
    ]

    assert fft_items == [1024, 2048, 4096]
    assert window_items == ["hann", "hamming", "blackman"]
    assert window.control_bar.device_combo.count() >= 1


def test_start_stop_renders_waveform_and_spectrum(window: MainWindow, qtbot) -> None:
    """M1 验收：开始后波形与频谱曲线均有数据，按钮按状态机置灰。"""
    window.start_monitoring()
    assert window.controller.state is AppState.MONITORING
    assert not window.control_bar.start_button.isEnabled()
    assert window.control_bar.stop_button.isEnabled()

    qtbot.wait(800)

    wave_x, wave_y = window.waveform_view._curve.getData()
    assert wave_x is not None and len(wave_x) > 0, "波形应已绘制"
    spec_x, spec_y = window.spectrum_view._curve.getData()
    assert spec_y is not None and len(spec_y) > 0, "频谱应已绘制"
    assert "峰值" in window.spectrum_view.title_text()

    window.stop_monitoring()
    assert window.controller.state is AppState.IDLE
    assert window.control_bar.start_button.isEnabled()
    assert not window.control_bar.stop_button.isEnabled()


def test_status_bar_shows_pitch_readout(window: MainWindow, qtbot) -> None:
    window.start_monitoring()
    qtbot.wait(600)

    text = window._status_level.text()
    window.stop_monitoring()

    assert "RMS" in text
    assert "主频" in text


def test_pitch_data_view_shows_frequency_and_note(window: MainWindow, qtbot) -> None:
    """v0.5 变更 1/2：数据区逐帧输出主频与音名，状态栏与图标题同步。"""
    window.start_monitoring()
    qtbot.wait(700)

    rows = window.pitch_data_view.row_count
    text = window.pitch_data_view.toPlainText()
    status = window._status_level.text()
    title = window.pitch_view.plotItem.titleLabel.text
    window.stop_monitoring()

    assert rows > 1, "监听期间数据区应有数据行"
    assert "B5" in text, "1 kHz 合成正弦应报 B5 音名"
    assert re.search(r"[A-G]#?\d", status), "状态栏应同时输出音名"
    assert "B5" in title, "主频图标题应同时输出音名"


def test_pitch_display_clears_on_new_session(window: MainWindow, qtbot) -> None:
    """开始新一次采集时清空主频时序图与数据区。"""
    window.start_monitoring()
    qtbot.wait(400)
    window.stop_monitoring()
    assert window.pitch_data_view.row_count > 1

    window.start_monitoring()
    cleared = window.pitch_data_view.row_count
    window.stop_monitoring()

    assert cleared == 1, "开始新一次采集时数据区应清空（仅保留列头）"


def test_log_view_receives_messages(window: MainWindow, qtbot) -> None:
    window.start_monitoring()
    qtbot.wait(300)
    window.stop_monitoring()
    qtbot.wait(50)

    content = window.log_view.toPlainText()

    assert "开始实时分析" in content
    assert "状态：空闲 → 监听中" in content


def test_close_stops_timers_and_detaches_logging(qtbot, default_config: AppConfig) -> None:
    logging.getLogger().setLevel(logging.INFO)
    win = MainWindow(default_config, source_factory=synthetic_factory, persist_config=False)
    qtbot.addWidget(win)
    win.show()
    win.start_monitoring()
    qtbot.wait(200)

    win.close()

    assert not win._wave_timer.isActive()
    assert not win._snapshot_timer.isActive()
    assert win.controller.state is AppState.IDLE
    assert win.log_view._handler not in logging.getLogger().handlers


def test_settings_change_updates_config(window: MainWindow) -> None:
    window._on_settings_changed({"fft_size": 2048, "window": "hamming"})

    assert window.config.fft_size == 2048
    assert window.config.window == "hamming"
