"""控制器媒体功能测试：录制（A4）与文件播放（A5）。

播放用不依赖声卡的 :class:`FakePlayer`，因此在无声卡/CI 环境也能验证
"解码 → 环形缓冲 → 分析 → 播放队列"的完整链路与进度语义。
"""

from __future__ import annotations

import csv
import logging
import time

import numpy as np
import pytest
import soundfile as sf

from zpyaudio.app.config import AppConfig
from zpyaudio.app.controller import AppState, AudioController
from zpyaudio.core.analyzer import Analyzer, AnalyzerConfig
from zpyaudio.core.pitch_series import CSV_HEADER
from zpyaudio.sources.synthetic_source import SyntheticSource

from fakes import FakePlayer

FS = 48_000


def synthetic_factory(frequency: float = 1000.0):
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
    deadline = time.perf_counter() + seconds
    snapshots = []
    while time.perf_counter() < deadline:
        snapshot = controller.latest_snapshot()
        if snapshot is not None:
            snapshots.append(snapshot)
        time.sleep(0.02)
    return snapshots


def write_tone(path, *, seconds: float = 0.4, frequency: float = 1000.0):
    t = np.arange(int(seconds * FS)) / FS
    sf.write(
        str(path),
        (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32),
        FS,
        subtype="PCM_16",
    )
    return path


def dominant_freq_of(path) -> float:
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=4096))
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    assert rate == FS
    return analyzer.analyze(data[:4096, 0]).dominant_freq


@pytest.fixture
def config(work_dir) -> AppConfig:
    cfg = AppConfig()
    cfg.records_dir = str(work_dir / "records")
    cfg.validate()
    return cfg


# ---------------------------------------------------------------- 录制（A4）
def test_recording_creates_wav_and_writes_frames(config: AppConfig) -> None:
    controller = AudioController(config, source_factory=synthetic_factory())
    controller.start_recording()
    assert controller.state is AppState.RECORDING
    assert controller.recorder is not None

    snapshots = collect(controller, 0.6)
    controller.stop()

    assert controller.state is AppState.IDLE
    stats = controller.last_recording_stats
    assert stats is not None
    assert stats.path.exists()
    assert stats.path.parent == config.records_path
    assert stats.frames > 0
    assert snapshots, "录制期间应持续产出分析帧（FR-1.5）"


def test_recording_duration_matches_wall_clock(config: AppConfig) -> None:
    """A4 判据：文件时长与录制区间一致（误差 < 50 ms）。"""
    controller = AudioController(config, source_factory=synthetic_factory())
    controller.start_recording()
    begin = time.perf_counter()
    time.sleep(0.6)
    collected = time.perf_counter() - begin
    controller.stop()

    stats = controller.last_recording_stats
    assert stats is not None
    span = (stats.ended_at - stats.started_at).total_seconds()
    assert stats.duration == pytest.approx(span, abs=0.05)
    assert stats.duration == pytest.approx(collected, abs=0.15)


def test_recording_content_matches_source(config: AppConfig) -> None:
    """录到的内容应与实时分析的视图一致（1 kHz 合成正弦）。"""
    controller = AudioController(config, source_factory=synthetic_factory(1000.0))
    controller.start_recording()
    collect(controller, 0.5)
    controller.stop()

    stats = controller.last_recording_stats
    assert stats is not None
    assert dominant_freq_of(stats.path) == pytest.approx(1000.0, abs=2.0)


def test_recording_filename_contains_device_name(config: AppConfig) -> None:
    controller = AudioController(config, source_factory=synthetic_factory())
    controller.start_recording()
    controller.stop()

    stats = controller.last_recording_stats
    assert stats is not None
    assert "合成信号" in stats.path.name
    assert stats.path.suffix == ".wav"


def test_pause_freezes_recording_then_resumes(config: AppConfig) -> None:
    """FR-1.4：暂停期间不写入样本，恢复后写入同一文件。"""
    controller = AudioController(config, source_factory=synthetic_factory())
    controller.start_recording()
    time.sleep(0.35)

    controller.pause()
    assert controller.state is AppState.PAUSED
    time.sleep(0.05)  # 让在途块落盘
    frozen = controller.recorder.frames_written

    time.sleep(0.25)
    paused_growth = controller.recorder.frames_written - frozen

    controller.resume()
    assert controller.state is AppState.RECORDING
    time.sleep(0.3)
    active_growth = controller.recorder.frames_written - frozen

    controller.stop()

    assert paused_growth <= 4096, f"暂停期间仍在写入：{paused_growth} 帧"
    assert active_growth > paused_growth, "恢复后应继续写入"
    stats = controller.last_recording_stats
    assert stats is not None
    assert stats.duration < 0.8, "暂停时长不应计入文件时长"


def test_recording_logs_start_and_end_fields(config: AppConfig, caplog) -> None:
    """规格书 7.3：录制开始/结束的字段必须齐全。"""
    controller = AudioController(config, source_factory=synthetic_factory())
    with caplog.at_level(logging.INFO, logger="zpyaudio.app.controller"):
        controller.start_recording()
        time.sleep(0.2)
        controller.stop()

    text = caplog.text
    assert "录制开始时间" in text
    assert "录制结束时间" in text
    assert "保存路径" in text
    assert "主频时序" in text, "规格书 v0.5：录制结束日志需给出主频时序文件"


# ---------------------------------------------------------------- 主频时序导出（v0.5 变更 3）
def test_recording_exports_pitch_series_csv(config: AppConfig) -> None:
    """录制结束后导出与 wav 同名的 CSV，且时间基准为录制相对时间。"""
    controller = AudioController(config, source_factory=synthetic_factory(1000.0))
    controller.start_recording()
    collect(controller, 0.6)
    controller.stop()

    stats = controller.last_recording_stats
    export = controller.consume_pitch_export()

    assert stats is not None and export is not None
    target = stats.path.with_suffix(".csv")
    assert export.path == target
    assert export.error is None and export.rows > 0
    assert target.exists()

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == CSV_HEADER.split(",")
    data = rows[1:]
    assert export.rows == len(data) > 3
    assert all(row[3] == "B5" for row in data), "1 kHz 合成正弦应稳定报 B5"
    assert 0.0 <= float(data[0][0]) < 1.0, "时间基准应为录制相对时间"
    assert controller.consume_pitch_export() is None, "导出结果只能被消费一次"


def test_monitoring_does_not_export_pitch_series(config: AppConfig) -> None:
    """只有录制才导出；监听仍收集序列供界面显示。"""
    controller = AudioController(config, source_factory=synthetic_factory(1000.0))
    controller.start_monitoring()
    collect(controller, 0.4)
    controller.stop()

    assert controller.last_recording_stats is None
    assert controller.consume_pitch_export() is None
    assert controller.pitch_series.total > 0, "监听期间也要给界面提供时序数据"
    assert not list(config.records_path.glob("*.csv"))


def test_silent_recording_creates_no_pitch_csv(config: AppConfig) -> None:
    """静音录制没有有效主频帧：wav 正常，CSV 不生成（日志说明原因）。"""

    def silence_factory(ring) -> SyntheticSource:
        return SyntheticSource(
            ring, sample_rate=FS, channels=1, waveform="silence", block_size=1024
        )

    controller = AudioController(config, source_factory=silence_factory)
    controller.start_recording()
    collect(controller, 0.4)
    controller.stop()

    stats = controller.last_recording_stats
    export = controller.consume_pitch_export()

    assert stats is not None and stats.path.exists()
    assert export is not None
    assert export.path is None and export.error is None and export.rows == 0
    assert not list(config.records_path.glob("*.csv"))


def test_pitch_export_failure_keeps_wav_and_reports(
    config: AppConfig, monkeypatch
) -> None:
    """导出失败必须只提示、不影响 wav 收尾（NFR-4 / A8）。"""

    def boom(*_args, **_kwargs):
        raise OSError("磁盘已满")

    monkeypatch.setattr("zpyaudio.app.controller.write_pitch_csv", boom)
    controller = AudioController(config, source_factory=synthetic_factory(1000.0))
    controller.start_recording()
    collect(controller, 0.4)
    controller.stop()

    stats = controller.last_recording_stats
    export = controller.consume_pitch_export()

    assert stats is not None and stats.path.exists(), "导出失败不得影响 wav 收尾"
    assert export is not None and export.path is None
    assert "磁盘已满" in (export.error or "")
    assert controller.state is AppState.IDLE


# ---------------------------------------------------------------- 播放（A5）
def test_playback_uses_file_source_and_player(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=0.4)
    players: list[FakePlayer] = []

    def factory(**kwargs) -> FakePlayer:
        player = FakePlayer(**kwargs)
        players.append(player)
        return player

    controller = AudioController(config, player_factory=factory)
    controller.start_playback(path)

    assert controller.state is AppState.PLAYING
    assert controller.file_source is not None
    assert controller.duration_seconds == pytest.approx(0.4, abs=0.02)

    snapshots = collect(controller, 0.5)
    player = players[0]

    assert player.started and player.opened
    assert player.frames_fed > 0, "应把解码数据交给播放器（FR-3.3）"
    assert snapshots and controller.file_source.position_seconds > 0.0
    # A5 判据：进度显示与实际播放位置偏差 < 200 ms
    assert abs(controller.position_seconds - player.position_seconds) < 0.2
    assert 0.0 < (controller.progress or 0.0) <= 1.0

    controller.stop()
    assert controller.state is AppState.IDLE


def test_playback_analysis_matches_file_tone(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=0.6, frequency=440.0)
    controller = AudioController(config, player_factory=lambda **kwargs: FakePlayer(**kwargs))

    controller.start_playback(path)
    snapshots = collect(controller, 0.7)
    controller.stop()

    freqs = [item.dominant_freq for item in snapshots if item.has_pitch]
    assert freqs
    assert float(np.median(freqs)) == pytest.approx(440.0, abs=5.0)


def test_playback_seek_updates_position(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=1.0)
    controller = AudioController(config, player_factory=lambda **kwargs: FakePlayer(**kwargs))
    controller.start_playback(path)
    try:
        controller.pause()
        controller.seek(0.6)
        assert controller.file_source is not None
        assert controller.file_source.position_seconds == pytest.approx(0.6, abs=0.03)
    finally:
        controller.stop()


def test_playback_pause_marks_player_paused(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=0.6)
    players: list[FakePlayer] = []

    def factory(**kwargs) -> FakePlayer:
        player = FakePlayer(**kwargs)
        players.append(player)
        return player

    controller = AudioController(config, player_factory=factory)
    controller.start_playback(path)
    try:
        controller.pause()
        assert controller.state is AppState.PAUSED
        assert players[0].paused is True

        controller.resume()
        assert controller.state is AppState.PLAYING
        assert players[0].paused is False
    finally:
        controller.stop()


def test_playback_completion_is_detected(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "short.wav", seconds=0.2)
    controller = AudioController(config, player_factory=lambda **kwargs: FakePlayer(**kwargs))
    controller.start_playback(path)

    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline and not controller.poll_completion():
        time.sleep(0.02)

    assert controller.poll_completion() is True, "读到结尾且队列排空后应判定播放结束"
    controller.stop()


def test_playback_logs_file_fields(config: AppConfig, work_dir, caplog) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=0.3)
    controller = AudioController(config, player_factory=lambda **kwargs: FakePlayer(**kwargs))
    with caplog.at_level(logging.INFO, logger="zpyaudio.app.controller"):
        controller.start_playback(path)
        controller.stop()

    text = caplog.text
    assert "播放文件" in text
    assert "总时长" in text


def test_playback_rejected_while_recording(config: AppConfig, work_dir) -> None:
    path = write_tone(work_dir / "tone.wav", seconds=0.2)
    controller = AudioController(config, source_factory=synthetic_factory())
    controller.start_recording()
    try:
        with pytest.raises(RuntimeError, match="无法开始播放"):
            controller.start_playback(path)
    finally:
        controller.stop()


def test_recording_rejected_while_recording(config: AppConfig) -> None:
    """重复点击「录制」不应产生第二个文件。"""
    controller = AudioController(config, source_factory=synthetic_factory())
    first = controller.start_recording()
    second = controller.start_recording()
    controller.stop()

    assert first is second
    assert controller.last_recording_stats is not None
