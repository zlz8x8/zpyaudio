"""测试替身（不依赖声卡/麦克风）。"""

from __future__ import annotations

__all__ = ["FakePlayer"]


class FakePlayer:
    """播放器替身：喂入即视为已播放，用于无声卡环境验证播放链路。"""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int = 1,
        device=None,
        block_size: int = 1024,
        queue_blocks: int = 8,
        latency: str = "low",
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.block_size = int(block_size)
        self.opened = False
        self.started = False
        self.paused = False
        self.frames: list = []
        self.frames_fed = 0

    def open(self) -> None:
        self.opened = True

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.opened = False

    def feed(self, frame) -> None:
        self.frames.append(frame)
        self.frames_fed += frame.frames

    def pause(self, paused: bool = True) -> None:
        self.paused = bool(paused)

    @property
    def position_seconds(self) -> float:
        return self.frames_fed / float(self.sample_rate)

    @property
    def queued_blocks(self) -> int:
        return 0

    @property
    def queue_capacity(self) -> int:
        """0 表示不缓冲：控制器会跳过播放预填等待。"""
        return 0

    @property
    def underruns(self) -> int:
        return 0

    @property
    def dropped_blocks(self) -> int:
        return 0
