"""WAV 录制（规格书 FR-1）。

写盘在独立线程完成（FR-1.7）：采集/分析线程只把块塞进有界队列，
队列满时丢弃最旧并计数，绝不阻塞分析节拍（规格书 5.2 约束 3）。
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from zpyaudio.core.frames import AudioFrame
from zpyaudio.core.resample import prepare_block

__all__ = ["Recorder", "RecorderStats", "build_record_path", "sanitize_name"]

logger = logging.getLogger(__name__)

#: 写盘队列深度（块）
DEFAULT_QUEUE_BLOCKS = 64


@dataclass(frozen=True, slots=True)
class RecorderStats:
    """录制结束后的统计（日志区展示，规格书 7.3）。"""

    path: Path
    sample_rate: int
    channels: int
    frames: int
    duration: float
    size_bytes: int
    dropped_frames: int
    started_at: datetime
    ended_at: datetime

    def describe(self) -> str:
        """一行中文摘要。"""
        return (
            f"{self.path}（{self.duration:.2f} s，{self.size_bytes / 1024:.1f} KiB，"
            f"{self.sample_rate} Hz，{self.channels} 声道"
            + (f"，丢弃 {self.dropped_frames} 帧" if self.dropped_frames else "")
            + "）"
        )


def sanitize_name(name: str, *, max_length: int = 40) -> str:
    """把设备名清理成可用的文件名片段（FR-1.2）。"""
    cleaned = "".join("_" if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in name)
    cleaned = "_".join(cleaned.split())
    cleaned = cleaned.strip(". _")
    return cleaned[:max_length] or "device"


def build_record_path(directory: str | Path, device_name: str, *, now: datetime | None = None) -> Path:
    """按 ``YYYYmmdd_HHMMSS_<设备名>.wav`` 生成不重名的录制路径（FR-1.2）。"""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = target_dir / f"{stamp}_{sanitize_name(device_name)}"
    candidate = base.with_suffix(".wav")
    index = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{index}").with_suffix(".wav")
        index += 1
    return candidate


class Recorder:
    """把音频块写成 wav。"""

    def __init__(
        self,
        path: str | Path,
        *,
        sample_rate: int,
        channels: int,
        subtype: str = "PCM_16",
        queue_blocks: int = DEFAULT_QUEUE_BLOCKS,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.subtype = subtype
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(1, int(queue_blocks)))
        self._handle: sf.SoundFile | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frames_written = 0
        self._dropped_frames = 0
        self._started_at: datetime | None = None
        self._ended_at: datetime | None = None

    # ---------------------------------------------------------------- 属性
    @property
    def frames_written(self) -> int:
        return self._frames_written

    @property
    def dropped_frames(self) -> int:
        """因队列满而丢弃的样本数。"""
        return self._dropped_frames

    @property
    def duration(self) -> float:
        """已写入时长（秒）。"""
        return self._frames_written / float(self.sample_rate)

    @property
    def opened(self) -> bool:
        return self._handle is not None

    @property
    def started_at_text(self) -> str:
        """录制开始时间（日志区展示，规格书 7.3）。"""
        return (self._started_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> Path:
        """创建 wav 文件（父目录不存在时自动建立）。"""
        if self._handle is not None:
            return self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = sf.SoundFile(
            str(self.path),
            mode="w",
            samplerate=self.sample_rate,
            channels=self.channels,
            subtype=self.subtype,
        )
        logger.info(
            "开始录制：%s（%d Hz，%d 声道，%s）",
            self.path,
            self.sample_rate,
            self.channels,
            self.subtype,
        )
        return self.path

    def start(self) -> None:
        if self._handle is None:
            raise RuntimeError("请先调用 open() 再 start()")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started_at = datetime.now()
        self._thread = threading.Thread(target=self._run, name="Recorder", daemon=True)
        self._thread.start()

    def feed(self, frame: AudioFrame) -> None:
        """把一块音频放入写盘队列（非阻塞）。"""
        samples = prepare_block(frame.samples, self.channels)
        try:
            self._queue.put_nowait(samples)
            return
        except queue.Full:
            pass
        self._dropped_frames += int(samples.shape[0])
        try:
            self._queue.get_nowait()
        except queue.Empty:  # pragma: no cover
            pass
        try:
            self._queue.put_nowait(samples)
        except queue.Full:  # pragma: no cover
            logger.debug("写盘队列仍满，丢弃本块")

    def stop(self) -> RecorderStats:
        """停止写盘、补齐文件头并返回统计。"""
        # 先盖结束时间戳：此时才真正停止采集，之后只剩排空队列与关文件的开销
        self._ended_at = datetime.now()
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():  # pragma: no cover - 磁盘极慢时
                logger.warning("录制线程未在 5 s 内退出")
        self._thread = None
        if self._handle is not None:
            try:
                self._handle.flush()
            finally:
                self._handle.close()
                self._handle = None

        size = self.path.stat().st_size if self.path.exists() else 0
        stats = RecorderStats(
            path=self.path,
            sample_rate=self.sample_rate,
            channels=self.channels,
            frames=self._frames_written,
            duration=self.duration,
            size_bytes=size,
            dropped_frames=self._dropped_frames,
            started_at=self._started_at or datetime.now(),
            ended_at=self._ended_at,
        )
        logger.info("录制结束：%s", stats.describe())
        return stats

    def close(self) -> None:
        """异常路径下的兜底释放。"""
        if self._handle is not None or self._thread is not None:
            self.stop()

    # ---------------------------------------------------------------- 内部
    def _run(self) -> None:
        handle = self._handle
        assert handle is not None
        while not self._stop.is_set() or not self._queue.empty():
            try:
                block = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                handle.write(block)
            except Exception:
                logger.exception("写入 wav 失败，停止录制")
                self._stop.set()
                return
            self._frames_written += int(block.shape[0])
