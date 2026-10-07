"""音频文件解码（规格书 4.5）。

* **wav / flac / ogg 等无损格式**：``soundfile`` 直接读，无子进程开销；
* **mp3 / m4a / aac 等有损格式**：不自行解码，调用 ``ffmpeg`` 输出裸 PCM 流
  （``-f f32le -acodec pcm_f32le``）；seek 用 ``-ss`` 重启子进程；
* **元信息**：``ffprobe`` 读取时长/采样率/声道。

采样率规整策略：工程采样率与分析/播放目标一致时才走 ``soundfile``；
需要重采样时统一交给 ffmpeg（其多相重采样有正确的滤波状态，
而逐块 ``resample_poly`` 会在块边界产生周期性失真）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import soundfile as sf

__all__ = [
    "AudioFileInfo",
    "Decoder",
    "SoundFileDecoder",
    "FfmpegDecoder",
    "ResampleRequired",
    "find_ffmpeg",
    "find_ffprobe",
    "probe",
    "open_decoder",
    "LOSSLESS_SUFFIXES",
]

logger = logging.getLogger(__name__)

#: 可直接用 soundfile 打开的无损/容器格式
LOSSLESS_SUFFIXES = frozenset(
    {".wav", ".wave", ".flac", ".ogg", ".oga", ".aiff", ".aif", ".aifc", ".au", ".w64", ".caf"}
)

#: 优先搜索的 ffmpeg 目录（规格书 2.1：C:\ffmpeg）
DEFAULT_FFMPEG_DIRS = (r"C:\ffmpeg\bin", r"C:\ffmpeg")

#: 每次读取样本数上限（防御异常参数）
_MAX_READ_FRAMES = 1 << 22

#: 子进程无控制台窗口（Windows）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ResampleRequired(RuntimeError):
    """soundfile 解码时发现采样率与目标不一致，需要改用 ffmpeg。"""


@dataclass(frozen=True, slots=True)
class AudioFileInfo:
    """音频文件元信息。"""

    path: Path
    duration: float
    sample_rate: int
    channels: int
    codec: str = ""
    format: str = ""

    @property
    def name(self) -> str:
        return self.path.name


@runtime_checkable
class Decoder(Protocol):
    """解码器协议：按块拉取 PCM。"""

    info: AudioFileInfo
    sample_rate: int
    channels: int

    def read(self, frames: int) -> np.ndarray | None:
        """读取 ``frames`` 个样本，返回 ``(n, channels)`` float32；EOF 返回 ``None``。"""

    def seek(self, seconds: float) -> None: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------- ffmpeg
def find_ffmpeg(extra_dir: str | Path | None = None) -> Path | None:
    """定位 ffmpeg 可执行文件；找不到返回 ``None``。"""
    return _find_tool("ffmpeg", extra_dir)


def find_ffprobe(extra_dir: str | Path | None = None) -> Path | None:
    """定位 ffprobe 可执行文件；找不到返回 ``None``。"""
    return _find_tool("ffprobe", extra_dir)


def _find_tool(name: str, extra_dir: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if extra_dir:
        base = Path(extra_dir)
        if base.is_file():
            candidates.append(base)
        else:
            candidates.extend([base / f"{name}.exe", base / name])
    for directory in DEFAULT_FFMPEG_DIRS:
        candidates.extend([Path(directory) / f"{name}.exe", Path(directory) / name])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _require_ffmpeg(extra_dir: str | Path | None = None) -> Path:
    tool = find_ffmpeg(extra_dir)
    if tool is None:
        raise FileNotFoundError(
            "未找到 ffmpeg：请在设置中指定 ffmpeg 所在目录，"
            r"或将其加入 PATH（例如 C:\ffmpeg\bin）"
        )
    return tool


def probe(path: str | Path, *, ffprobe_dir: str | Path | None = None) -> AudioFileInfo:
    """读取音频文件元信息：优先 soundfile，失败时用 ffprobe。"""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")
    try:
        with sf.SoundFile(str(target)) as handle:
            return AudioFileInfo(
                path=target,
                duration=float(len(handle)) / float(handle.samplerate)
                if handle.samplerate
                else 0.0,
                sample_rate=int(handle.samplerate),
                channels=int(handle.channels),
                codec=str(handle.format_info if hasattr(handle, "format_info") else ""),
                format=str(handle.format),
            )
    except Exception as exc:  # noqa: BLE001 - 交给 ffprobe 兜底
        logger.debug("soundfile 无法读取 %s（%s），改用 ffprobe", target.name, exc)

    ffprobe = find_ffprobe(ffprobe_dir)
    if ffprobe is None:
        raise FileNotFoundError(
            f"无法读取音频文件 {target.name}，且未找到 ffprobe（ffmpeg 缺失）"
        ) from None
    return _probe_with_ffprobe(ffprobe, target)


def _probe_with_ffprobe(ffprobe: Path, path: Path) -> AudioFileInfo:
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=sample_rate,channels,codec_name",
        "-select_streams",
        "a:0",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"ffprobe 读取失败：{completed.stderr.strip() or '未知错误'}")

    import json

    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0]
    container = payload.get("format") or {}
    return AudioFileInfo(
        path=path,
        duration=float(container.get("duration") or 0.0),
        sample_rate=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        codec=str(stream.get("codec_name") or ""),
        format=str(container.get("format_name") or ""),
    )


# --------------------------------------------------------------------- 解码器
class SoundFileDecoder:
    """基于 ``soundfile`` 的无损格式解码器。"""

    def __init__(
        self, path: str | Path, *, target_sample_rate: int | None = None
    ) -> None:
        self.info = probe(path)
        if target_sample_rate and int(target_sample_rate) != self.info.sample_rate:
            raise ResampleRequired(
                f"{self.info.sample_rate} Hz → {target_sample_rate} Hz 需要重采样，改用 ffmpeg"
            )
        self._handle = sf.SoundFile(str(path))
        self.sample_rate = int(self._handle.samplerate)
        self.channels = int(self._handle.channels)
        self._frames_read = 0

    @property
    def frames_read(self) -> int:
        """自上次定位以来已读出的样本数。"""
        return self._frames_read

    @property
    def position_seconds(self) -> float:
        """当前解码位置（秒）。``seek`` 后 ``frames_read`` 即为绝对位置。"""
        return self._frames_read / float(self.sample_rate)

    def read(self, frames: int) -> np.ndarray | None:
        count = int(frames)
        if count <= 0 or count > _MAX_READ_FRAMES:
            raise ValueError(f"frames 必须在 1..{_MAX_READ_FRAMES}，收到 {frames}")
        data = self._handle.read(count, dtype="float32", always_2d=True)
        if data.shape[0] == 0:
            return None
        self._frames_read += int(data.shape[0])
        return np.ascontiguousarray(data, dtype=np.float32)

    def seek(self, seconds: float) -> None:
        frame = max(0, int(round(float(seconds) * self.sample_rate)))
        self._handle.seek(frame)
        self._frames_read = frame

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:  # pragma: no cover - 关闭失败无补救手段
            logger.debug("关闭 soundfile 句柄失败", exc_info=True)


class FfmpegDecoder:
    """基于 ffmpeg 子进程的解码器（同时负责重采样与声道转换）。"""

    def __init__(
        self,
        path: str | Path,
        *,
        sample_rate: int = 48_000,
        channels: int = 0,
        info: AudioFileInfo | None = None,
        ffmpeg_dir: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        # 先确认 ffmpeg 可用，报错才能给出可操作的指引（而不是"读不到文件"）
        self._ffmpeg = _require_ffmpeg(ffmpeg_dir)
        self._ffmpeg_dir = ffmpeg_dir
        self.info = info or probe(self.path, ffprobe_dir=ffmpeg_dir)
        # channels=0 表示沿用文件声道数
        self.channels = int(channels) if channels else max(1, self.info.channels)
        self._process: subprocess.Popen | None = None
        self._frames_read = 0
        self._seek_offset = 0.0
        self._spawn(0.0)

    # ---------------------------------------------------------------- 属性
    @property
    def frames_read(self) -> int:
        """自上次定位以来已读出的样本数。"""
        return self._frames_read

    @property
    def position_seconds(self) -> float:
        """当前解码位置（秒，含 seek 偏移）。"""
        return self._seek_offset + self._frames_read / float(self.sample_rate)

    # ---------------------------------------------------------------- 进程
    def _spawn(self, start_seconds: float) -> None:
        command = [
            str(self._ffmpeg),
            "-hide_banner",
            "-v",
            "error",
            "-nostdin",
        ]
        if start_seconds > 0.0:
            command += ["-ss", f"{start_seconds:.6f}"]
        command += [
            "-i",
            str(self.path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(self.channels),
            "-ar",
            str(self.sample_rate),
            "pipe:1",
        ]
        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,
        )
        self._frames_read = 0
        self._seek_offset = max(0.0, float(start_seconds))
        logger.debug("ffmpeg 已启动：%s @ %.3fs", self.path.name, self._seek_offset)

    def read(self, frames: int) -> np.ndarray | None:
        count = int(frames)
        if count <= 0 or count > _MAX_READ_FRAMES:
            raise ValueError(f"frames 必须在 1..{_MAX_READ_FRAMES}，收到 {frames}")
        process = self._process
        if process is None or process.stdout is None:
            return None
        wanted = count * self.channels * 4  # float32
        chunks: list[bytes] = []
        remaining = wanted
        while remaining > 0:
            chunk = process.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not chunks:
            self._log_stderr_tail()
            return None
        buffer = b"".join(chunks)
        samples = np.frombuffer(buffer, dtype="<f4")
        usable = (samples.size // self.channels) * self.channels
        if usable == 0:
            return None
        data = samples[:usable].reshape(-1, self.channels)
        self._frames_read += int(data.shape[0])
        return np.ascontiguousarray(data, dtype=np.float32)

    def seek(self, seconds: float) -> None:
        self._terminate()
        self._spawn(max(0.0, float(seconds)))

    def close(self) -> None:
        self._terminate()

    # ---------------------------------------------------------------- 内部
    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:  # pragma: no cover - 罕见
                    process.kill()
        except Exception:  # pragma: no cover
            logger.debug("终止 ffmpeg 子进程失败", exc_info=True)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # pragma: no cover
                    pass

    def _log_stderr_tail(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            message = process.stderr.read().decode("utf-8", errors="replace").strip()
        except Exception:  # pragma: no cover
            return
        if message:
            logger.warning("ffmpeg 报告：%s", message)


# --------------------------------------------------------------------- 工厂
def open_decoder(
    path: str | Path,
    *,
    target_sample_rate: int | None = None,
    channels: int = 0,
    ffmpeg_dir: str | Path | None = None,
) -> Decoder:
    """按扩展名选择解码器。

    ``channels=0`` 表示沿用文件声道数；``target_sample_rate`` 为目标采样率
    （不一致时统一交给 ffmpeg 重采样）。
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在：{target}")

    if target.suffix.lower() in LOSSLESS_SUFFIXES:
        try:
            return SoundFileDecoder(target, target_sample_rate=target_sample_rate)
        except ResampleRequired as exc:
            logger.info("%s，改用 ffmpeg", exc)
        except Exception as exc:  # noqa: BLE001 - 回落 ffmpeg
            logger.warning("soundfile 打开 %s 失败（%s），改用 ffmpeg", target.name, exc)

    info = probe(target, ffprobe_dir=ffmpeg_dir)
    rate = int(target_sample_rate) if target_sample_rate else info.sample_rate
    if rate <= 0:
        raise ValueError(f"无法确定采样率：{target.name}")
    return FfmpegDecoder(
        target,
        sample_rate=rate,
        channels=channels,
        info=info,
        ffmpeg_dir=ffmpeg_dir,
    )
