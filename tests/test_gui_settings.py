"""ffmpeg 路径设置（FR-6.4）的界面用例。

覆盖三件事：

1. 启动时**自动检测** ffmpeg 并写回配置（用户配置优先）；
2. 「文件 → 设置 ffmpeg 路径…」对话框的状态提示、浏览与「自动检测」按钮；
3. 用户手动指定路径后**不被自动检测覆盖**，并立即重新定位（无需重启）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog  # noqa: E402

from zpyaudio.app.config import AppConfig  # noqa: E402
from zpyaudio.gui import settings_dialog as settings_module  # noqa: E402
from zpyaudio.gui.main_window import MainWindow  # noqa: E402
from zpyaudio.gui.settings_dialog import SettingsDialog  # noqa: E402
from zpyaudio.media import decoder as decoder_module  # noqa: E402
from zpyaudio.sources.synthetic_source import SyntheticSource  # noqa: E402

from fakes import FakePlayer  # noqa: E402

FS = 48_000


def stub_ffmpeg(path):
    """造一个占位 ffmpeg 可执行文件（只用于定位判断，不执行）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


def synthetic_factory(ring) -> SyntheticSource:
    return SyntheticSource(
        ring, sample_rate=FS, channels=1, frequency=1000.0, amplitude=0.5, block_size=1024
    )


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.validate()
    return cfg


def make_window(qtbot, config: AppConfig) -> MainWindow:
    window = MainWindow(
        config,
        source_factory=synthetic_factory,
        player_factory=lambda **options: FakePlayer(**options),
        persist_config=False,
        quiet_errors=True,
    )
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture
def window(qtbot, config: AppConfig):
    win = make_window(qtbot, config)
    yield win
    win.close()


# ---------------------------------------------------------------- 启动自动检测
def test_startup_autodetects_when_unset(qtbot, config: AppConfig, work_dir, monkeypatch) -> None:
    """配置为空 → 启动时自动检测并写回 ffmpeg_dir（用户下次可见、可改）。"""
    tool = stub_ffmpeg(work_dir / "auto" / "ffmpeg.exe")
    monkeypatch.setattr(decoder_module, "find_ffmpeg", lambda extra=None: tool)

    win = make_window(qtbot, config)
    try:
        assert win.ffmpeg_tool == tool
        assert config.ffmpeg_dir == str(tool)
    finally:
        win.close()


def test_startup_keeps_user_path(qtbot, work_dir, monkeypatch) -> None:
    """用户已指定可用路径 → 启动时不再自动检测、不覆盖。"""
    tool = stub_ffmpeg(work_dir / "mine" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=str(tool))
    monkeypatch.setattr(
        decoder_module, "find_ffmpeg", lambda extra=None: pytest.fail("用户路径可用时不应自动检测")
    )

    win = make_window(qtbot, config)
    try:
        assert win.ffmpeg_tool == tool
        assert config.ffmpeg_dir == str(tool)
    finally:
        win.close()


# ---------------------------------------------------------------- 对话框
def test_dialog_reports_autodetected_ffmpeg(qtbot, config: AppConfig, work_dir, monkeypatch) -> None:
    tool = stub_ffmpeg(work_dir / "auto" / "ffmpeg.exe")
    monkeypatch.setattr(settings_module, "find_ffmpeg", lambda: tool)
    monkeypatch.setattr(settings_module, "ffmpeg_version", lambda _tool=None: "ffmpeg version stub")

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    assert dialog.ffmpeg_path() == ""
    assert str(tool) in dialog.status_label.text()
    assert "自动检测到" in dialog.status_label.text()
    assert "ffmpeg version stub" in dialog.status_label.text()


def test_dialog_flags_unusable_user_path(qtbot, config: AppConfig, work_dir, monkeypatch) -> None:
    """用户路径写错时状态行必须报"未找到"，而不是显示别处检测到的 ffmpeg。"""
    monkeypatch.setattr(
        settings_module, "find_ffmpeg", lambda: stub_ffmpeg(work_dir / "elsewhere" / "ffmpeg.exe")
    )
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.path_edit.setText(str(work_dir / "missing" / "ffmpeg.exe"))

    assert "未找到" in dialog.status_label.text()


def test_dialog_autodetect_button_fills_path(qtbot, config: AppConfig, work_dir, monkeypatch) -> None:
    tool = stub_ffmpeg(work_dir / "auto" / "ffmpeg.exe")
    monkeypatch.setattr(settings_module, "find_ffmpeg", lambda: tool)
    monkeypatch.setattr(settings_module, "ffmpeg_version", lambda _tool=None: "ffmpeg version stub")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.detect_button.click()

    assert dialog.ffmpeg_path() == str(tool)
    assert dialog.autodetect_enabled() is True


def test_dialog_accepts_valid_path(qtbot, config: AppConfig, work_dir, monkeypatch) -> None:
    """路径可用时「确定」直接通过（不会弹确认框）。"""
    tool = stub_ffmpeg(work_dir / "mine" / "ffmpeg.exe")
    monkeypatch.setattr(settings_module, "ffmpeg_version", lambda _tool=None: "ffmpeg version stub")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.path_edit.setText(str(tool))

    dialog.accept()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.ffmpeg_path() == str(tool)


# ---------------------------------------------------------------- 菜单与配置写回
def test_file_menu_has_ffmpeg_settings_action(window: MainWindow) -> None:
    file_menu = window.menuBar().actions()[0].menu()
    titles = [action.text() for action in file_menu.actions()]

    assert "设置 ffmpeg 路径…" in titles


def test_open_settings_writes_config(qtbot, work_dir, monkeypatch) -> None:
    """对话框确认后：写回配置 + 立即重新定位 + 立即可用（不必重启）。"""
    tool = stub_ffmpeg(work_dir / "picked" / "ffmpeg.exe")
    config = AppConfig()

    class FakeDialog:
        def __init__(self, cfg, parent=None) -> None:  # noqa: ANN001
            self.cfg = cfg

        def exec(self):  # noqa: ANN201
            return QDialog.DialogCode.Accepted

        def ffmpeg_path(self) -> str:
            return str(tool)

        def autodetect_enabled(self) -> bool:
            return False

    monkeypatch.setattr(settings_module, "SettingsDialog", FakeDialog)
    win = make_window(qtbot, config)
    try:
        win.open_settings()

        assert config.ffmpeg_dir == str(tool)
        assert config.ffmpeg_autodetect is False
        assert win.ffmpeg_tool == tool
    finally:
        win.close()


def test_open_settings_cancel_keeps_config(qtbot, config: AppConfig, monkeypatch) -> None:
    class FakeDialog:
        def __init__(self, cfg, parent=None) -> None:  # noqa: ANN001
            self.cfg = cfg

        def exec(self):  # noqa: ANN201
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(settings_module, "SettingsDialog", FakeDialog)
    win = make_window(qtbot, config)
    try:
        before = (config.ffmpeg_dir, config.ffmpeg_autodetect)
        win.open_settings()

        assert (config.ffmpeg_dir, config.ffmpeg_autodetect) == before
    finally:
        win.close()


def test_restore_defaults_reruns_autodetect(qtbot, work_dir, monkeypatch) -> None:
    """恢复默认设置后 ffmpeg 回到"自动检测"，并立即重新检测（FR-6.3 + FR-6.4）。"""
    detected = stub_ffmpeg(work_dir / "auto" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=str(work_dir / "custom" / "ffmpeg.exe"))
    monkeypatch.setattr(decoder_module, "find_ffmpeg", lambda extra=None: detected)
    win = make_window(qtbot, config)
    try:
        win.restore_defaults()

        assert config.ffmpeg_autodetect is True
        assert win.ffmpeg_tool == detected
    finally:
        win.close()
