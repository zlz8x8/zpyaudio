"""准实时文件源（规格书 4.5）。

按 **1/16 s 节拍**（实际由样本计数推算的截止时刻）推进解码，
同一块数据同时送往环形缓冲（分析）与块回调（播放/录制）。

刻意不用 ``time.sleep(hop)`` 累加推进：那样每轮的系统抖动会累积成漂移，
而"已产出样本数 ÷ 采样率"是单调、可校准的逻辑时钟。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from zpyaudio.media.decoder import AudioFileInfo, Decoder, open_decoder
from zpyaudio.sources.base import BaseSource

__all__ = ["FileSource"]

logger = logging.getLogger(__name__)

#: 起播预填块数：填满播放队列（默认 8 块）留出抗抖动余量，避免起播欠载
DEFAULT_PREFILL_BLOCKS = 6


class FileSource(BaseSource):
    """把音频文件当作实时数据流读出。"""

    name = "文件"

    def __init__(
        self,
        ring,
        path: str | Path,
        *,
        target_sample_rate: int | None = None,
        block_size: int = 1024,
        realtime: bool = True,
        prefill_blocks: int = DEFAULT_PREFILL_BLOCKS,
        loop: bool = False,
        ffmpeg_dir: str | Path | None = None,
        on_block=None,
    ) -> None:
        super().__init__(ring, sample_rate=target_sample_rate or 48_000, channels=1, on_block=on_block)
        self.path = Path(path)
        self.block_size = int(block_size)
        self.info: AudioFileInfo | None = None
        self._target_sample_rate = target_sample_rate
        self._realtime = bool(realtime)
        self._prefill_blocks = max(0, int(prefill_blocks))
        self._loop = bool(loop)
        self._ffmpeg_dir = ffmpeg_dir
        self._decoder: Decoder | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._pacing_reset = threading.Event()
        self._eof = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._emitted = 0
        self._late_events = 0

    # ---------------------------------------------------------------- 属性
    @property
    def duration_seconds(self) -> float:
        """文件总时长（秒）。"""
        if self.info is not None and self.info.duration > 0.0:
            return float(self.info.duration)
        return 0.0

    @property
    def position_seconds(self) -> float:
        """当前解码位置（秒）。"""
        with self._lock:
            decoder = self._decoder
            if decoder is None:
                return 0.0
            return float(getattr(decoder, "position_seconds", 0.0))

    @property
    def eof(self) -> bool:
        """是否已读到文件结尾。"""
        return self._eof.is_set()

    @property
    def late_events(self) -> int:
        """节拍落后次数（诊断用）。"""
        return self._late_events

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        self._decoder = open_decoder(
            self.path,
            target_sample_rate=self._target_sample_rate,
            channels=0,
            ffmpeg_dir=self._ffmpeg_dir,
        )
        self.info = self._decoder.info
        self.sample_rate = int(self._decoder.sample_rate)
        self.channels = int(self._decoder.channels)
        logger.info(
            "已打开文件：%s（%s，%.2f s，%d Hz，%d 声道%s）",
            self.path,
            self.info.format or self.path.suffix.lstrip("."),
            self.duration_seconds,
            self.sample_rate,
            self.channels,
            f"，{self.info.codec}" if self.info.codec else "",
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._eof.clear()
        self._paused.clear()
        self._emitted = 0
        self._thread = threading.Thread(target=self._run, name="FileSource", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():  # pragma: no cover - 解码阻塞时的兜底
                logger.warning("文件源线程未在 2 s 内退出")
        self._thread = None

    def close(self) -> None:
        self.stop()
        with self._lock:
            if self._decoder is not None:
                self._decoder.close()
                self._decoder = None
        super().close()

    def pause(self) -> None:
        """暂停推进（节拍基准在恢复时顺延，不产生时间跳变）。"""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def seek(self, seconds: float) -> None:
        """定位到指定秒数并重置节拍基准。"""
        target = max(0.0, float(seconds))
        with self._lock:
            if self._decoder is None:
                return
            self._decoder.seek(target)
            self._emitted = 0
        self._eof.clear()
        self._pacing_reset.set()
        logger.info("文件定位：%s → %.2f s", self.path.name, target)

    # ---------------------------------------------------------------- 内部
    def _read_block(self) -> np.ndarray | None:
        with self._lock:
            if self._decoder is None:
                return None
            return self._decoder.read(self.block_size)

    def _run(self) -> None:
        if self._decoder is None:  # pragma: no cover - start 前必先 open
            return
        sample_rate = float(self.sample_rate)
        prefill = self._prefill_blocks * self.block_size

        emitted = 0
        while emitted < prefill and not self._stop.is_set() and not self._pacing_reset.is_set():
            block = self._read_block()
            if block is None:
                break
            self._emit(block)
            emitted += int(block.shape[0])

        # 预填相当于"提前把这批样本产出完了"：节拍基准要回拨预填时长，
        # 否则源会在起播后干等一个预填时长，播出队列被抽干 → 欠载。
        start = time.perf_counter() - emitted / sample_rate
        with self._lock:
            self._emitted = emitted

        while not self._stop.is_set():
            if self._pacing_reset.is_set():
                self._pacing_reset.clear()
                emitted = 0
                start = time.perf_counter()

            if self._paused.is_set():
                pause_start = time.perf_counter()
                while self._paused.is_set() and not self._stop.is_set():
                    time.sleep(0.02)
                start += time.perf_counter() - pause_start
                continue

            block = self._read_block()
            if block is None:
                if self._loop:
                    self.seek(0.0)
                    continue
                self._eof.set()
                logger.info(
                    "文件播放结束：%s（共 %.2f s）", self.path.name, self.position_seconds
                )
                break

            self._emit(block)
            emitted += int(block.shape[0])
            with self._lock:
                self._emitted = emitted

            if not self._realtime:
                continue
            delay = start + emitted / sample_rate - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                self._late_events += 1
                if self._late_events <= 3 or self._late_events % 100 == 0:
                    logger.debug(
                        "文件源节拍落后 %.1f ms（第 %d 次）", -delay * 1000.0, self._late_events
                    )
                # 重新对齐，避免落后量累积
                start = time.perf_counter() - emitted / sample_rate
