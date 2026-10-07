"""文件源测试（规格书 4.5：准实时推进、seek、暂停）。"""

from __future__ import annotations

import time

import numpy as np
import pytest
import soundfile as sf

from zpyaudio.core.ringbuffer import RingBuffer
from zpyaudio.sources.file_source import FileSource

FS = 48_000


def write_tone(path, *, seconds: float = 0.4, channels: int = 1, frequency: float = 1000.0):
    t = np.arange(int(seconds * FS)) / FS
    mono = (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
    data = np.repeat(mono[:, None], channels, axis=1) if channels > 1 else mono
    sf.write(str(path), data, FS, subtype="PCM_16")
    return path


@pytest.fixture
def tone_file(work_dir):
    return write_tone(work_dir / "tone.wav")


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_file_source_reads_everything_to_ring(tone_file) -> None:
    ring = RingBuffer(capacity=FS * 2, channels=1)
    source = FileSource(ring, tone_file, realtime=False)
    try:
        source.open()
        assert source.duration_seconds == pytest.approx(0.4, abs=0.01)
        source.start()
        assert _wait_until(lambda: source.eof), "应读到文件结尾"
        position = source.position_seconds
    finally:
        source.close()

    assert ring.written == pytest.approx(int(0.4 * FS), abs=FS * 0.01)
    assert position == pytest.approx(0.4, abs=0.02)


def test_file_source_realtime_pacing_is_close_to_duration(tone_file) -> None:
    """实时模式下源按样本计数推进：读完 0.4 s 文件约需 0.4 s 减去预填提前量。"""
    ring = RingBuffer(capacity=FS * 2, channels=1)
    source = FileSource(ring, tone_file, realtime=True)
    try:
        source.open()
        start = time.perf_counter()
        source.start()
        assert _wait_until(lambda: source.eof)
        elapsed = time.perf_counter() - start
    finally:
        source.close()

    # 预填 6 块（0.128 s）是"提前产出"，因此允许比文件时长略短，但绝不能瞬间读完
    assert 0.15 <= elapsed <= 1.0


def test_file_source_non_realtime_reads_immediately(tone_file) -> None:
    ring = RingBuffer(capacity=FS * 2, channels=1)
    source = FileSource(ring, tone_file, realtime=False)
    try:
        source.open()
        start = time.perf_counter()
        source.start()
        assert _wait_until(lambda: source.eof)
        elapsed = time.perf_counter() - start
    finally:
        source.close()

    assert elapsed < 0.15


def test_file_source_seek_moves_position(tone_file) -> None:
    ring = RingBuffer(capacity=FS * 2, channels=1)
    source = FileSource(ring, tone_file, realtime=False)
    try:
        source.open()
        source.start()
        assert _wait_until(lambda: source.eof)
        source.seek(0.1)
        assert source.position_seconds == pytest.approx(0.1, abs=0.02)
        assert not source.eof, "定位后应清除 EOF 标记"
    finally:
        source.close()


def test_file_source_pause_freezes_output(tone_file) -> None:
    ring = RingBuffer(capacity=FS * 2, channels=1)
    source = FileSource(ring, tone_file, realtime=True, prefill_blocks=2)
    try:
        source.open()
        source.start()
        assert _wait_until(lambda: ring.written > 0)

        source.pause()
        time.sleep(0.05)
        frozen = ring.written
        time.sleep(0.2)
        assert ring.written == frozen, "暂停期间不应继续产出"

        source.resume()
        assert _wait_until(lambda: ring.written > frozen), "恢复后应继续产出"
    finally:
        source.close()


def test_file_source_downmixes_ring_but_keeps_frame_channels(work_dir) -> None:
    """环形缓冲（分析）用单声道，块回调（播放）保留立体声（规格书 4.2）。"""
    path = write_tone(work_dir / "stereo.wav", seconds=0.2, channels=2)
    ring = RingBuffer(capacity=FS, channels=1)
    frames = []
    source = FileSource(ring, path, realtime=False, on_block=frames.append)
    try:
        source.open()
        assert source.channels == 2
        source.start()
        assert _wait_until(lambda: source.eof)
    finally:
        source.close()

    assert ring.channels == 1
    assert ring.written > 0
    assert frames and frames[0].channels == 2
    assert frames[0].samples.shape[1] == 2


def test_file_source_reports_format_info(tone_file) -> None:
    ring = RingBuffer(capacity=FS, channels=1)
    source = FileSource(ring, tone_file)
    try:
        source.open()
        assert source.info is not None
        assert source.info.sample_rate == FS
        assert source.sample_rate == FS
    finally:
        source.close()


def test_file_source_close_is_idempotent(tone_file) -> None:
    ring = RingBuffer(capacity=FS, channels=1)
    source = FileSource(ring, tone_file)
    source.open()

    source.close()
    source.close()

    assert source.position_seconds == 0.0
