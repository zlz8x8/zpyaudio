"""环形缓冲测试（规格书 4.6）。"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from zpyaudio.core.ringbuffer import RingBuffer


def _ramp(n: int, channels: int = 1, start: int = 0) -> np.ndarray:
    values = np.arange(start, start + n, dtype=np.float32)
    return np.repeat(values[:, None], channels, axis=1)


def test_read_latest_returns_most_recent_samples() -> None:
    ring = RingBuffer(capacity=16, channels=1)
    ring.write(_ramp(8))

    latest = ring.read_latest(4)

    assert latest is not None
    assert latest[:, 0].tolist() == [4.0, 5.0, 6.0, 7.0]
    assert ring.written == 8
    assert ring.available == 8


def test_read_latest_returns_none_when_not_enough_data() -> None:
    ring = RingBuffer(capacity=16, channels=1)
    ring.write(_ramp(4))

    assert ring.read_latest(8) is None
    assert ring.read_latest(4) is not None


def test_wraps_around_capacity() -> None:
    ring = RingBuffer(capacity=8, channels=1)
    ring.write(_ramp(6))
    ring.write(_ramp(6, start=6))

    latest = ring.read_latest(8)

    assert latest is not None
    assert latest[:, 0].tolist() == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]


def test_oversized_write_keeps_newest_and_counts_overrun() -> None:
    ring = RingBuffer(capacity=8, channels=1)

    stored = ring.write(_ramp(12))

    assert stored == 8
    assert ring.read_latest(8)[:, 0].tolist() == [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
    assert ring.overrun == 1
    assert ring.dropped_samples == 4


def test_multi_channel_write_and_read() -> None:
    ring = RingBuffer(capacity=8, channels=2)
    ring.write(_ramp(4, channels=2))

    latest = ring.read_latest(2)

    assert latest.shape == (2, 2)
    assert latest[:, 0].tolist() == [2.0, 3.0]
    assert latest[:, 1].tolist() == [2.0, 3.0]


def test_rejects_channel_mismatch() -> None:
    ring = RingBuffer(capacity=8, channels=2)

    with pytest.raises(ValueError, match="声道"):
        ring.write(np.zeros((4, 1), dtype=np.float32))


@pytest.mark.parametrize("n", [0, -1])
def test_rejects_invalid_read_size(n: int) -> None:
    ring = RingBuffer(capacity=8, channels=1)

    with pytest.raises(ValueError, match="n 必须为正"):
        ring.read_latest(n)


def test_rejects_oversized_read() -> None:
    ring = RingBuffer(capacity=8, channels=1)

    with pytest.raises(ValueError, match="超过容量"):
        ring.read_latest(9)


def test_clear_resets_state() -> None:
    ring = RingBuffer(capacity=8, channels=1)
    ring.write(_ramp(9))

    ring.clear()

    assert ring.written == 0
    assert ring.available == 0
    assert ring.overrun == 0
    assert ring.dropped_samples == 0
    assert ring.read_latest(1) is None


def test_concurrent_write_and_read_stays_consistent() -> None:
    """写线程持续写入时，读方拿到的数据块内部必须连续（近似无锁方案的正确性）。"""
    capacity = 4096
    block = 512
    blocks = 200
    ring = RingBuffer(capacity=capacity, channels=1)
    stop = threading.Event()
    reads = 0
    torn_reads = 0

    def writer() -> None:
        for index in range(blocks):
            ring.write(_ramp(block, start=index * block))
        stop.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    while not stop.is_set():
        latest = ring.read_latest(1024)
        if latest is None:
            continue
        reads += 1
        # 整条流是 0,1,2,... 连续序列，正常读出的块内差分为 1
        if not np.allclose(np.diff(latest[:, 0]), 1.0):
            torn_reads += 1
    thread.join(timeout=5.0)

    assert reads > 0
    assert ring.written == blocks * block
    assert torn_reads <= max(1, reads // 50), f"撕裂读比例过高：{torn_reads}/{reads}"
