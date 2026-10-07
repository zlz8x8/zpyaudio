"""音频输入设备源（规格书 S1 / 5.1）。

``sounddevice`` 采用**延迟导入**：没有音频设备或未安装 PortAudio 的环境仍可
跑通合成源、单元测试与离屏 GUI 冒烟。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from zpyaudio.sources.base import BaseSource

__all__ = ["DeviceInfo", "list_input_devices", "default_input_device", "DeviceSource"]

logger = logging.getLogger(__name__)

#: 状态告警日志的抽稀间隔（避免回调里刷屏）
_STATUS_LOG_EVERY = 100


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """输入设备描述。"""

    index: int
    name: str
    channels: int
    default_sample_rate: float
    is_default: bool


def _sd():
    import sounddevice as sd  # 延迟导入

    return sd


def list_input_devices() -> list[DeviceInfo]:
    """枚举可用的输入设备（无可用设备时返回空列表）。"""
    sd = _sd()
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
    except Exception:
        logger.exception("枚举音频设备失败")
        return []

    result: list[DeviceInfo] = []
    for index, info in enumerate(devices):
        channels = int(info.get("max_input_channels", 0) or 0)
        if channels <= 0:
            continue
        result.append(
            DeviceInfo(
                index=index,
                name=str(info.get("name", f"设备{index}")),
                channels=channels,
                default_sample_rate=float(info.get("default_samplerate", 0.0) or 0.0),
                is_default=index == default_in,
            )
        )
    return result


def default_input_device() -> int | None:
    """系统默认输入设备索引；不可用时返回 ``None``。"""
    sd = _sd()
    try:
        index = sd.default.device[0]
    except Exception:
        logger.exception("读取默认输入设备失败")
        return None
    return int(index) if index is not None and int(index) >= 0 else None


class DeviceSource(BaseSource):
    """基于 ``sounddevice.InputStream`` 的采集源（回调线程只写环形缓冲）。"""

    name = "输入设备"

    #: 实际设备名（用于录制文件名，规格书 FR-1.2）
    device_name = "输入设备"

    def __init__(
        self,
        ring,
        *,
        device: int | None = None,
        sample_rate: int = 48_000,
        channels: int = 1,
        block_size: int = 1024,
        latency: str = "low",
        on_block=None,
    ) -> None:
        super().__init__(
            ring, sample_rate=sample_rate, channels=channels, on_block=on_block
        )
        self._device = device
        self.block_size = int(block_size)
        self.latency = latency
        self._stream = None
        self._sd = None
        self._status_events = 0
        self._fallback_note: str | None = None

    # ---------------------------------------------------------------- 属性
    @property
    def fallback_note(self) -> str | None:
        """采样率回退说明（供日志区展示），无回退时为 ``None``。"""
        return self._fallback_note

    # ---------------------------------------------------------------- 生命周期
    def open(self) -> None:
        sd = _sd()
        self._sd = sd
        device = self._device if self._device is not None else default_input_device()

        info = sd.query_devices(device, "input")
        name = str(info.get("name", "输入设备"))
        self.name = f"输入设备：{name}"
        self.device_name = name

        max_channels = int(info.get("max_input_channels", 1) or 1)
        channels = max(1, min(self.channels, max_channels))
        if channels != self.channels:
            logger.warning(
                "设备 %s 最多支持 %d 个输入声道，已从 %d 调整为 %d",
                name,
                max_channels,
                self.channels,
                channels,
            )
        self.channels = channels

        rate = self._negotiate_sample_rate(sd, device, info, channels)
        self._stream = sd.InputStream(
            device=device,
            samplerate=rate,
            channels=channels,
            dtype="float32",
            blocksize=self.block_size,
            latency=self.latency,
            callback=self._callback,
        )
        logger.info(
            "已打开输入设备：%s（采样率 %d Hz，声道 %d，块长 %d）",
            name,
            rate,
            channels,
            self.block_size,
        )

    def _negotiate_sample_rate(self, sd, device, info, channels: int) -> int:
        """校验请求采样率，不支持时回退到设备默认值（规格书第十章风险对策）。"""
        requested = int(self.sample_rate)
        try:
            sd.check_input_settings(
                device=device, samplerate=requested, channels=channels, dtype="float32"
            )
            return requested
        except Exception as exc:
            fallback = int(info.get("default_samplerate") or 0) or 48_000
            self._fallback_note = (
                f"设备不支持 {requested} Hz（{exc}），已回退到 {fallback} Hz"
            )
            logger.warning(self._fallback_note)
            self.sample_rate = fallback
            return fallback

    def start(self) -> None:
        if self._stream is None:
            raise RuntimeError("请先调用 open() 再 start()")
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and not self._stream.stopped:
            self._stream.stop()

    def pause(self) -> None:
        """暂停采集：停止输入流，避免驱动缓冲堆积后"补播"暂停期间的数据。"""
        self.stop()

    def resume(self) -> None:
        """恢复采集。"""
        if self._stream is not None and self._stream.stopped:
            self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()

    # ---------------------------------------------------------------- 回调
    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self._status_events += 1
            if self._status_events <= 3 or self._status_events % _STATUS_LOG_EVERY == 0:
                logger.warning("采集状态告警（第 %d 次）：%s", self._status_events, status)
        self._emit(np.asarray(indata, dtype=np.float32))
