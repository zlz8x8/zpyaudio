"""配置与持久化（规格书 FR-6 / 4.3 / 4.4）。

配置缺失或损坏时回退默认值，非法取值被纠正并记录告警（FR-6.2）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from zpyaudio.core.analyzer import FFT_SIZES, WINDOW_FUNCTIONS, AnalyzerConfig
from zpyaudio.core.pitch import PITCH_METHODS

__all__ = [
    "AppConfig",
    "PROJECT_ROOT",
    "DEFAULT_CONFIG_PATH",
    "SAMPLE_RATES",
    "BLOCK_SIZES",
    "load_config",
    "save_config",
]

logger = logging.getLogger(__name__)

#: 项目根目录（src/zpyaudio/app/config.py → 上溯三级）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: 默认配置文件位置（规格书 FR-6.2：./config.json）
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

#: 可选采样率（规格书 4.2）
SAMPLE_RATES: tuple[int, ...] = (16_000, 44_100, 48_000)

#: 可选设备块长（规格书 4.1）
BLOCK_SIZES: tuple[int, ...] = (512, 1024, 2048)

#: 可选波形显示时间窗（规格书 FR-2.1）
WAVEFORM_SECONDS: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.0)

#: 可选主频时间窗（规格书 7.2：默认 10 s，可选 5/10/30/60 s）
PITCH_SECONDS: tuple[float, ...] = (5.0, 10.0, 30.0, 60.0)

#: 录制写盘的采样格式（规格书 FR-1.3）
RECORD_SUBTYPES: tuple[str, ...] = ("PCM_16", "PCM_24", "FLOAT")

LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass
class AppConfig:
    """应用配置（字段默认值取自规格书）。"""

    device: int | None = None
    sample_rate: int = 48_000
    block_size: int = 1024
    fft_size: int = 4096
    window: str = "hann"
    zero_pad: int = 1
    pitch_method: str = "fft_parabolic"
    fmin: float = 50.0
    fmax: float = 5000.0
    rms_gate_db: float = -60.0
    waveform_seconds: float = 0.5
    waveform_autoscale: bool = False
    spectrum_log_x: bool = False
    pitch_seconds: float = 10.0
    pitch_smoothing: bool = True
    ring_seconds: float = 5.0
    records_dir: str = "records"
    logs_dir: str = "logs"
    ffmpeg_dir: str = r"C:\ffmpeg\bin"
    record_subtype: str = "PCM_16"
    log_level: str = "INFO"
    _notes: list[str] = field(default_factory=list, repr=False, compare=False)

    # ---------------------------------------------------------------- 派生
    def analyzer_config(self) -> AnalyzerConfig:
        """映射为分析器配置。"""
        return AnalyzerConfig(
            sample_rate=self.sample_rate,
            window_size=self.fft_size,
            window=self.window,
            zero_pad=self.zero_pad,
            pitch_method=self.pitch_method,
            fmin=self.fmin,
            fmax=self.fmax,
            rms_gate_db=self.rms_gate_db,
        )

    def resolve_path(self, value: str | Path) -> Path:
        """把配置里的相对路径解析到项目根目录。"""
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def records_path(self) -> Path:
        """录制输出目录。"""
        return self.resolve_path(self.records_dir)

    @property
    def logs_path(self) -> Path:
        """日志输出目录。"""
        return self.resolve_path(self.logs_dir)

    # ---------------------------------------------------------------- 校验
    def validate(self) -> list[str]:
        """纠正非法取值，返回被纠正项的说明列表。"""
        notes: list[str] = []

        def fix_choice(name: str, allowed, default) -> None:
            value = getattr(self, name)
            if value not in allowed:
                notes.append(f"{name}={value!r} 非法，已回退为 {default!r}")
                setattr(self, name, default)

        def fix_range(name: str, low: float, high: float, default) -> None:
            value = getattr(self, name)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                notes.append(f"{name}={value!r} 不是数字，已回退为 {default!r}")
                setattr(self, name, default)
                return
            if not low <= numeric <= high:
                notes.append(f"{name}={numeric} 超出范围 [{low}, {high}]，已回退为 {default!r}")
                setattr(self, name, default)

        fix_choice("sample_rate", SAMPLE_RATES, 48_000)
        fix_choice("block_size", BLOCK_SIZES, 1024)
        fix_choice("fft_size", FFT_SIZES, 4096)
        fix_choice("window", WINDOW_FUNCTIONS, "hann")
        fix_choice("zero_pad", (1, 2), 1)
        fix_choice("pitch_method", PITCH_METHODS, "fft_parabolic")
        fix_choice("log_level", LOG_LEVELS, "INFO")
        fix_choice("pitch_seconds", PITCH_SECONDS, 10.0)
        fix_choice("record_subtype", RECORD_SUBTYPES, "PCM_16")
        fix_range("waveform_seconds", 0.05, 2.0, 0.5)
        fix_range("ring_seconds", 1.0, 600.0, 5.0)
        fix_range("fmin", 1.0, 20_000.0, 50.0)
        fix_range("fmax", 1.0, 24_000.0, 5000.0)
        fix_range("rms_gate_db", -120.0, 0.0, -60.0)
        fix_range("zero_pad", 1, 2, 1)

        if self.fmin >= self.fmax:
            notes.append(f"fmin={self.fmin} 不小于 fmax={self.fmax}，已回退为 50/5000")
            self.fmin, self.fmax = 50.0, 5000.0
        if self.device is not None:
            try:
                self.device = int(self.device)
            except (TypeError, ValueError):
                notes.append(f"device={self.device!r} 非法，已回退为系统默认设备")
                self.device = None
        if not isinstance(self.spectrum_log_x, bool):
            self.spectrum_log_x = bool(self.spectrum_log_x)
        if not isinstance(self.waveform_autoscale, bool):
            self.waveform_autoscale = bool(self.waveform_autoscale)
        if not isinstance(self.pitch_smoothing, bool):
            self.pitch_smoothing = bool(self.pitch_smoothing)

        self._notes = notes
        for note in notes:
            logger.warning("配置校正：%s", note)
        return notes

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典（不含内部说明字段）。"""
        return {key: value for key, value in asdict(self).items() if not key.startswith("_")}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        """从字典构造；未知键忽略，缺失键用默认值。"""
        known = {item.name for item in fields(cls) if not item.name.startswith("_")}
        payload = {key: value for key, value in data.items() if key in known}
        return cls(**payload)


def load_config(path: str | Path | None = None) -> AppConfig:
    """读取配置；文件缺失或损坏时返回默认配置（FR-6.2）。"""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        logger.info("未找到配置文件 %s，使用默认配置", target)
        config = AppConfig()
    else:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点必须是对象")
            config = AppConfig.from_dict(data)
            logger.info("已加载配置：%s", target)
        except Exception as exc:
            logger.warning("配置文件 %s 无法解析（%s），回退默认配置", target, exc)
            config = AppConfig()
    config.validate()
    return config


def save_config(config: AppConfig, path: str | Path | None = None) -> Path:
    """保存配置到 JSON 文件，返回实际写入路径。"""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("配置已保存：%s", target)
    return target
