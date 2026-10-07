"""控制器与状态机测试（规格书 5.2 / 5.3，验收项 A2）。"""

from __future__ import annotations

import time

import numpy as np
import pytest

from zpyaudio.app.config import AppConfig
from zpyaudio.app.controller import (
    HOP_SECONDS,
    LEGAL_TRANSITIONS,
    AppState,
    AudioController,
)
from zpyaudio.core.ringbuffer import RingBuffer
from zpyaudio.sources.synthetic_source import SyntheticSource

FS = 48_000


def synthetic_factory(frequency: float = 1000.0):
    """构造写入控制器环形缓冲的合成源工厂。"""

    def factory(ring) -> SyntheticSource:
        return SyntheticSource(
            ring,
            sample_rate=FS,
            channels=1,
            frequency=frequency,
            amplitude=0.5,
            block_size=1024,
        )

    return factory


def collect(controller: AudioController, seconds: float) -> list:
    """在给定时间内收集快照。"""
    deadline = time.perf_counter() + seconds
    snapshots = []
    while time.perf_counter() < deadline:
        snapshot = controller.latest_snapshot()
        if snapshot is not None:
            snapshots.append(snapshot)
        time.sleep(0.02)
    return snapshots


@pytest.fixture
def controller(default_config: AppConfig) -> AudioController:
    return AudioController(default_config, source_factory=synthetic_factory())


# ---------------------------------------------------------------- 常量与表
def test_hop_is_one_sixteenth_second() -> None:
    """规格书 4.3：hop = 1/16 s = 62.5 ms。"""
    assert HOP_SECONDS == pytest.approx(1.0 / 16.0)
    assert HOP_SECONDS * 1000.0 == pytest.approx(62.5)


def test_transition_table_matches_spec() -> None:
    assert AppState.IDLE not in LEGAL_TRANSITIONS[AppState.IDLE]
    assert AppState.MONITORING in LEGAL_TRANSITIONS[AppState.IDLE]
    assert AppState.PAUSED in LEGAL_TRANSITIONS[AppState.RECORDING]
    assert AppState.PAUSED in LEGAL_TRANSITIONS[AppState.PLAYING]
    assert AppState.PLAYING not in LEGAL_TRANSITIONS[AppState.MONITORING]
    assert LEGAL_TRANSITIONS[AppState.ERROR] == frozenset({AppState.IDLE})


# ---------------------------------------------------------------- 生命周期
def test_start_and_stop_cycle_produces_snapshots(controller: AudioController) -> None:
    """A2 判据：1 kHz 稳定正弦下主频读数 1000 ± 5 Hz 且抖动 < 5 Hz。"""
    assert controller.state is AppState.IDLE

    source = controller.start_monitoring()
    assert controller.state is AppState.MONITORING
    assert source is controller.source

    snapshots = collect(controller, 0.8)
    controller.stop()

    assert controller.state is AppState.IDLE
    assert len(snapshots) >= 5, "0.8 s 内应产出至少 5 帧（理论 12 帧）"
    freqs = [s.dominant_freq for s in snapshots if s.has_pitch]
    assert freqs, "应有有效主频帧"
    assert float(np.median(freqs)) == pytest.approx(1000.0, abs=5.0)
    assert float(np.std(freqs)) < 5.0
    assert all(abs(s.peak_freq - 1000.0) <= 11.72 for s in snapshots)


def test_stats_reflect_runtime(controller: AudioController) -> None:
    controller.start_monitoring()
    collect(controller, 0.3)
    stats = controller.stats()
    controller.stop()

    assert stats["state"] is AppState.MONITORING
    assert stats["state_label"] == "监听中"
    assert stats["snapshots"] >= 1
    assert stats["overrun"] == 0
    assert stats["dropped_samples"] == 0
    assert stats["analysis_errors"] == 0
    assert stats["stream_seconds"] > 0.0


def test_start_while_running_is_ignored(controller: AudioController) -> None:
    first = controller.start_monitoring()
    second = controller.start_monitoring()
    controller.stop()

    assert first is second


def test_stop_is_idempotent(controller: AudioController) -> None:
    controller.start_monitoring()
    controller.stop()
    controller.stop()

    assert controller.state is AppState.IDLE


def test_ring_is_cleared_on_start(default_config: AppConfig) -> None:
    ring = RingBuffer(capacity=default_config.sample_rate * 5, channels=1)
    ring.write(np.ones((1024, 1), dtype=np.float32))
    ctl = AudioController(default_config, ring=ring, source_factory=synthetic_factory())

    ctl.start_monitoring()
    try:
        data = ring.read_latest(1024)
        assert data is None or not np.allclose(data, 1.0), "启动时应清空环形缓冲"
    finally:
        ctl.stop()


def test_latest_snapshot_is_none_when_idle(controller: AudioController) -> None:
    assert controller.latest_snapshot() is None


def test_snapshot_queue_is_bounded(controller: AudioController) -> None:
    """规格书 5.2 约束 3：跨线程队列必须有界。"""
    assert controller.snapshots.maxsize == 4


# ---------------------------------------------------------------- 状态监听
def test_state_listeners_are_notified(controller: AudioController) -> None:
    events: list[tuple[AppState, AppState]] = []
    controller.add_state_listener(lambda old, new: events.append((old, new)))

    controller.start_monitoring()
    controller.stop()

    assert events == [
        (AppState.IDLE, AppState.MONITORING),
        (AppState.MONITORING, AppState.IDLE),
    ]


def test_remove_state_listener(controller: AudioController) -> None:
    events: list[tuple[AppState, AppState]] = []
    listener = lambda old, new: events.append((old, new))  # noqa: E731
    controller.add_state_listener(listener)
    controller.remove_state_listener(listener)

    controller.start_monitoring()
    controller.stop()

    assert events == []


def test_listener_exception_does_not_break_controller(controller: AudioController) -> None:
    def broken(old: AppState, new: AppState) -> None:
        raise RuntimeError("listener boom")

    controller.add_state_listener(broken)
    controller.start_monitoring()
    controller.stop()

    assert controller.state is AppState.IDLE


def test_illegal_transition_is_ignored(controller: AudioController) -> None:
    """MONITORING → PLAYING 非法，应被忽略（规格书 5.3）。"""
    controller.start_monitoring()
    try:
        controller._set_state(AppState.PLAYING)
        assert controller.state is AppState.MONITORING
    finally:
        controller.stop()


# ---------------------------------------------------------------- 异常路径
def test_source_open_failure_returns_to_idle(default_config: AppConfig) -> None:
    class FailingSource:
        name = "失败源"
        sample_rate = FS
        channels = 1

        def open(self) -> None:
            raise RuntimeError("设备被占用")

        def start(self) -> None:  # pragma: no cover - 不会被执行
            raise AssertionError

        def stop(self) -> None: ...

        def close(self) -> None: ...

    ctl = AudioController(default_config, source_factory=lambda ring: FailingSource())

    with pytest.raises(RuntimeError, match="设备被占用"):
        ctl.start_monitoring()

    assert ctl.state is AppState.IDLE
    assert ctl.source is None


def test_shutdown_releases_resources(controller: AudioController) -> None:
    controller.start_monitoring()
    controller.shutdown()

    assert controller.state is AppState.IDLE
    assert controller.source is None
