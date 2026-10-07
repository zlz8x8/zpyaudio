"""pytest 公共夹具。

GUI 用例一律以离屏平台运行（规格书 2.2：``QT_QPA_PLATFORM=offscreen``），
因此必须在导入 Qt 之前设置环境变量。

受限开发环境下系统临时目录不可用，``pytest.ini`` 关闭了 tmpdir 插件，
本文件提供 :func:`work_dir` 作为工作区内的临时目录夹具。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

#: 测试统一采样率
SAMPLE_RATE = 48_000

#: 临时目录根（位于工作区内，便于受控环境写入）
BUILD_TMP_ROOT = Path(__file__).resolve().parents[1] / "build" / "test-tmp"


@pytest.fixture
def work_dir(request) -> Path:
    """为当前用例准备一个干净的工作区内临时目录。"""
    name = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)
    target = BUILD_TMP_ROOT / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture
def make_sine():
    """生成浮点正弦测试信号（默认 1 kHz、幅度 0.5）。"""

    def _make(
        frequency: float,
        seconds: float = 0.5,
        sample_rate: int = SAMPLE_RATE,
        amplitude: float = 0.5,
    ) -> np.ndarray:
        count = int(round(seconds * sample_rate))
        t = np.arange(count, dtype=np.float64) / float(sample_rate)
        return (amplitude * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)

    return _make


@pytest.fixture
def default_config():
    """默认应用配置（已校验）。"""
    from zpyaudio.app.config import AppConfig

    config = AppConfig()
    config.validate()
    return config


#: ``write_midi`` 默认速度：500000 µs/拍 = 120 BPM
MIDI_TEMPO_US = 500_000

#: ``write_midi`` 默认每拍 tick 数
MIDI_TICKS_PER_BEAT = 480


@pytest.fixture
def write_midi():
    """把音符清单写成**真实 MIDI 文件**（M5 用例共用）。

    入参 ``notes`` 是 6 元组 ``(start_s, note, duration_s, velocity, channel, track)``，
    时间以**秒**给（内部按 120 BPM / 480 tpb 换算成 tick），用例因此可以直接按秒断言。
    轨道数由 ``track`` 的最大值决定。返回写入的 :class:`pathlib.Path`。
    """
    import mido

    def _seconds_to_ticks(seconds: float, *, ticks_per_beat: int, tempo_us: int) -> int:
        return int(round(float(seconds) * ticks_per_beat * 1_000_000 / tempo_us))

    def _write(
        path,
        notes,
        *,
        ticks_per_beat: int = MIDI_TICKS_PER_BEAT,
        tempo_us: int = MIDI_TEMPO_US,
        midi_type: int = 1,
    ):
        target = Path(path)
        track_count = max((int(item[5]) for item in notes), default=0) + 1
        midi = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=midi_type)
        buckets: list[list[tuple[int, int, object]]] = []
        for _ in range(track_count):
            midi.tracks.append(mido.MidiTrack())
            buckets.append([])

        for start, note, duration, velocity, channel, track_index in notes:
            begin = _seconds_to_ticks(start, ticks_per_beat=ticks_per_beat, tempo_us=tempo_us)
            finish = _seconds_to_ticks(
                float(start) + float(duration),
                ticks_per_beat=ticks_per_beat,
                tempo_us=tempo_us,
            )
            # 第二个元素用于同一 tick 上「先 note_on 后 note_off」的稳定排序
            buckets[int(track_index)].append(
                (
                    begin,
                    1,
                    mido.Message(
                        "note_on", note=int(note), velocity=int(velocity), channel=int(channel)
                    ),
                )
            )
            buckets[int(track_index)].append(
                (
                    finish,
                    0,
                    mido.Message(
                        "note_off", note=int(note), velocity=0, channel=int(channel)
                    ),
                )
            )

        for index, track in enumerate(midi.tracks):
            last_tick = 0
            for tick, _order, message in sorted(buckets[index], key=lambda item: (item[0], item[1])):
                message.time = tick - last_tick
                last_tick = tick
                track.append(message)
            if index == 0:
                track.insert(0, mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
            track.append(mido.MetaMessage("end_of_track", time=0))

        midi.save(str(target))
        return target

    return _write
