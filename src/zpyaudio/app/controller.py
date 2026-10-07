"""状态机 + 线程编排（规格书 5.2 / 5.3）。

线程模型：``源线程/回调 → RingBuffer → 分析线程 → 有界快照队列 → GUI 定时器``。
本模块**不依赖 GUI**，GUI 通过 :meth:`AudioController.latest_snapshot` 主动取最新
快照，因此分析节拍与界面重绘互不阻塞。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from zpyaudio.app.config import AppConfig
from zpyaudio.core.analyzer import Analyzer
from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.pitch_series import PitchSeries, write_pitch_csv
from zpyaudio.core.resample import to_mono
from zpyaudio.core.ringbuffer import RingBuffer
from zpyaudio.media.midi_loader import MidiDocument
from zpyaudio.media.midi_loader import load_midi as load_midi_file
from zpyaudio.media.player import Player, default_output_sample_rate, output_settings_ok
from zpyaudio.media.recorder import Recorder, RecorderStats, build_record_path
from zpyaudio.sources.base import AudioSource
from zpyaudio.sources.device_source import DeviceSource
from zpyaudio.sources.file_source import FileSource

__all__ = ["AppState", "AudioController", "HOP_SECONDS", "LEGAL_TRANSITIONS", "PitchExport"]

logger = logging.getLogger(__name__)

#: 分析跳步 = 1/16 秒（规格书 4.3）
HOP_SECONDS = 1.0 / 16.0

#: 快照队列长度：有界且只保留最新（规格书 5.2 约束 3）
SNAPSHOT_QUEUE_SIZE = 4

#: 状态监听器类型
StateListener = Callable[["AppState", "AppState"], None]

#: 源工厂类型：入参为该控制器使用的环形缓冲
SourceFactory = Callable[[RingBuffer], AudioSource]

#: 播放器工厂类型（测试/嵌入式场景可注入不依赖声卡的实现）
PlayerFactory = Callable[..., "Player"]


@dataclass(frozen=True, slots=True)
class PitchExport:
    """录制结束时主频时序的导出结果（v0.5 变更 3）。"""

    path: Path | None
    """CSV 路径；未生成时为 ``None``。"""

    rows: int
    """写入的数据行数（不含列头）。"""

    error: str | None = None
    """写盘失败的原因（中文提示用）；成功时为 ``None``。"""

    dropped_rows: int = 0
    """因超过序列上限而未保留的点数（>0 表示导出被截断）。"""

    def describe(self) -> str:
        """一行中文摘要（日志用）。"""
        if self.error is not None:
            return f"失败（{self.error}）"
        if self.path is None:
            return "未生成（本次录制没有有效主频帧）"
        text = f"{self.path}（{self.rows} 行）"
        if self.dropped_rows:
            text += f"，截断 {self.dropped_rows} 行"
        return text


class AppState(StrEnum):
    """应用状态（规格书 5.3）。"""

    IDLE = "IDLE"
    MONITORING = "MONITORING"
    RECORDING = "RECORDING"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"

    def label(self) -> str:
        """中文标签（界面与日志共用）。"""
        return _STATE_LABELS[self]


_STATE_LABELS = {
    AppState.IDLE: "空闲",
    AppState.MONITORING: "监听中",
    AppState.RECORDING: "录制中",
    AppState.PLAYING: "播放中",
    AppState.PAUSED: "已暂停",
    AppState.ERROR: "错误",
}

#: 合法状态迁移（规格书 5.3 状态迁移表）
LEGAL_TRANSITIONS: dict[AppState, frozenset[AppState]] = {
    AppState.IDLE: frozenset({AppState.MONITORING, AppState.RECORDING, AppState.PLAYING, AppState.ERROR}),
    AppState.MONITORING: frozenset({AppState.IDLE, AppState.ERROR}),
    AppState.RECORDING: frozenset({AppState.PAUSED, AppState.IDLE, AppState.ERROR}),
    AppState.PLAYING: frozenset({AppState.PAUSED, AppState.IDLE, AppState.ERROR}),
    AppState.PAUSED: frozenset(
        {AppState.RECORDING, AppState.PLAYING, AppState.IDLE, AppState.ERROR}
    ),
    AppState.ERROR: frozenset({AppState.IDLE}),
}


class AudioController:
    """持有环缓冲、分析器、源与分析线程的唯一状态所有者。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        ring: RingBuffer | None = None,
        source_factory: SourceFactory | None = None,
        player_factory: PlayerFactory | None = None,
    ) -> None:
        self.config = config
        self.analyzer = Analyzer(config.analyzer_config())
        capacity = max(
            int(config.ring_seconds * config.sample_rate),
            self.analyzer.frame_samples * 2,
        )
        self.ring = ring if ring is not None else RingBuffer(capacity=capacity, channels=1)
        self.snapshots: queue.Queue[AnalysisSnapshot] = queue.Queue(maxsize=SNAPSHOT_QUEUE_SIZE)
        #: 主频时序收集器（分析线程单写：最近点供界面显示，录制时保留全量供导出）
        self.pitch_series = PitchSeries(smoothing=config.pitch_smoothing)
        self.state: AppState = AppState.IDLE

        self._source_factory = source_factory
        self._player_factory = player_factory
        self._source: AudioSource | None = None
        self._recorder: Recorder | None = None
        self._player: Player | None = None
        self._paused_from: AppState | None = None
        self._last_recording_stats: RecorderStats | None = None
        self._last_pitch_export: PitchExport | None = None
        #: 当前打开的 MIDI 符号数据（M5）：与状态机无关，仅表示"现在显示什么"
        self._midi: MidiDocument | None = None
        self._analysis_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listeners: list[StateListener] = []
        self._start_lock = threading.Lock()
        self._late_events = 0
        self._analysis_errors = 0
        self._snapshots_produced = 0
        self._last_analyzed_written = -1

    # ---------------------------------------------------------------- 状态
    def add_state_listener(self, listener: StateListener) -> None:
        """注册状态变更监听器（在触发线程内同步调用）。"""
        self._listeners.append(listener)

    def remove_state_listener(self, listener: StateListener) -> None:
        """注销监听器。"""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _set_state(self, new_state: AppState) -> None:
        old_state = self.state
        if new_state is old_state:
            return
        if new_state not in LEGAL_TRANSITIONS[old_state]:
            logger.debug("非法状态迁移 %s → %s（已忽略）", old_state.name, new_state.name)
            return
        self.state = new_state
        logger.info("状态：%s → %s", old_state.label(), new_state.label())
        for listener in list(self._listeners):
            try:
                listener(old_state, new_state)
            except Exception:  # pragma: no cover - 监听器异常不得影响控制器
                logger.exception("状态监听器执行失败")

    # ---------------------------------------------------------------- 生命周期
    @property
    def source(self) -> AudioSource | None:
        """当前输入源。"""
        return self._source

    @property
    def recorder(self) -> Recorder | None:
        """当前录制器（未录制时为 ``None``）。"""
        return self._recorder

    @property
    def player(self) -> Player | None:
        """当前播放器（未播放时为 ``None``）。"""
        return self._player

    @property
    def file_source(self) -> FileSource | None:
        """当前文件源（未播放文件时为 ``None``）。"""
        return self._source if isinstance(self._source, FileSource) else None

    @property
    def last_recording_stats(self) -> RecorderStats | None:
        """最近一次录制的统计（日志区与自检使用，规格书 7.3）。"""
        return self._last_recording_stats

    @property
    def last_pitch_export(self) -> PitchExport | None:
        """最近一次录制结束时的主频时序导出结果（v0.5 变更 3）。"""
        return self._last_pitch_export

    def consume_pitch_export(self) -> PitchExport | None:
        """取出并清除导出结果，供界面"只提示一次"（避免重复弹窗）。"""
        export, self._last_pitch_export = self._last_pitch_export, None
        return export

    @property
    def running(self) -> bool:
        """是否正在采集分析。"""
        return self.state is not AppState.IDLE

    # ---------------------------------------------------------------- MIDI
    @property
    def midi_document(self) -> MidiDocument | None:
        """当前打开的 MIDI 符号数据；没打开时为 ``None``（M5 / FR-4.1）。

        MIDI **不是**一次采集：它没有音频、不进状态机，只是"当前显示的一份符号数据"。
        因此界面用 :meth:`load_midi` 打开、用 :meth:`clear_midi` 关闭，
        而任何一次真正的采集/播放（:meth:`start_monitoring` / :meth:`start_recording` /
        :meth:`start_playback`）都会自动把它清掉，保证"钢琴卷帘只在 MIDI 时出现"。
        """
        return self._midi

    def load_midi(self, path: str | Path) -> MidiDocument:
        """解析并记住一份 MIDI（FR-4.1）；不改变播放/采集状态。

        Raises:
            RuntimeError: 当前不是 ``IDLE``（先停止当前任务再打开）。
            MidiLoadError: 文件不存在 / 不是 MIDI / 无法解析 / 没有音符事件。
        """
        with self._start_lock:
            if self.state is not AppState.IDLE:
                raise RuntimeError(f"当前状态为 {self.state.label()}，无法打开 MIDI")
            document = load_midi_file(path)
            self._midi = document
            # MIDI 是新的数据来源：清掉上一次的声学主频时序，避免两种数据混在一张图里
            self.pitch_series.reset()
            self._last_pitch_export = None
            logger.info("已打开 MIDI：%s", document.describe())
            return document

    def clear_midi(self) -> None:
        """清除当前 MIDI 符号数据（切回音频/开始采集时调用）。"""
        if self._midi is not None:
            logger.debug("清除 MIDI 符号数据：%s", self._midi.path.name)
        self._midi = None

    # ---------------------------------------------------------------- 进度
    @property
    def position_seconds(self) -> float:
        """当前进度（秒）：播放取播放器位置（用户实际听到的位置），录制取已写入时长。"""
        if self._player is not None and self.state in (AppState.PLAYING, AppState.PAUSED):
            return self._player.position_seconds
        if self._recorder is not None and self.state in (AppState.RECORDING, AppState.PAUSED):
            return self._recorder.duration
        source = self.file_source
        if source is not None:
            return source.position_seconds
        return 0.0

    @property
    def duration_seconds(self) -> float | None:
        """总时长（秒）；录制等无界流返回 ``None``。"""
        source = self.file_source
        if source is not None and source.duration_seconds > 0.0:
            return source.duration_seconds
        return None

    @property
    def progress(self) -> float | None:
        """播放进度 0..1；无总时长时返回 ``None``。"""
        total = self.duration_seconds
        if not total:
            return None
        return max(0.0, min(1.0, self.position_seconds / total))

    # ---------------------------------------------------------------- 启动
    def start_monitoring(self, source: AudioSource | None = None) -> AudioSource:
        """开始监听（M1）：打开源、启动分析线程，不写文件（规格书 S5/M1）。"""
        with self._start_lock:
            if self.state is not AppState.IDLE:
                logger.warning("当前状态为 %s，忽略开始请求", self.state.label())
                assert self._source is not None
                return self._source
            resolved = source if source is not None else self._build_source()
            self._launch(resolved, AppState.MONITORING, action="开始监听")
            logger.info(
                "开始监听：%s（采样率 %d Hz，FFT %d，窗 %s，主频算法 %s，hop %.1f ms）",
                resolved.name,
                resolved.sample_rate,
                self.config.fft_size,
                self.config.window,
                self.config.pitch_method,
                HOP_SECONDS * 1000.0,
            )
            return resolved

    def start_recording(
        self,
        *,
        path: str | None = None,
        source: AudioSource | None = None,
    ) -> AudioSource:
        """开始录制：采集 + 实时分析 + 写 wav（规格书 FR-1 / S5）。"""
        with self._start_lock:
            if self.state is not AppState.IDLE:
                logger.warning("当前状态为 %s，忽略录制请求", self.state.label())
                if self._source is not None:
                    return self._source
                raise RuntimeError(f"当前状态为 {self.state.label()}，无法开始录制")

            resolved = source if source is not None else self._build_source()
            resolved.open()
            self._source = resolved
            device_name = str(getattr(resolved, "device_name", None) or resolved.name)
            record_path = (
                Path(path) if path else build_record_path(self.config.records_path, device_name)
            )
            recorder = Recorder(
                record_path,
                sample_rate=resolved.sample_rate,
                channels=resolved.channels,
                subtype=self.config.record_subtype,
            )
            try:
                recorder.open()
                recorder.start()
                self._recorder = recorder
                self._launch(
                    resolved,
                    AppState.RECORDING,
                    recorder=recorder,
                    action="开始录制",
                    open_source=False,
                )
            except Exception:
                logger.exception("开始录制失败")
                self._teardown_recorder()
                self._teardown_source()
                self._set_state(AppState.ERROR)
                self._set_state(AppState.IDLE)
                raise
            logger.info(
                "录制开始时间：%s ｜ 设备：%s ｜ 采样率：%d Hz ｜ 声道：%d ｜ 保存路径：%s",
                recorder.started_at_text,
                device_name,
                resolved.sample_rate,
                resolved.channels,
                record_path,
            )
            return resolved

    def start_playback(self, path: str | Path, *, device: int | None = None) -> FileSource:
        """打开文件并播放：解码 → 环形缓冲（分析） + 输出设备（声音）（规格书 FR-3）。"""
        with self._start_lock:
            if self.state is not AppState.IDLE:
                logger.warning("当前状态为 %s，忽略播放请求", self.state.label())
                raise RuntimeError(f"当前状态为 {self.state.label()}，无法开始播放")

            target = Path(path)
            source = FileSource(
                self.ring,
                target,
                block_size=self.config.block_size,
                ffmpeg_dir=self.config.ffmpeg_dir,
            )
            # 开始播放音频 = 不再是"MIDI 符号数据"模式（钢琴卷帘随之隐藏）
            self.clear_midi()
            player: Player | None = None
            try:
                source.open()
                self._source = source
                channels = max(1, min(source.channels, 2))
                if self._player_factory is None and not output_settings_ok(
                    source.sample_rate, channels, device
                ):
                    fallback = default_output_sample_rate(device)
                    logger.warning(
                        "输出设备不支持 %d Hz/%d 声道，回退到 %d Hz",
                        source.sample_rate,
                        channels,
                        fallback,
                    )
                    source.close()
                    source = FileSource(
                        self.ring,
                        target,
                        target_sample_rate=fallback,
                        block_size=self.config.block_size,
                        ffmpeg_dir=self.config.ffmpeg_dir,
                    )
                    source.open()
                    self._source = source
                    channels = max(1, min(source.channels, 2))

                player = (self._player_factory or Player)(
                    sample_rate=source.sample_rate,
                    channels=channels,
                    device=device,
                    block_size=self.config.block_size,
                )
                player.open()
                self._player = player
                self._launch(
                    source,
                    AppState.PLAYING,
                    player=player,
                    action="开始播放",
                    open_source=False,
                )
            except Exception:
                logger.exception("打开或播放文件失败")
                self._teardown_player()
                self._teardown_source()
                self._set_state(AppState.ERROR)
                self._set_state(AppState.IDLE)
                raise

            info = source.info
            logger.info(
                "播放文件：%s ｜ 总时长：%.2f s ｜ 采样率：%d Hz ｜ 声道：%d ｜ 格式：%s",
                source.path,
                source.duration_seconds,
                source.sample_rate,
                source.channels,
                (info.format if info else "") or source.path.suffix.lstrip("."),
            )
            return source

    def _launch(
        self,
        source: AudioSource,
        state: AppState,
        *,
        recorder: Recorder | None = None,
        player: Player | None = None,
        action: str = "启动",
        open_source: bool = True,
    ) -> None:
        """公共启动流程：打开源 → 挂回调 → 清缓冲 → 起源 → 起分析线程 → 切状态。

        ``open_source=False`` 用于录制/播放：它们在创建录制器/播放器之前
        已经 ``open()`` 过（需要拿到打开后的真实采样率与声道数）。
        """
        callback = recorder.feed if recorder is not None else (player.feed if player is not None else None)
        if callback is not None and hasattr(source, "set_block_callback"):
            source.set_block_callback(callback)
        self._source = source
        try:
            if open_source:
                source.open()
            self.ring.clear()
            # 新一次采集：复位主频时序（仅录制保留全量点用于导出，v0.5 变更 2/3）
            self.pitch_series.reset(record_full=state is AppState.RECORDING)
            # 采集中不该再挂着 MIDI 的符号数据显示（M5）
            self.clear_midi()
            self._last_pitch_export = None
            self._stop_event.clear()
            self._last_analyzed_written = -1
            source.start()
            if player is not None:
                # 先等文件源预填播放队列，再启动输出流，避免起播即欠载
                self._await_player_prefill(player)
                player.start()
            self._analysis_thread = threading.Thread(
                target=self._analysis_loop, name="Analysis", daemon=True
            )
            self._analysis_thread.start()
            self._set_state(state)
        except Exception:
            logger.exception("%s失败", action)
            self._teardown_source()
            self._set_state(AppState.ERROR)
            self._set_state(AppState.IDLE)
            raise

    # ---------------------------------------------------------------- 暂停
    def pause(self) -> None:
        """暂停录制/播放：位置冻结、缓冲保留、不写盘不出声（规格书 5.3）。"""
        with self._start_lock:
            if self.state not in (AppState.RECORDING, AppState.PLAYING):
                logger.debug("当前状态为 %s，忽略暂停", self.state.label())
                return
            self._paused_from = self.state
            if self._source is not None and hasattr(self._source, "pause"):
                self._source.pause()
            if self._player is not None:
                self._player.pause(True)
            self._set_state(AppState.PAUSED)

    def resume(self) -> None:
        """继续录制/播放。"""
        with self._start_lock:
            if self.state is not AppState.PAUSED:
                logger.debug("当前状态为 %s，忽略继续", self.state.label())
                return
            if self._player is not None:
                self._player.pause(False)
            if self._source is not None and hasattr(self._source, "resume"):
                self._source.resume()
            self._set_state(self._paused_from or AppState.PLAYING)

    def seek(self, seconds: float) -> None:
        """播放进度拖动（规格书 FR-3.2）。"""
        source = self.file_source
        if source is None or self.state not in (AppState.PLAYING, AppState.PAUSED):
            logger.debug("当前不可拖动进度（状态 %s）", self.state.label())
            return
        source.seek(seconds)

    # ---------------------------------------------------------------- 停止
    def poll_completion(self) -> bool:
        """播放是否已自然结束（供 GUI 定时器轮询，返回 ``True`` 表示应停止）。"""
        if self.state is not AppState.PLAYING:
            return False
        source = self.file_source
        if source is None or not source.eof:
            return False
        if self._player is not None and self._player.queued_blocks > 0:
            return False
        return True

    def stop(self) -> None:
        """停止采集/录制/播放并释放资源（规格书 5.3：停止 → IDLE）。"""
        with self._start_lock:
            if self.state is AppState.IDLE:
                return
            self._stop_event.set()
            thread = self._analysis_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
                if thread.is_alive():  # pragma: no cover - 分析线程不应长时间阻塞
                    logger.warning("分析线程未在 1 s 内退出")
            self._analysis_thread = None
            self._teardown_source()
            self._teardown_player()
            stats = self._teardown_recorder()
            export: PitchExport | None = None
            if stats is not None:
                self._last_recording_stats = stats
                # 录制结束后导出主频时序（v0.5 变更 3）；失败不影响 wav 收尾
                export = self._export_pitch_series(stats)
            self._last_pitch_export = export
            self._paused_from = None
            logger.info(
                "已停止：累计分析 %d 帧，环形缓冲 overrun %d 次，丢弃样本 %d",
                self._snapshots_produced,
                self.ring.overrun,
                self.ring.dropped_samples,
            )
            if stats is not None:
                logger.info(
                    "录制结束时间：%s ｜ 总时长：%.2f s ｜ 保存路径：%s ｜ 文件大小：%.1f KiB"
                    " ｜ 主频时序：%s",
                    stats.ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    stats.duration,
                    stats.path,
                    stats.size_bytes / 1024.0,
                    export.describe() if export is not None else "未生成",
                )
            self._set_state(AppState.IDLE)

    def _export_pitch_series(self, stats: RecorderStats) -> PitchExport:
        """把本次录制的主频时序写到与 wav 同名的 CSV（v0.5 变更 3）。

        * 只导出有效主频帧；静音录制不生成文件（日志说明原因）；
        * 与 wav 同 stem，天然与音频成对且不会重名；
        * 任何写盘异常都被捕获并转成中文提示，绝不阻断停止流程。
        """
        dropped = self.pitch_series.dropped_records
        points = self.pitch_series.records
        if not points:
            logger.warning("本次录制没有有效主频帧，未生成主频时序文件")
            return PitchExport(path=None, rows=0, dropped_rows=dropped)
        target = Path(stats.path).with_suffix(".csv")
        try:
            # 口径以序列自身为准（与界面显示、音名换算保持同一开关）
            rows = write_pitch_csv(target, points, smoothing=self.pitch_series.smoothing)
        except Exception as exc:
            logger.exception("保存主频时序失败：%s", target)
            return PitchExport(path=None, rows=0, error=str(exc), dropped_rows=dropped)
        export = PitchExport(path=target, rows=rows, dropped_rows=dropped)
        logger.info("主频时序已保存：%s", export.describe())
        return export

    def shutdown(self) -> None:
        """退出时释放全部资源。"""
        self.stop()
        self._listeners.clear()

    def apply_config(self, config: AppConfig) -> None:
        """在空闲状态下应用新配置（FR-6.3 恢复默认设置）。

        分析器与环形缓冲容量都依赖配置，因此必须重建；仅在 ``IDLE`` 下允许。
        """
        if self.state is not AppState.IDLE:
            raise RuntimeError(f"当前状态为 {self.state.label()}，无法应用新配置")
        self.config = config
        self.analyzer = Analyzer(config.analyzer_config())
        capacity = max(
            int(config.ring_seconds * config.sample_rate),
            self.analyzer.frame_samples * 2,
        )
        self.ring = RingBuffer(capacity=capacity, channels=self.ring.channels)
        self.pitch_series.set_smoothing(config.pitch_smoothing)
        self._last_analyzed_written = -1
        logger.info(
            "配置已应用：采样率 %d Hz，FFT %d，窗 %s，主频算法 %s，环形缓冲 %.1f s",
            config.sample_rate,
            config.fft_size,
            config.window,
            config.pitch_method,
            capacity / float(config.sample_rate),
        )

    @staticmethod
    def _await_player_prefill(player: Player, *, min_blocks: int = 0, timeout: float = 1.0) -> None:
        """等待播放队列被预填到接近满，再启动输出流。

        起播时队列太浅，任何系统抖动都会让回调取不到数据（欠载 → 静音毛刺），
        因此这里要求预填到 ``容量 - 2`` 块（默认 8 块容量 → 6 块 ≈ 130 ms）。
        不缓冲的播放器（``queue_capacity <= 1``，如测试替身）直接跳过。
        """
        capacity = int(getattr(player, "queue_capacity", 0) or 0)
        if capacity <= 1:
            return
        target = min_blocks or max(2, capacity - 2)
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if player.queued_blocks >= target:
                return
            time.sleep(0.005)
        logger.debug("播放预填超时（队列 %d/%d 块）", player.queued_blocks, capacity)

    def _build_source(self) -> AudioSource:
        if self._source_factory is not None:
            return self._source_factory(self.ring)
        return DeviceSource(
            self.ring,
            device=self.config.device,
            sample_rate=self.config.sample_rate,
            channels=1,
            block_size=self.config.block_size,
        )

    def _teardown_recorder(self) -> RecorderStats | None:
        recorder = self._recorder
        self._recorder = None
        if recorder is None:
            return None
        try:
            return recorder.stop()
        except Exception:
            logger.exception("停止录制器失败")
            return None

    def _teardown_player(self) -> None:
        player = self._player
        self._player = None
        if player is None:
            return
        try:
            player.stop()
        except Exception:
            logger.exception("停止播放器失败")
        try:
            player.close()
        except Exception:
            logger.exception("关闭播放器失败")

    def _teardown_source(self) -> None:
        source = self._source
        self._source = None
        if source is None:
            return
        try:
            source.stop()
        except Exception:
            logger.exception("停止输入源失败")
        try:
            source.close()
        except Exception:
            logger.exception("关闭输入源失败")

    # ---------------------------------------------------------------- 分析线程
    def _analysis_loop(self) -> None:
        """按 1/16 s 节拍取最新窗长数据做一次分析（规格书 4.3）。"""
        analyzer = self.analyzer
        frame_samples = analyzer.frame_samples
        sample_rate = float(self.config.sample_rate)
        next_time = time.perf_counter()

        while not self._stop_event.is_set():
            next_time += HOP_SECONDS
            delay = next_time - time.perf_counter()
            if delay > 0:
                self._stop_event.wait(delay)
            else:
                self._late_events += 1
                if self._late_events <= 3 or self._late_events % 100 == 0:
                    logger.debug(
                        "分析节拍落后 %.1f ms（第 %d 次）", -delay * 1000.0, self._late_events
                    )
                next_time = time.perf_counter()
            if self._stop_event.is_set():
                break
            try:
                written = self.ring.written
                if written == self._last_analyzed_written:
                    # 无新数据（暂停中或源未产出）：不要重复分析同一段（规格书 5.3 暂停语义）
                    continue
                block = self.ring.read_latest(frame_samples)
                if block is None:
                    continue
                self._last_analyzed_written = written
                snapshot = analyzer.analyze(
                    to_mono(block), t=written / sample_rate
                )
                self._publish(snapshot)
            except Exception:
                self._analysis_errors += 1
                if self._analysis_errors <= 3 or self._analysis_errors % 100 == 0:
                    logger.exception("分析失败（第 %d 次）", self._analysis_errors)

    def _publish(self, snapshot: AnalysisSnapshot) -> None:
        """写入有界队列，满时丢弃最旧（规格书 5.2 约束 3）。"""
        self._snapshots_produced += 1
        # 时序收集在分析线程内完成：不依赖 GUI 定时器，界面丢帧不影响导出完整性
        try:
            self.pitch_series.push(snapshot)
        except Exception:  # pragma: no cover - 收集异常不得中断分析链路
            logger.exception("主频时序收集失败（第 %d 帧）", self._snapshots_produced)
        try:
            self.snapshots.put_nowait(snapshot)
            return
        except queue.Full:
            pass
        try:
            self.snapshots.get_nowait()
        except queue.Empty:  # pragma: no cover - 与上个分支竞争
            pass
        try:
            self.snapshots.put_nowait(snapshot)
        except queue.Full:  # pragma: no cover
            logger.debug("快照队列已满，丢弃本帧")

    # ---------------------------------------------------------------- 读取
    def latest_snapshot(self) -> AnalysisSnapshot | None:
        """取出最新快照（丢弃积压），无新数据时返回 ``None``。"""
        latest: AnalysisSnapshot | None = None
        while True:
            try:
                latest = self.snapshots.get_nowait()
            except queue.Empty:
                return latest

    def stats(self) -> dict[str, object]:
        """运行时统计（状态栏展示，规格书 7.2）。"""
        return {
            "state": self.state,
            "state_label": self.state.label(),
            "snapshots": self._snapshots_produced,
            "overrun": self.ring.overrun,
            "dropped_samples": self.ring.dropped_samples,
            "late_events": self._late_events,
            "analysis_errors": self._analysis_errors,
            "stream_seconds": self.ring.written / float(self.config.sample_rate),
            "position_seconds": self.position_seconds,
            "duration_seconds": self.duration_seconds,
            "recorded_seconds": self._recorder.duration if self._recorder else 0.0,
            "recorded_frames": self._recorder.frames_written if self._recorder else 0,
            "playback_underruns": self._player.underruns if self._player else 0,
        }
