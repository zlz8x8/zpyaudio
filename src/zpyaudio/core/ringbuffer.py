"""环形缓冲区：单写多读，无锁（规格书 4.6）。

设计取舍
--------
音频回调线程里禁止加锁与分配（规格书 5.2），因此这里使用「写序号校验 + 有限重试」
的近似无锁方案：

* 写方只做 numpy 切片赋值与一个整数自增（GIL 下原子）；
* 读方先读 ``written``，拷贝完成后比对 ``written`` 是否变化；变化则重试，
  重试耗尽后按已拷贝结果返回，并把该次竞争计入 ``overrun``。

容量不足时丢弃最旧数据，累计 ``dropped_samples`` / ``overrun``，供状态下栏告警
（规格书 7.2「状态栏」）。
"""

from __future__ import annotations

import numpy as np

__all__ = ["RingBuffer"]

#: 读方为规避并发覆盖的最大重试次数
_READ_RETRY = 3


class RingBuffer:
    """定长环形缓冲，样本形状固定为 ``(capacity, channels)``。"""

    def __init__(self, capacity: int, channels: int = 1, dtype: np.dtype | type = np.float32) -> None:
        if capacity <= 1:
            raise ValueError(f"capacity 必须大于 1，收到 {capacity}")
        if channels <= 0:
            raise ValueError(f"channels 必须为正，收到 {channels}")
        self._capacity = int(capacity)
        self._channels = int(channels)
        self._dtype = np.dtype(dtype)
        self._buf = np.zeros((self._capacity, self._channels), dtype=self._dtype)
        self._written = 0
        self._overrun = 0
        self._dropped = 0

    # ---------------------------------------------------------------- 属性
    @property
    def capacity(self) -> int:
        """容量（样本数）。"""
        return self._capacity

    @property
    def channels(self) -> int:
        """声道数。"""
        return self._channels

    @property
    def written(self) -> int:
        """累计写入的样本数（单调递增，用于推导流时间）。"""
        return self._written

    @property
    def available(self) -> int:
        """当前可读的样本数。"""
        return min(self._written, self._capacity)

    @property
    def overrun(self) -> int:
        """溢出事件计数：写入超容量、或读取遇到被覆盖。"""
        return self._overrun

    @property
    def dropped_samples(self) -> int:
        """因写入超容量而丢弃的样本总数。"""
        return self._dropped

    # ---------------------------------------------------------------- 写入
    def write(self, block: np.ndarray) -> int:
        """写入一块样本，返回实际存入的样本数。

        块长超过容量时只保留最新的 ``capacity`` 个样本。
        """
        arr = np.asarray(block, dtype=self._dtype)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        if arr.ndim != 2 or arr.shape[1] != self._channels:
            raise ValueError(
                f"写入块形状 {arr.shape} 与缓冲区声道数 {self._channels} 不匹配"
            )
        n = int(arr.shape[0])
        if n == 0:
            return 0
        if n >= self._capacity:
            self._dropped += n - self._capacity
            self._overrun += 1
            arr = arr[-self._capacity :]
            n = self._capacity

        start = self._written
        pos = start % self._capacity
        first = min(n, self._capacity - pos)
        self._buf[pos : pos + first] = arr[:first]
        if n > first:
            self._buf[: n - first] = arr[first:]
        self._written = start + n
        return n

    # ---------------------------------------------------------------- 读取
    def read_latest(self, n: int) -> np.ndarray | None:
        """返回最近 ``n`` 个样本的副本，形状 ``(n, channels)``。

        数据不足（尚未写满 ``n`` 个样本）时返回 ``None``。
        """
        if n <= 0:
            raise ValueError(f"n 必须为正，收到 {n}")
        if n > self._capacity:
            raise ValueError(f"n={n} 超过容量 {self._capacity}")
        for _ in range(_READ_RETRY):
            written = self._written
            if written < n:
                return None
            out = self._copy(written - n, n)
            if self._written == written:
                return out
        self._overrun += 1
        return self._copy(self._written - n, n)

    def clear(self) -> None:
        """清空数据并复位计数（停止时调用，规格书 5.3）。"""
        self._buf.fill(0)
        self._written = 0
        self._overrun = 0
        self._dropped = 0

    # ---------------------------------------------------------------- 内部
    def _copy(self, start_abs: int, n: int) -> np.ndarray:
        pos = start_abs % self._capacity
        first = min(n, self._capacity - pos)
        out = np.empty((n, self._channels), dtype=self._dtype)
        out[:first] = self._buf[pos : pos + first]
        if n > first:
            out[first:] = self._buf[: n - first]
        return out
