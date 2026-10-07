"""播放线程（规格书 4.5）。

播放与分析共用同一路解码数据：文件源把每个块同时写入环形缓冲（分析）与
播放器队列（出声），因此不会出现"听得到但图不动"或反之（FR-3.3）。

暂停 = 输出静音并冻结播放位置，队列不清空，恢复后从原位置继续。
"""

from __future__ import annotations

import logging
import queue

import numpy as np

from zpyaudio.core.frames import AudioFrame

__all__ = ["Player", "output_settings_ok", "default_output_sample_rate"]

logger = logging.getLogger(__name__)

#: 输出队列深度（块）：越大越抗抖动，但播放延迟越大
DEFAULT_QUEUE_BLOCKS = 8


def _sd():
    import sounddevice as sd  # 延迟导入：无音频设备的环境仍可跑合成源与测试

    return sd


def default_output_sample_rate(device: int | None = None) -> int:
    """输出设备的默认采样率；不可用时返回 48000。"""
    try:
        info = _sd().query_devices(device, "output")
        rate = int(info.get("default_samplerate") or 0)
        return rate or 48_000
    except Exception:
        logger.debug("读取默认输出采样率失败", exc_info=True)
        return 48_000


def output_settings_ok(sample_rate: int, channels: int = 1, device: int | None = None) -> bool:
    """设备是否支持该采样率/声道组合（规格书第十章风险对策）。"""
    try:
        _sd().check_output_settings(
            device=device, samplerate=int(sample_rate), channels=int(channels), dtype="float32"
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 任何失败都表示不支持
        logger.debug("输出设置不受支持（%s Hz/%s ch）：%s", sample_rate, channels, exc)
        return False


class Player:
    """把音频块写入 ``sounddevice.OutputStream``。"""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int = 1,
        device: int | None = None,
        block_size: int = 1024,
        queue_blocks: int = DEFAULT_QUEUE_BLOCKS,
        latency: str = "low",
    ) -> None:
        if sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正，收到 {sample_rate}")
        if channels <= 0:
            raise ValueError(f"channels 必须为正，收到 {channels}")
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.device = device
        self.block_size = int(block_size)
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(1, int(queue_blocks)))
        self._stream = None
        self._current: np.ndarray | None = None
        self._paused = False
        self._frames_played = 0
        self._underruns = 0
        self._dropped_blocks = 0
        self._status_events = 0
        self._latency = latency

    # ---------------------------------------------------------------- 属性
    @property
    def position_seconds(self) -> float:
        """已播放时长（秒），即用户实际听到的位置。"""
        return self._frames_played / float(self.sample_rate)

    @property
    def underruns(self) -> int:
        """输出欠载次数：回调取不到数据而输出静音的次数（应为 0）。"""
        return self._underruns

    @property
    def dropped_blocks(self) -> int:
        """因队列已满而丢弃的块数（正常起播预填时会丢弃旧块）。"""
        return self._dropped_blocks

    @property
    def queued_blocks(self) -> int:
        """待播放块数。"""
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        """输出队列容量（块）。"""
        return self._queue.maxsize

    @property
    def paused(self) -> bool:
        return self._paused

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        sd = _sd()
        self._stream = sd.OutputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=self.block_size,
            latency=self._latency,
            callback=self._callback,
        )
        logger.info(
            "播放输出已打开：%d Hz，%d 声道，块长 %d", self.sample_rate, self.channels, self.block_size
        )

    def start(self) -> None:
        if self._stream is None:
            raise RuntimeError("请先调用 open() 再 start()")
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and not self._stream.stopped:
            self._stream.stop()
        self._drain()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        self._drain()

    def _drain(self) -> None:
        self._current = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # ---------------------------------------------------------------- 数据
    def feed(self, frame: AudioFrame) -> None:
        """把一块音频放入输出队列（有界，满则丢弃最旧并计数）。"""
        samples = np.asarray(frame.samples, dtype=np.float32)
        if samples.ndim == 1:
            samples = samples.reshape(-1, 1)
        if samples.shape[1] != self.channels:
            if samples.shape[1] == 1:
                samples = np.repeat(samples, self.channels, axis=1)
            else:
                samples = samples[:, : self.channels]
        try:
            self._queue.put_nowait(np.ascontiguousarray(samples))
            return
        except queue.Full:
            pass
        self._dropped_blocks += 1
        try:
            self._queue.get_nowait()
        except queue.Empty:  # pragma: no cover - 与上个分支竞争
            pass
        try:
            self._queue.put_nowait(np.ascontiguousarray(samples))
        except queue.Full:  # pragma: no cover
            logger.debug("播放队列仍满，丢弃本块")

    def pause(self, paused: bool = True) -> None:
        """暂停/恢复输出（暂停时输出静音，位置冻结，队列保留）。"""
        self._paused = bool(paused)

    # ---------------------------------------------------------------- 回调
    def _callback(self, outdata, frames, time_info, status) -> None:
        if status:
            self._status_events += 1
            if self._status_events <= 3 or self._status_events % 100 == 0:
                logger.warning("播放状态告警（第 %d 次）：%s", self._status_events, status)
        outdata.fill(0.0)
        if self._paused:
            return

        filled = 0
        while filled < frames:
            chunk = self._current
            if chunk is None or chunk.shape[0] == 0:
                try:
                    self._current = self._queue.get_nowait()
                except queue.Empty:
                    self._underruns += 1
                    if self._underruns <= 3 or self._underruns % 100 == 0:
                        logger.debug("播放欠载（第 %d 次）", self._underruns)
                    break
                continue
            take = min(frames - filled, chunk.shape[0])
            outdata[filled : filled + take] = chunk[:take]
            self._current = chunk[take:]
            filled += take
            self._frames_played += take
