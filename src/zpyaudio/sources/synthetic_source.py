"""合成测试信号源：无麦克风环境下验证波形/频谱/主频链路（验收项 A1、A6）。

用**样本计数**而非累积 ``time.sleep`` 控制节拍，避免系统抖动造成漂移（规格书 4.5）。
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from zpyaudio.sources.base import BaseSource

__all__ = ["SyntheticSource", "WAVEFORMS"]

logger = logging.getLogger(__name__)

#: 支持的测试波形
WAVEFORMS: tuple[str, ...] = ("sine", "silence", "noise")


class SyntheticSource(BaseSource):
    """按真实时间节拍产出正弦 / 静音 / 白噪声。"""

    name = "合成信号"

    def __init__(
        self,
        ring,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frequency: float = 1000.0,
        amplitude: float = 0.5,
        block_size: int = 1024,
        waveform: str = "sine",
        on_block=None,
    ) -> None:
        super().__init__(
            ring, sample_rate=sample_rate, channels=channels, on_block=on_block
        )
        if waveform not in WAVEFORMS:
            raise ValueError(f"不支持的波形 {waveform!r}，可选：{', '.join(WAVEFORMS)}")
        if not 0.0 < amplitude <= 1.0:
            raise ValueError(f"amplitude 必须在 (0, 1]，收到 {amplitude}")
        if block_size <= 0:
            raise ValueError(f"block_size 必须为正，收到 {block_size}")
        self.waveform = waveform
        self.frequency = float(frequency)
        self.amplitude = float(amplitude)
        self.block_size = int(block_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_index = 0
        self._rng = np.random.default_rng(20260101)

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        logger.info(
            "合成信号源已就绪：%s %.1f Hz，幅度 %.2f，采样率 %d Hz",
            self.waveform,
            self.frequency,
            self.amplitude,
            self.sample_rate,
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="SyntheticSource", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def close(self) -> None:
        self.stop()
        super().close()

    # ---------------------------------------------------------------- 内部
    def _generate(self) -> np.ndarray:
        n = self.block_size
        freq = self.frequency / float(self.sample_rate)
        if self.waveform == "sine":
            phase = (self._sample_index + np.arange(n, dtype=np.float64)) * freq
            mono = self.amplitude * np.sin(2.0 * np.pi * phase)
        elif self.waveform == "noise":
            mono = self.amplitude * self._rng.standard_normal(n)
        else:  # silence
            mono = np.zeros(n, dtype=np.float64)
        self._sample_index += n
        block = np.repeat(mono[:, None], self.channels, axis=1)
        return np.clip(block, -1.0, 1.0).astype(np.float32)

    def _run(self) -> None:
        hop = self.block_size / float(self.sample_rate)
        next_time = time.perf_counter()
        late_events = 0
        while not self._stop.is_set():
            self._emit(self._generate())
            next_time += hop
            delay = next_time - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                late_events += 1
                if late_events <= 3 or late_events % 100 == 0:
                    logger.debug("合成源节拍落后 %.1f ms（第 %d 次）", -delay * 1000.0, late_events)
                next_time = time.perf_counter()
