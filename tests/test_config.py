"""配置与持久化测试（规格书 FR-6）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zpyaudio.app import config as config_module
from zpyaudio.app.config import (
    BLOCK_SIZES,
    DEFAULT_CONFIG_PATH,
    SAMPLE_RATES,
    AppConfig,
    load_config,
    save_config,
)
from zpyaudio.media import decoder as decoder_module


def test_defaults_match_spec(default_config: AppConfig) -> None:
    """默认值取自规格书 4.2 / 4.3 / 4.4。"""
    assert default_config.sample_rate == 48_000
    assert default_config.block_size == 1024
    assert default_config.fft_size == 4096
    assert default_config.window == "hann"
    assert default_config.pitch_method == "fft_parabolic"
    assert default_config.fmin == 50.0
    assert default_config.fmax == 5000.0
    assert default_config.rms_gate_db == -60.0
    assert default_config.waveform_seconds == 0.5
    assert default_config.waveform_autoscale is False
    assert default_config.pitch_seconds == 10.0
    assert default_config.pitch_smoothing is True
    assert default_config.record_subtype == "PCM_16"
    # FR-6.4：ffmpeg 路径默认留空 = 每次启动自动检测
    assert default_config.ffmpeg_dir == ""
    assert default_config.ffmpeg_autodetect is True
    assert default_config.ring_seconds == 5.0
    assert DEFAULT_CONFIG_PATH.name == "config.json"


def test_load_uses_defaults_when_file_missing(work_dir) -> None:
    config = load_config(work_dir / "nope.json")

    assert config.fft_size == 4096
    assert config.device is None


def test_load_falls_back_on_corrupt_file(work_dir) -> None:
    """FR-6.2：配置损坏时回退默认值且程序正常启动。"""
    broken = work_dir / "config.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    config = load_config(broken)

    assert config.sample_rate == 48_000
    assert config.fft_size == 4096


def test_load_falls_back_when_root_is_not_object(work_dir) -> None:
    bad = work_dir / "config.json"
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert load_config(bad).sample_rate == 48_000


def test_roundtrip_save_and_load(work_dir) -> None:
    config = AppConfig(
        device=2,
        fft_size=1024,
        window="hamming",
        waveform_seconds=1.0,
        waveform_autoscale=True,
        pitch_seconds=30.0,
        record_subtype="PCM_24",
    )
    path = work_dir / "sub" / "config.json"

    saved = save_config(config, path)
    loaded = load_config(saved)

    assert saved.exists()
    assert loaded.device == 2
    assert loaded.fft_size == 1024
    assert loaded.window == "hamming"
    assert loaded.waveform_seconds == 1.0
    assert loaded.waveform_autoscale is True
    assert loaded.pitch_seconds == 30.0
    assert loaded.record_subtype == "PCM_24"


def test_from_dict_ignores_unknown_keys() -> None:
    config = AppConfig.from_dict({"fft_size": 2048, "不存在的键": 1})

    assert config.fft_size == 2048


def test_to_dict_excludes_private_fields() -> None:
    payload = AppConfig().to_dict()

    assert "fft_size" in payload
    assert all(not key.startswith("_") for key in payload)


@pytest.mark.parametrize(
    ("field", "bad", "expected"),
    [
        ("sample_rate", 12_345, 48_000),
        ("block_size", 999, 1024),
        ("fft_size", 3000, 4096),
        ("window", "kaiser", "hann"),
        ("pitch_method", "magic", "fft_parabolic"),
        ("zero_pad", 7, 1),
        ("log_level", "VERBOSE", "INFO"),
        ("waveform_seconds", 99.0, 0.5),
        ("pitch_seconds", 7.0, 10.0),
        ("record_subtype", "PCM_8", "PCM_16"),
        ("ring_seconds", 0.1, 5.0),
        ("rms_gate_db", 12.0, -60.0),
        ("device", "abc", None),
        ("ffmpeg_dir", 123, ""),
    ],
)
def test_validate_corrects_invalid_values(field: str, bad, expected) -> None:
    config = AppConfig(**{field: bad})

    notes = config.validate()

    assert getattr(config, field) == expected
    assert notes, "非法取值应产生校正说明"


def test_validate_fixes_inverted_frequency_range() -> None:
    config = AppConfig(fmin=8000.0, fmax=100.0)

    config.validate()

    assert (config.fmin, config.fmax) == (50.0, 5000.0)


def test_validate_keeps_valid_values() -> None:
    config = AppConfig(sample_rate=44_100, fft_size=2048, window="blackman")

    assert config.validate() == []
    assert config.sample_rate == 44_100


def test_analyzer_config_mapping(default_config: AppConfig) -> None:
    default_config.fft_size = 2048
    default_config.window = "hamming"

    analyzer_config = default_config.analyzer_config()

    assert analyzer_config.sample_rate == default_config.sample_rate
    assert analyzer_config.window_size == 2048
    assert analyzer_config.window == "hamming"


def test_paths_resolve_against_project_root(default_config: AppConfig) -> None:
    assert default_config.records_path.name == "records"
    assert default_config.records_path.is_absolute()
    assert default_config.logs_path.is_absolute()


# ---------------------------------------------------------------- 打包后的根目录（NFR-7）
def test_project_root_is_repo_root_when_not_frozen() -> None:
    """源码运行时根目录 = 仓库根（默认配置写在仓库根的 config.json）。"""
    expected = Path(__file__).resolve().parents[1]

    assert config_module.resolve_project_root() == expected
    assert config_module.PROJECT_ROOT == expected


def test_project_root_follows_executable_when_frozen(monkeypatch, work_dir) -> None:
    """打包后根目录 = 可执行文件所在目录，便携版把 config/logs/records 放在 exe 旁边。"""
    portable = work_dir / "zpyaudio-0.1.0-win.amd64-portable"
    portable.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_module.sys, "executable", str(portable / "zpyaudio.exe"))

    assert config_module.resolve_project_root() == portable

    # 根目录换了以后，相对路径（records/logs）随之落在 exe 旁边
    monkeypatch.setattr(config_module, "PROJECT_ROOT", portable)
    config = AppConfig(records_dir="records", logs_dir="logs")
    assert config.records_path == portable / "records"
    assert config.logs_path == portable / "logs"


def test_catalogs_match_spec() -> None:
    assert SAMPLE_RATES == (16_000, 44_100, 48_000)
    assert BLOCK_SIZES == (512, 1024, 2048)


# ---------------------------------------------------------------- ffmpeg（FR-6.4）
def _stub_ffmpeg(path):
    """在 ``path`` 造一个"像可执行文件"的占位文件（不运行，只做定位判断）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")
    return path


def test_validate_normalises_ffmpeg_fields() -> None:
    config = AppConfig(ffmpeg_dir="  ", ffmpeg_autodetect=0)

    config.validate()

    assert config.ffmpeg_dir == ""
    assert config.ffmpeg_autodetect is False


def test_validate_keeps_ffmpeg_path() -> None:
    config = AppConfig(ffmpeg_dir=r" D:\tools\ffmpeg\bin ")

    notes = config.validate()

    assert config.ffmpeg_dir == r"D:\tools\ffmpeg\bin"
    assert notes == []


def test_resolve_ffmpeg_prefers_usable_user_path(monkeypatch, work_dir) -> None:
    """用户指定的路径可用时直接采用，**不做**自动检测覆盖（FR-6.4）。"""
    tool = _stub_ffmpeg(work_dir / "bin" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=str(tool))
    calls: list[object] = []

    def fake_detect(extra_dir=None):  # noqa: ANN001, ANN202
        calls.append(extra_dir)
        return None

    monkeypatch.setattr(decoder_module, "find_ffmpeg", fake_detect)

    assert config.resolve_ffmpeg() == tool
    assert config.ffmpeg_dir == str(tool)
    assert calls == [], "用户路径可用时不应再触发自动检测"


def test_resolve_ffmpeg_accepts_directory(monkeypatch, work_dir) -> None:
    tool = _stub_ffmpeg(work_dir / "bin" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=str(tool.parent))

    assert config.resolve_ffmpeg() == tool


def test_resolve_ffmpeg_autodetects_and_writes_back(monkeypatch, work_dir) -> None:
    """配置为空时自动检测，并把结果写回配置，用户下次可见/可改（FR-6.4）。"""
    detected = _stub_ffmpeg(work_dir / "detected" / "ffmpeg.exe")
    config = AppConfig()
    monkeypatch.setattr(decoder_module, "find_ffmpeg", lambda extra=None: detected)

    assert config.resolve_ffmpeg() == detected
    assert config.ffmpeg_dir == str(detected)


def test_resolve_ffmpeg_returns_none_when_not_found(monkeypatch) -> None:
    config = AppConfig()
    monkeypatch.setattr(decoder_module, "find_ffmpeg", lambda extra=None: None)

    assert config.resolve_ffmpeg() is None
    assert config.ffmpeg_dir == ""


def test_resolve_ffmpeg_keeps_user_path_when_autodetect_disabled(monkeypatch, work_dir) -> None:
    """关闭自动检测后不覆盖用户配置（即使路径当前不可用）。"""
    configured = str(work_dir / "missing" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=configured, ffmpeg_autodetect=False)
    monkeypatch.setattr(
        decoder_module, "find_ffmpeg", lambda extra=None: pytest.fail("不应触发自动检测")
    )

    assert config.resolve_ffmpeg() is None
    assert config.ffmpeg_dir == configured


def test_resolve_ffmpeg_recovers_when_configured_path_breaks(monkeypatch, work_dir) -> None:
    """配置的路径失效（如 ffmpeg 被移动）时回落到自动检测并改写配置。"""
    detected = _stub_ffmpeg(work_dir / "other" / "ffmpeg.exe")
    config = AppConfig(ffmpeg_dir=str(work_dir / "gone" / "ffmpeg.exe"))
    monkeypatch.setattr(decoder_module, "find_ffmpeg", lambda extra=None: detected)

    assert config.resolve_ffmpeg() == detected
    assert config.ffmpeg_dir == str(detected)


def test_ffmpeg_settings_roundtrip(work_dir) -> None:
    """ffmpeg 设置可持久化（重启后仍是用户指定的路径）。"""
    config = AppConfig(ffmpeg_dir=r"D:\tools\ffmpeg\bin", ffmpeg_autodetect=False)

    loaded = load_config(save_config(config, work_dir / "config.json"))

    assert loaded.ffmpeg_dir == r"D:\tools\ffmpeg\bin"
    assert loaded.ffmpeg_autodetect is False
