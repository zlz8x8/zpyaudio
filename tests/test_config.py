"""配置与持久化测试（规格书 FR-6）。"""

from __future__ import annotations

import json

import pytest

from zpyaudio.app.config import (
    BLOCK_SIZES,
    DEFAULT_CONFIG_PATH,
    SAMPLE_RATES,
    AppConfig,
    load_config,
    save_config,
)


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
    assert default_config.ffmpeg_dir.endswith("ffmpeg\\bin")
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


def test_catalogs_match_spec() -> None:
    assert SAMPLE_RATES == (16_000, 44_100, 48_000)
    assert BLOCK_SIZES == (512, 1024, 2048)
