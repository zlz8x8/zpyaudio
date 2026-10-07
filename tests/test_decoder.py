"""解码器测试（规格书 4.5）。

wav 用 ``soundfile`` 现场生成（不依赖 ffmpeg）；mp3 用 ffmpeg 生成，
ffmpeg 缺失时相关用例自动跳过。
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
import soundfile as sf

from zpyaudio.media import decoder as decoder_module
from zpyaudio.media.decoder import (
    FfmpegDecoder,
    ResampleRequired,
    SoundFileDecoder,
    find_ffmpeg,
    open_decoder,
    probe,
)

FS = 48_000


def _write_sawtooth(path, seconds: float = 2.0, sample_rate: int = FS, channels: int = 1):
    """写入锯齿波：样本值 = (n % 100)/100*2-1，便于校验定位精度。"""
    n = int(seconds * sample_rate)
    ramp = ((np.arange(n) % 100) / 100.0 * 2.0 - 1.0).astype(np.float32)
    data = np.repeat(ramp[:, None], channels, axis=1) if channels > 1 else ramp
    sf.write(str(path), data, sample_rate, subtype="PCM_16")
    return path


def _write_sine(path, *, frequency: float = 1000.0, seconds: float = 2.0, sample_rate: int = FS):
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    sf.write(
        str(path),
        (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32),
        sample_rate,
        subtype="PCM_16",
    )
    return path


@pytest.fixture
def wav_file(work_dir):
    return _write_sine(work_dir / "sine_1k.wav")


@pytest.fixture
def stereo_wav(work_dir):
    return _write_sawtooth(work_dir / "stereo.wav", seconds=1.0, channels=2)


@pytest.fixture
def mp3_file(work_dir):
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("未安装 ffmpeg，跳过 mp3 用例")
    target = work_dir / "sine_1k.mp3"
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration=2:sample_rate={FS}",
            "-ac",
            "1",
            "-b:a",
            "128k",
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:  # pragma: no cover - 环境异常
        pytest.skip(f"ffmpeg 生成 mp3 失败：{completed.stderr!r}")
    return target


# ---------------------------------------------------------------- 元信息
def test_probe_reports_wav_metadata(wav_file) -> None:
    info = probe(wav_file)

    assert info.duration == pytest.approx(2.0, abs=0.01)
    assert info.sample_rate == FS
    assert info.channels == 1
    assert info.name == "sine_1k.wav"


def test_probe_rejects_missing_file(work_dir) -> None:
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        probe(work_dir / "nope.wav")


def test_probe_reads_mp3_via_ffprobe(mp3_file) -> None:
    info = probe(mp3_file)

    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert info.sample_rate == FS
    assert info.channels == 1


def test_find_ffmpeg_locates_bundled_copy() -> None:
    tool = find_ffmpeg()

    assert tool is None or tool.is_file()


def test_find_ffmpeg_returns_none_when_absent(monkeypatch, work_dir) -> None:
    """模拟 ffmpeg 缺失（A8 故障注入）。"""
    monkeypatch.setattr(decoder_module, "DEFAULT_FFMPEG_DIRS", ())
    monkeypatch.setattr(decoder_module.shutil, "which", lambda name: None)

    assert find_ffmpeg() is None
    assert decoder_module.find_ffprobe() is None


def test_open_decoder_reports_missing_ffmpeg(monkeypatch, work_dir) -> None:
    """缺失 ffmpeg 时给出中文指引，而不是抛底层 OSError。"""
    target = work_dir / "fake.mp3"
    target.write_bytes(b"not really mp3")
    monkeypatch.setattr(decoder_module, "DEFAULT_FFMPEG_DIRS", ())
    monkeypatch.setattr(decoder_module.shutil, "which", lambda name: None)

    with pytest.raises(FileNotFoundError, match="未找到 ffmpeg"):
        FfmpegDecoder(target, sample_rate=FS)


# ---------------------------------------------------------------- soundfile 路径
def test_soundfile_decoder_reads_blocks(wav_file) -> None:
    decoder = SoundFileDecoder(wav_file)
    try:
        block = decoder.read(1024)
        assert block is not None and block.shape == (1024, 1)
        assert block.dtype == np.float32
        assert decoder.position_seconds == pytest.approx(1024 / FS)
    finally:
        decoder.close()


def test_soundfile_decoder_returns_none_at_eof(work_dir) -> None:
    decoder = SoundFileDecoder(_write_sine(work_dir / "short.wav", seconds=0.05))
    try:
        assert decoder.read(4096) is not None
        assert decoder.read(4096) is None
    finally:
        decoder.close()


def test_soundfile_decoder_seek_is_sample_accurate(work_dir) -> None:
    """锯齿波：定位后读到的样本值应与绝对位置一致（验收 FR-3.2 的基础）。"""
    path = _write_sawtooth(work_dir / "ramp.wav", seconds=2.0)
    decoder = SoundFileDecoder(path)
    try:
        decoder.seek(1.0)
        block = decoder.read(64)
        assert block is not None
        expected = (np.arange(int(1.0 * FS), int(1.0 * FS) + 64) % 100) / 100.0 * 2.0 - 1.0
        assert block[:, 0] == pytest.approx(expected, abs=0.02)
    finally:
        decoder.close()


def test_soundfile_decoder_requires_matching_rate(wav_file) -> None:
    with pytest.raises(ResampleRequired):
        SoundFileDecoder(wav_file, target_sample_rate=44_100)


def test_soundfile_decoder_keeps_channels(stereo_wav) -> None:
    decoder = SoundFileDecoder(stereo_wav)
    try:
        block = decoder.read(128)
        assert block is not None and block.shape == (128, 2)
    finally:
        decoder.close()


@pytest.mark.parametrize("frames", [0, -5, 1 << 23])
def test_decoders_reject_invalid_block_size(wav_file, frames: int) -> None:
    decoder = SoundFileDecoder(wav_file)
    try:
        with pytest.raises(ValueError, match="frames"):
            decoder.read(frames)
    finally:
        decoder.close()


# ---------------------------------------------------------------- ffmpeg 路径
def test_ffmpeg_decoder_decodes_full_mp3(mp3_file) -> None:
    decoder = FfmpegDecoder(mp3_file, sample_rate=FS, channels=1)
    try:
        total = 0
        while True:
            block = decoder.read(4096)
            if block is None:
                break
            assert block.shape[1] == 1
            total += block.shape[0]
        assert total == pytest.approx(2 * FS, rel=0.01)
    finally:
        decoder.close()


def test_ffmpeg_decoder_seek_restarts_stream(mp3_file) -> None:
    decoder = FfmpegDecoder(mp3_file, sample_rate=FS, channels=1)
    try:
        decoder.read(4800)
        assert decoder.position_seconds == pytest.approx(0.1, abs=0.01)

        decoder.seek(1.0)

        assert decoder.position_seconds == pytest.approx(1.0, abs=0.01)
        block = decoder.read(2048)
        assert block is not None
        assert decoder.position_seconds == pytest.approx(1.0 + 2048 / FS, abs=0.02)
    finally:
        decoder.close()


def test_ffmpeg_decoder_resamples_to_target_rate(wav_file) -> None:
    """目标采样率不一致时由 ffmpeg 重采样（避免逐块重采样的边界失真）。"""
    decoder = FfmpegDecoder(wav_file, sample_rate=16_000, channels=1)
    try:
        assert decoder.sample_rate == 16_000
        total = 0
        while True:
            block = decoder.read(2048)
            if block is None:
                break
            total += block.shape[0]
        assert total == pytest.approx(2 * 16_000, rel=0.01)
    finally:
        decoder.close()


# ---------------------------------------------------------------- 工厂选择
def test_open_decoder_prefers_soundfile_for_matching_wav(wav_file) -> None:
    decoder = open_decoder(wav_file, target_sample_rate=FS)

    assert isinstance(decoder, SoundFileDecoder)
    decoder.close()


def test_open_decoder_falls_back_to_ffmpeg_for_mp3(mp3_file) -> None:
    decoder = open_decoder(mp3_file, target_sample_rate=FS, channels=1)

    assert isinstance(decoder, FfmpegDecoder)
    decoder.close()


def test_open_decoder_uses_ffmpeg_when_rate_differs(wav_file) -> None:
    decoder = open_decoder(wav_file, target_sample_rate=22_050)

    assert isinstance(decoder, FfmpegDecoder)
    decoder.close()


def test_open_decoder_rejects_missing_file(work_dir) -> None:
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        open_decoder(work_dir / "nope.wav")
