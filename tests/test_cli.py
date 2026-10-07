"""命令行入口测试（M0 / M1 验收的可自动化部分）。"""

from __future__ import annotations

import pytest

from zpyaudio.app.cli import _run_audio_check, _run_midi_check, build_parser
from zpyaudio.app.config import AppConfig


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.config is None
    assert args.log_level is None
    assert args.list_devices is False
    assert args.self_test is False
    assert args.self_test_ms == 800
    assert args.audio_check is None
    assert args.source == "synthetic"
    assert args.tone_frequency == 1000.0
    assert args.demo_tone is None


def test_parser_accepts_all_options() -> None:
    args = build_parser().parse_args(
        [
            "--audio-check",
            "0.5",
            "--source",
            "device",
            "--tone-frequency",
            "440",
            "--demo-tone",
            "1000",
            "--log-level",
            "DEBUG",
            "--self-test",
            "--list-devices",
        ]
    )

    assert args.audio_check == 0.5
    assert args.source == "device"
    assert args.tone_frequency == 440.0
    assert args.demo_tone == 1000.0
    assert args.log_level == "DEBUG"
    assert args.self_test is True
    assert args.list_devices is True
    assert args.self_test_shot is None


def test_parser_accepts_self_test_shot() -> None:
    args = build_parser().parse_args(["--self-test", "--self-test-shot", "images/shot.png"])

    assert args.self_test_shot.name == "shot.png"


def test_gui_self_test_creates_window_and_optional_shot(work_dir) -> None:
    """M0 验收：离屏起窗口并能产出布局截图（走真实命令行入口）。

    必须在**子进程**中运行：在 pytest 进程内调用 ``QApplication.exec()`` 会让
    后续 GUI 用例的 ``qtbot.wait()`` 立即返回，污染其它测试。
    """
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    shot = work_dir / "shot.png"

    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "main.py"),
            "--self-test",
            "--self-test-ms",
            "600",
            "--demo-tone",
            "1000",
            "--self-test-shot",
            str(shot),
            "--log-level",
            "WARNING",
        ],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0
    assert shot.exists() and shot.stat().st_size > 0


def test_parser_rejects_unknown_source() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--source", "magic"])


def test_audio_check_synthetic_passes(default_config: AppConfig, capsys) -> None:
    """M1 端到端：合成 1 kHz → 环形缓冲 → 分析，主频偏差 ≤ 2 Hz。"""
    code = _run_audio_check(default_config, 0.5, "synthetic", 1000.0)
    output = capsys.readouterr().out

    assert code == 0, output
    assert "自检结论      : PASS" in output
    assert "主频中位数" in output
    assert "环形缓冲      : overrun 0 次" in output


def test_audio_check_reports_frame_count(default_config: AppConfig, capsys) -> None:
    """理论帧数应扣除窗长预热（规格书 4.1）。"""
    from zpyaudio.app.controller import HOP_SECONDS

    seconds = 0.4
    _run_audio_check(default_config, seconds, "synthetic", 440.0)
    output = capsys.readouterr().out

    warmup = default_config.fft_size / default_config.sample_rate
    expected = (seconds - warmup) / HOP_SECONDS
    assert f"/ 理论 {expected:.0f}" in output
    assert "窗长预热" in output


def test_audio_check_fails_when_too_short(default_config: AppConfig, capsys) -> None:
    """时长不足以填满一个分析窗时，应判定 FAIL 并返回 1。"""
    config = AppConfig()
    config.fft_size = 4096

    code = _run_audio_check(config, 0.02, "synthetic", 1000.0)
    output = capsys.readouterr().out

    assert code == 1
    assert "FAIL" in output


# ---------------------------------------------------------------- M5：A7 自检
def test_parser_accepts_midi_check() -> None:
    args = build_parser().parse_args(["--midi-check", "song.mid"])
    assert args.midi_check.name == "song.mid"


def test_parser_defaults_include_midi_check() -> None:
    assert build_parser().parse_args([]).midi_check is None


def test_midi_check_passes_on_valid_file(work_dir, write_midi, capsys) -> None:
    """A7：音符数 == mido note_on 数、C4 = 261.63 Hz、起止合法且有序。"""
    path = write_midi(
        work_dir / "scale.mid",
        [
            (0.0, 60, 0.5, 100, 0, 0),
            (1.0, 64, 0.5, 100, 0, 0),
            (2.0, 67, 0.5, 100, 0, 0),
        ],
    )
    code = _run_midi_check(path)
    output = capsys.readouterr().out

    assert code == 0, output
    assert "音符事件      : 3 个（mido note_on 3 个）" in output
    assert "C4（note 60） : 261.63 Hz" in output
    assert "自检结论      : PASS" in output


def test_midi_check_fails_on_broken_file(work_dir, capsys) -> None:
    broken = work_dir / "broken.mid"
    broken.write_bytes(b"still not a midi" * 4)

    assert _run_midi_check(broken) == 1
    assert "打开 MIDI 失败" in capsys.readouterr().out


def test_parser_accepts_demo_and_self_test_midi() -> None:
    args = build_parser().parse_args(["--demo-midi", "--self-test-midi", "a.mid"])

    assert args.demo_midi is True
    assert args.self_test_midi.name == "a.mid"


def test_parser_defaults_have_no_midi_flags() -> None:
    args = build_parser().parse_args([])

    assert args.demo_midi is False
    assert args.self_test_midi is None


def test_demo_midi_is_written_and_loadable(work_dir) -> None:
    """``--demo-midi`` 的素材（A7 可视验收用）必须真的能被自己的解析器读出来。"""
    from zpyaudio.app.cli import DEMO_MIDI_NOTES, _write_demo_midi
    from zpyaudio.media.midi_loader import load_midi

    path = _write_demo_midi(work_dir / "demo.mid")
    assert path.exists() and path.stat().st_size > 0

    document = load_midi(path)
    assert document.note_count == len(DEMO_MIDI_NOTES)
    assert document.note_range == (60, 74)  # C4 → D5
    assert 60 in {item.note for item in document.notes}


def test_demo_midi_passes_a7_check(work_dir, capsys) -> None:
    from zpyaudio.app.cli import _write_demo_midi

    path = _write_demo_midi(work_dir / "demo.mid")
    assert _run_midi_check(path) == 0
    assert "PASS" in capsys.readouterr().out
