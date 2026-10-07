"""音频输入源抽象（规格书 5.1）。

数据流：源 → ``RingBuffer`` →（可选）块回调（M3 录制用）。
源自身不启动线程以外的逻辑，分析由 ``app.controller`` 负责。
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol, runtime_checkable

import numpy as np

from zpyaudio.core.frames import AudioFrame
from zpyaudio.core.resample import prepare_block
from zpyaudio.core.ringbuffer import RingBuffer

__all__ = ["AudioSource", "BaseSource"]

logger = logging.getLogger(__name__)


@runtime_checkable
class AudioSource(Protocol):
    """输入源协议。"""

    name: str
    sample_rate: int
    channels: int

    def open(self) -> None:
        """申请资源（打开设备 / 准备数据）。"""

    def start(self) -> None:
        """开始产出数据。"""

    def stop(self) -> None:
        """停止产出数据，保留资源。"""

    def close(self) -> None:
        """释放资源。"""


class BaseSource:
    """源基类：统一时钟（样本计数）与环形缓冲写入。"""

    #: 子类覆盖
    name = "source"

    def __init__(
        self,
        ring: RingBuffer,
        *,
        sample_rate: int,
        channels: int,
        on_block=None,
    ) -> None:
        self.ring = ring
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._on_block = on_block
        self._frames_written = 0
        self._closed = False
        self._paused = threading.Event()

    # ---------------------------------------------------------------- 属性
    @property
    def timestamp(self) -> float:
        """下一个待写入样本的流时间（秒），由样本计数推导（规格书 4.1）。"""
        return self._frames_written / float(self.sample_rate)

    @property
    def frames_written(self) -> int:
        """累计写入样本数。"""
        return self._frames_written

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def start(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def pause(self) -> None:
        """暂停产出：通用实现为丢弃后续块（子类可覆盖为更彻底的方式）。

        设备源会额外 ``stream.stop()``（避免驱动缓冲堆积），
        文件源会暂停节拍推进。
        """
        self._paused.set()

    def resume(self) -> None:
        """恢复产出。"""
        self._paused.clear()

    def set_block_callback(self, callback) -> None:
        """设置/替换块回调（录制与播放共用的挂载点）。"""
        self._on_block = callback

    def close(self) -> None:
        """默认无资源可释放。"""
        self._closed = True

    # ---------------------------------------------------------------- 内部
    def _emit(self, block: np.ndarray) -> None:
        """把一块样本写入环形缓冲（回调线程内调用，只做拷贝与计数）。

        **不加锁**（规格书 5.2）：单个源只有一个写入线程（设备回调线程、合成源
        线程或文件源线程），因此只要"先写缓冲、后累加样本计数"，时间戳就单调
        且与数据一致。

        环形缓冲按**分析用声道数**写入（可能是降混），而块回调拿到的是
        源原始声道的样本（播放需要立体声，FR-3.3）。
        """
        arr = prepare_block(block, self.channels)
        if arr.shape[0] == 0:
            return
        if self._paused.is_set():
            # 暂停期间丢弃（规格书 FR-1.4：不写入样本）
            return
        timestamp = self.timestamp
        self.ring.write(prepare_block(arr, self.ring.channels))
        self._frames_written += int(arr.shape[0])
        if self._on_block is not None:
            try:
                self._on_block(
                    AudioFrame(arr, self.sample_rate, self.channels, timestamp)
                )
            except Exception:  # pragma: no cover - 回调异常不得影响采集
                logger.exception("块回调执行失败（已忽略，不影响采集）")
