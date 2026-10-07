"""命令行入口（M0/M1 验收用）。

``main.py`` 只负责把 ``src/`` 加入 ``sys.path`` 后调用 :func:`main`。
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from zpyaudio.app.config import LOG_LEVELS, AppConfig, load_config
from zpyaudio.app.controller import HOP_SECONDS
from zpyaudio.app.logging_setup import setup_logging
from zpyaudio.core.notes import format_note, freq_to_note

__all__ = ["build_parser", "main"]

logger = logging.getLogger(__name__)


def _note_suffix(freq: float) -> str:
    """频率 → ``" ＝ B5 +20.8¢"`` 后缀（无有效频率时为空串，v0.5 变更 1）。"""
    info = freq_to_note(freq)
    if info is None:
        return ""
    return " ＝ " + format_note(info, low_band_hint="full" if info.low_band else None)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog="zpyaudio",
        description="音频分析软件（规格书 v0.2，里程碑 M0/M1）",
    )
    parser.add_argument("--config", type=Path, default=None, help="配置文件路径（默认 ./config.json）")
    parser.add_argument("--log-level", choices=LOG_LEVELS, default=None, help="日志级别")
    parser.add_argument("--list-devices", action="store_true", help="列出可用输入设备后退出")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="以离屏方式启动 GUI 并自动退出（M0 验收：程序能起窗口）",
    )
    parser.add_argument("--self-test-ms", type=int, default=800, help="自检窗口存活毫秒数")
    parser.add_argument(
        "--self-test-shot",
        type=Path,
        default=None,
        metavar="PNG",
        help="自检结束时把主窗口截图保存到该文件（布局自检）",
    )
    parser.add_argument(
        "--audio-check",
        type=float,
        metavar="SECONDS",
        default=None,
        help="无界面采集分析自检（M1 验收：A1 频谱峰值 / A6 静音）",
    )
    parser.add_argument(
        "--source",
        choices=("synthetic", "device"),
        default="synthetic",
        help="--audio-check 使用的信号源（默认 synthetic）",
    )
    parser.add_argument("--tone-frequency", type=float, default=1000.0, help="合成信号频率（Hz）")
    parser.add_argument(
        "--demo-tone",
        type=float,
        metavar="HZ",
        default=None,
        help="用合成正弦代替麦克风启动 GUI（可视验收 A1）",
    )
    parser.add_argument(
        "--file-check",
        type=Path,
        metavar="PATH",
        default=None,
        help="播放指定音频文件做 A5 自检（真实声卡输出 + 进度核对）",
    )
    parser.add_argument(
        "--file-check-seconds",
        type=float,
        default=3.0,
        help="--file-check 的播放时长（秒）",
    )
    parser.add_argument(
        "--midi-check",
        type=Path,
        metavar="PATH",
        default=None,
        help="无界面解析 MIDI 做 A7 自检（钢琴卷帘数据 + C4 频率 + 与 mido 的消息数一致性）",
    )
    parser.add_argument(
        "--demo-midi",
        action="store_true",
        help="生成一段演示 MIDI（C 大调音阶 + 三个和弦）并打开，用于可视验收 A7",
    )
    parser.add_argument(
        "--self-test-midi",
        type=Path,
        metavar="PATH",
        default=None,
        help="--self-test 时先打开指定 MIDI，便于把钢琴卷帘一起截进布局图",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """程序入口，返回进程退出码。"""
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.log_level:
        config.log_level = args.log_level
    log_file = setup_logging(config.logs_path, config.log_level)
    logger.info("zpyaudio 启动，日志文件：%s", log_file)

    if args.list_devices:
        return _run_list_devices()
    if args.audio_check is not None:
        return _run_audio_check(config, args.audio_check, args.source, args.tone_frequency)
    if args.file_check is not None:
        return _run_file_check(config, args.file_check, args.file_check_seconds)
    if args.midi_check is not None:
        return _run_midi_check(args.midi_check)

    midi_path = _resolve_midi_argument(args)
    if args.self_test:
        return _run_gui_self_test(
            config, args.self_test_ms, args.demo_tone, args.self_test_shot, midi_path
        )
    return _run_gui(config, args.demo_tone, midi_path)


# -------------------------------------------------------------------- 子命令
def _run_list_devices() -> int:
    from zpyaudio.sources.device_source import list_input_devices

    try:
        devices = list_input_devices()
    except Exception as exc:
        print(f"无法枚举音频设备：{exc}")
        return 1
    if not devices:
        print("未发现可用输入设备")
        return 1
    print(f"{'索引':>4} {'声道':>4} {'默认采样率':>10} {'默认':>4}  名称")
    for device in devices:
        print(
            f"{device.index:>4} {device.channels:>4} {device.default_sample_rate:>10.0f} "
            f"{'是' if device.is_default else '':>4}  {device.name}"
        )
    return 0


def _run_audio_check(
    config: AppConfig, seconds: float, source_kind: str, frequency: float
) -> int:
    """无界面跑一遍 采集 → 环形缓冲 → 分析 链路并打印统计。"""
    import numpy as np

    from zpyaudio.app.controller import AudioController
    from zpyaudio.core.ringbuffer import RingBuffer
    from zpyaudio.sources.device_source import DeviceSource
    from zpyaudio.sources.synthetic_source import SyntheticSource

    capacity = max(int(config.ring_seconds * config.sample_rate), config.fft_size * 2)

    def factory(ring: RingBuffer):
        if source_kind == "device":
            return DeviceSource(
                ring,
                device=config.device,
                sample_rate=config.sample_rate,
                channels=1,
                block_size=config.block_size,
            )
        return SyntheticSource(
            ring,
            sample_rate=config.sample_rate,
            channels=1,
            frequency=frequency,
            block_size=config.block_size,
        )

    controller = AudioController(
        config, ring=RingBuffer(capacity=capacity, channels=1), source_factory=factory
    )
    try:
        controller.start_monitoring()
    except Exception as exc:
        print(f"启动采集失败：{exc}")
        return 1

    collected = []
    deadline = time.perf_counter() + seconds
    try:
        while time.perf_counter() < deadline:
            snapshot = controller.latest_snapshot()
            if snapshot is not None:
                collected.append(snapshot)
            time.sleep(0.02)
    finally:
        controller.shutdown()

    stats = controller.stats()
    # 理论帧数需扣除窗长预热：分析窗必须先被填满才能产出第一帧（规格书 4.1）
    warmup = config.fft_size / float(config.sample_rate)
    expected = max(1.0, (seconds - warmup) / HOP_SECONDS)
    print(f"信号源        : {source_kind}" + (f"（{frequency:.1f} Hz 正弦）" if source_kind == "synthetic" else ""))
    print(f"采集时长      : {seconds:.2f} s（窗长预热 {warmup * 1000:.0f} ms）")
    print(f"分析帧数      : {len(collected)} / 理论 {expected:.0f}")
    print(f"环形缓冲      : overrun {stats['overrun']} 次，丢弃样本 {stats['dropped_samples']}")
    print(f"分析线程迟到  : {stats['late_events']} 次，异常 {stats['analysis_errors']} 次")
    if not collected:
        print("自检结论      : FAIL（未取得任何分析帧）")
        return 1

    freqs = [s.dominant_freq for s in collected if s.has_pitch]
    peaks = [s.peak_freq for s in collected]
    print(f"RMS 电平      : 中位数 {np.median([s.rms_db for s in collected]):.1f} dBFS")
    print(f"频谱峰值(BIN) : 中位数 {np.median(peaks):.2f} Hz")
    if freqs:
        median_freq = float(np.median(freqs))
        print(
            f"主频中位数    : {median_freq:.2f} Hz{_note_suffix(median_freq)}"
            f"（有效帧 {len(freqs)}/{len(collected)}，置信度中位数 "
            f"{np.median([s.confidence for s in collected]):.2f}）"
        )
        if source_kind == "synthetic":
            deviation = abs(median_freq - frequency)
            print(f"与设定频率偏差: {deviation:.2f} Hz")
            ok = deviation <= 2.0 and len(collected) >= 0.8 * expected
            print(f"自检结论      : {'PASS' if ok else 'FAIL'}（判据：|偏差| ≤ 2 Hz 且帧数 ≥ 80% 理论值）")
            return 0 if ok else 1
    else:
        print("主频中位数    : 无有效主频（静音或低于门限）")

    ok = len(collected) >= 0.8 * expected
    print(f"自检结论      : {'PASS' if ok else 'FAIL'}（判据：帧数 ≥ 80% 理论值）")
    return 0 if ok else 1


def _run_file_check(config: AppConfig, path: Path, seconds: float) -> int:
    """A5 自检：真实声卡播放文件，核对进度与实际播放位置的一致性。"""
    import numpy as np

    from zpyaudio.app.controller import AudioController

    controller = AudioController(config)
    try:
        controller.start_playback(path)
    except Exception as exc:
        print(f"打开或播放文件失败：{exc}")
        return 1

    collected = []
    deadline = time.perf_counter() + seconds
    started = time.perf_counter()
    try:
        while time.perf_counter() < deadline and not controller.poll_completion():
            snapshot = controller.latest_snapshot()
            if snapshot is not None:
                collected.append(snapshot)
            time.sleep(0.02)
        elapsed = time.perf_counter() - started
        position = controller.position_seconds
        duration = controller.duration_seconds or 0.0
        stats = controller.stats()
    finally:
        controller.shutdown()

    source = path
    print(f"文件          : {source}")
    print(f"总时长        : {duration:.2f} s")
    print(f"播放时长      : {elapsed:.2f} s")
    print(f"播放位置      : {position:.2f} s（与播放时长偏差 {abs(position - elapsed) * 1000:.0f} ms）")
    print(f"分析帧数      : {len(collected)}")
    print(f"播放欠载      : {stats['playback_underruns']} 次")
    if collected:
        freqs = [item.dominant_freq for item in collected if item.has_pitch]
        if freqs:
            median_freq = float(np.median(freqs))
            print(
                f"主频中位数    : {median_freq:.2f} Hz{_note_suffix(median_freq)}"
                f"（有效帧 {len(freqs)}/{len(collected)}）"
            )

    ok = (
        len(collected) > 0
        and stats["playback_underruns"] == 0
        and abs(position - elapsed) < 0.2  # A5 判据：进度偏差 < 200 ms
    )
    print(
        "自检结论      : "
        + ("PASS" if ok else "FAIL")
        + "（判据：有分析帧 且 无播放欠载 且 进度偏差 < 200 ms）"
    )
    return 0 if ok else 1


def _run_midi_check(path: Path) -> int:
    """A7 自检：解析 MIDI，核对音符事件时间与 C4 频率（M5）。

    判据（对应 FR-4.1 / FR-4.3 与验收 A7）：

    1. 解析出的音符数 == ``mido`` 里 ``note_on``（velocity > 0）的条数；
    2. 每个音符 ``0 ≤ start ≤ end``，且列表按 start 非降序；
    3. ``note 60`` 的频率为 261.63 Hz（误差 < 0.01 Hz）；
    4. 至少有 1 个音符、总时长 > 0。
    """
    from zpyaudio.core.notes import format_note, freq_to_note, note_frequency
    from zpyaudio.media.midi_loader import NoteEvent, load_midi

    try:
        document = load_midi(path)
    except Exception as exc:
        print(f"打开 MIDI 失败：{exc}")
        return 1

    import mido

    midi = mido.MidiFile(str(path))
    note_ons = sum(
        1
        for track in midi.tracks
        for message in track
        if message.type == "note_on" and int(getattr(message, "velocity", 0)) > 0
    )

    notes: list[NoteEvent] = list(document.notes)
    low, high = document.note_range
    c4 = note_frequency(60)
    c4_info = freq_to_note(c4)

    print(f"文件          : {path}")
    print(f"MIDI 类型     : type {document.midi_type}，{document.track_count} 轨，{document.tempo_bpm:.1f} BPM")
    print(f"音符事件      : {document.note_count} 个（mido note_on {note_ons} 个）")
    print(f"时间范围      : {notes[0].start:.3f} – {max(item.end for item in notes):.3f} s"
          f"（总时长 {document.duration:.3f} s）")
    print(f"音域          : {low} – {high}")
    print(
        f"C4（note 60） : {c4:.2f} Hz"
        + (f" ＝ {format_note(c4_info)}" if c4_info is not None else "")
    )
    first = notes[0]
    print(
        f"首个音符      : start={first.start:.3f} end={first.end:.3f} "
        f"note={first.note}（{first.name}）velocity={first.velocity} track={first.track}"
    )

    monotonic = all(
        notes[index].start <= notes[index + 1].start for index in range(len(notes) - 1)
    )
    ok = (
        document.note_count == note_ons
        and document.note_count > 0
        and document.duration > 0.0
        and all(item.start >= 0.0 and item.end >= item.start for item in notes)
        and monotonic
        and abs(c4 - 261.63) < 0.01
    )
    print(
        "自检结论      : "
        + ("PASS" if ok else "FAIL")
        + "（判据：音符数 == mido note_on 数 且 起止合法且有序 且 |C4 − 261.63| < 0.01 Hz）"
    )
    return 0 if ok else 1


#: ``--demo-midi`` 的内容：``(起始秒, 音符号, 时长秒)``。
#: C 大调音阶（C4→C5，八分音符）+ C/F/G 三个大三和弦，覆盖"单音 + 和弦"两种形态。
DEMO_MIDI_NOTES: tuple[tuple[float, int, float], ...] = tuple(
    (index * 0.5, note, 0.45) for index, note in enumerate((60, 62, 64, 65, 67, 69, 71, 72))
) + tuple(
    (start, note, 1.6)
    for start, chord in (
        (4.5, (60, 64, 67)),
        (6.5, (65, 69, 72)),
        (8.5, (67, 71, 74)),
    )
    for note in chord
)


def _write_demo_midi(path: Path) -> Path:
    """生成演示 MIDI（M5 的可视验收素材，与 ``--demo-tone`` 同一思路：不依赖外部文件）。"""
    import mido

    ticks_per_beat = 480
    tempo_us = 500_000  # 120 BPM → 1 拍 = 0.5 s

    def to_ticks(seconds: float) -> int:
        return int(round(seconds * ticks_per_beat * 1_000_000 / tempo_us))

    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat, type=1)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    track.append(mido.MetaMessage("track_name", name="zpyaudio demo", time=0))

    events: list[tuple[int, int, object]] = []
    for start, note, duration in DEMO_MIDI_NOTES:
        events.append((to_ticks(start), 1, mido.Message("note_on", note=note, velocity=90)))
        events.append(
            (to_ticks(start + duration), 0, mido.Message("note_off", note=note, velocity=0))
        )
    last_tick = 0
    for tick, _order, message in sorted(events, key=lambda item: (item[0], item[1])):
        message.time = tick - last_tick
        last_tick = tick
        track.append(message)
    track.append(mido.MetaMessage("end_of_track", time=0))

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(target))
    return target


def _resolve_midi_argument(args) -> Path | None:  # noqa: ANN001
    """把 ``--self-test-midi`` / ``--demo-midi`` 归一成一个 MIDI 路径。"""
    if args.self_test_midi is not None:
        return Path(args.self_test_midi)
    if args.demo_midi:
        from zpyaudio.app.config import PROJECT_ROOT

        target = _write_demo_midi(PROJECT_ROOT / "build" / "demo_midi.mid")
        print(f"演示 MIDI 已生成：{target}")
        return target
    return None


def _demo_source_factory(config: AppConfig, frequency: float):
    """构造把合成正弦写入控制器环形缓冲的源工厂。"""
    from zpyaudio.core.ringbuffer import RingBuffer
    from zpyaudio.sources.synthetic_source import SyntheticSource

    def factory(ring: RingBuffer) -> SyntheticSource:
        return SyntheticSource(
            ring,
            sample_rate=config.sample_rate,
            channels=1,
            frequency=frequency,
            block_size=config.block_size,
        )

    return factory


def _run_gui_self_test(
    config: AppConfig,
    milliseconds: int,
    demo_tone: float | None = None,
    shot_path: Path | None = None,
    midi_path: Path | None = None,
) -> int:
    """离屏创建主窗口并短暂运行（M0 验收：程序能起窗口）。

    给定 ``demo_tone`` 时同时启动合成源，用于截图核对波形/频谱是否真的画出来；
    给定 ``midi_path`` 时先打开 MIDI，把**钢琴卷帘 + 符号数据频率曲线**一起截进布局图
    （M5 的可视验收 A7）。自检不写 config.json，避免覆盖用户配置。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from zpyaudio.gui.main_window import MainWindow

    if shot_path is not None:
        shot_path = Path(shot_path)
        shot_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    factory = _demo_source_factory(config, demo_tone) if demo_tone is not None else None
    window = MainWindow(config, source_factory=factory, persist_config=False)
    window.show()
    if demo_tone is not None:
        window.start_monitoring()
    if midi_path is not None:
        if not window.load_midi(str(midi_path)):
            print(f"打开 MIDI 失败：{midi_path}")
            return 1

    captured = {"ok": False}

    def _finish() -> None:
        # 必须在事件循环内截图：quit() 会关闭窗口并触发停止，之后状态已复位
        if shot_path is not None:
            captured["ok"] = bool(window.grab().save(str(Path(shot_path))))
        app.quit()

    QTimer.singleShot(milliseconds, _finish)
    app.exec()

    if shot_path is not None:
        target = Path(shot_path)
        if captured["ok"]:
            print(f"窗口截图已保存：{target}")
        else:  # pragma: no cover - 仅在异常环境出现
            print(f"窗口截图保存失败：{target}")
    window.close()
    platform = os.environ.get("QT_QPA_PLATFORM", "系统默认平台")
    print(f"GUI 自检通过：主窗口已创建并存活 {milliseconds} ms（平台 {platform}）")
    return 0


def _run_gui(config: AppConfig, demo_tone: float | None, midi_path: Path | None = None) -> int:
    """启动图形界面。"""
    from PySide6.QtWidgets import QApplication

    from zpyaudio.gui.main_window import MainWindow

    factory = _demo_source_factory(config, demo_tone) if demo_tone is not None else None

    app = QApplication.instance() or QApplication([])
    window = MainWindow(config, source_factory=factory)
    window.show()
    if demo_tone is not None:
        logger.info("演示模式：使用 %.1f Hz 合成正弦替代麦克风", demo_tone)
        window.start_monitoring()
    if midi_path is not None:
        logger.info("演示模式：打开 MIDI %s（符号数据，不播放音频）", midi_path)
        window.load_midi(str(midi_path))
    return app.exec()
