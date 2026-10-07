"""录制器测试（规格书 FR-1、验收 A4 的基础）。"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
import soundfile as sf

from zpyaudio.core.frames import AudioFrame
from zpyaudio.media.recorder import Recorder, build_record_path, sanitize_name

FS = 48_000


def _frame(value: float, frames: int = 1024, channels: int = 1, index: int = 0) -> AudioFrame:
    samples = np.full((frames, channels), value, dtype=np.float32)
    return AudioFrame(samples, FS, channels, index * frames / FS)


def test_build_record_path_format(work_dir) -> None:
    path = build_record_path(work_dir, "麦克风 (USB)", now=datetime(2026, 10, 5, 16, 30, 5))

    assert path.name == "20261005_163005_麦克风_(USB).wav"
    assert path.parent == work_dir


def test_build_record_path_avoids_collision(work_dir) -> None:
    now = datetime(2026, 10, 5, 16, 30, 5)
    first = build_record_path(work_dir, "mic", now=now)
    first.write_bytes(b"x")

    second = build_record_path(work_dir, "mic", now=now)

    assert second != first
    assert second.name == "20261005_163005_mic_1.wav"


def test_sanitize_name_removes_illegal_characters() -> None:
    assert sanitize_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_name("   多余   空格   ") == "多余_空格"
    assert sanitize_name("") == "device"


def test_recorder_roundtrip_writes_readable_wav(work_dir) -> None:
    """A4 基础：写出的 wav 可被 soundfile 读回，内容与写入一致。"""
    recorder = Recorder(work_dir / "rec.wav", sample_rate=FS, channels=1)
    recorder.open()
    recorder.start()
    for index in range(20):
        recorder.feed(_frame(0.25, index=index))

    stats = recorder.stop()

    assert stats.frames == 20 * 1024
    assert stats.duration == pytest.approx(20 * 1024 / FS)
    assert stats.dropped_frames == 0
    assert stats.size_bytes > 0
    assert stats.path.exists()

    data, rate = sf.read(str(stats.path), dtype="float32")
    assert rate == FS
    assert data.shape[0] == 20 * 1024
    assert float(np.abs(data).max()) == pytest.approx(0.25, abs=0.001)


def test_recorder_uses_configured_subtype(work_dir) -> None:
    recorder = Recorder(work_dir / "rec24.wav", sample_rate=FS, channels=1, subtype="PCM_24")
    recorder.open()
    recorder.start()
    recorder.feed(_frame(0.5))
    stats = recorder.stop()

    assert sf.info(str(stats.path)).subtype == "PCM_24"


def test_recorder_adapts_channel_count(work_dir) -> None:
    """立体声帧写入单声道录制器时应降混，而不是报错。"""
    recorder = Recorder(work_dir / "mono.wav", sample_rate=FS, channels=1)
    recorder.open()
    recorder.start()
    recorder.feed(_frame(0.5, channels=2))
    stats = recorder.stop()

    assert sf.info(str(stats.path)).channels == 1
    assert stats.frames == 1024


def test_recorder_describe_mentions_path_and_duration(work_dir) -> None:
    recorder = Recorder(work_dir / "rec.wav", sample_rate=FS, channels=1)
    recorder.open()
    recorder.start()
    recorder.feed(_frame(0.1))
    stats = recorder.stop()

    text = stats.describe()

    assert "rec.wav" in text
    assert "s，" in text or "s)" in text


def test_recorder_stop_is_safe_without_start(work_dir) -> None:
    recorder = Recorder(work_dir / "rec.wav", sample_rate=FS, channels=1)

    stats = recorder.stop()

    assert stats.frames == 0
    assert stats.duration == 0.0


def test_recorder_start_requires_open(work_dir) -> None:
    recorder = Recorder(work_dir / "rec.wav", sample_rate=FS, channels=1)

    with pytest.raises(RuntimeError, match="open"):
        recorder.start()
