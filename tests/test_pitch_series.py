"""主频时序收集与 CSV 导出测试（规格书 v0.5 变更 2 / 变更 3）。

判据要点：
* 收集在分析线程侧完成：静音帧只推进帧序号，不产生数据行；
* 平滑口径与界面一致（3 点中值 / 原始值）；CSV 两列都写；
* 界面只读最近 N 点，导出为录制全量点；超上限时截断并计数。
"""

from __future__ import annotations

import csv

import numpy as np
import pytest

from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.pitch_series import (
    CSV_HEADER,
    PitchSeries,
    SeriesPoint,
    write_pitch_csv,
)
from zpyaudio.core.notes import note_frequency

# ---------------------------------------------------------------- 工具
def _snapshot(
    freq: float,
    *,
    t: float = 0.0,
    confidence: float = 0.8,
    rms_db: float = -20.0,
    method: str = "fft_parabolic",
) -> AnalysisSnapshot:
    """构造一帧快照：``freq <= 0`` 表示静音（无有效主频）。"""
    valid = freq > 0.0
    return AnalysisSnapshot(
        t=t,
        spectrum_freqs=np.array([0.0, freq], dtype=np.float64),
        spectrum_db=np.array([-120.0, -6.0], dtype=np.float64),
        peak_freq=freq,
        dominant_freq=freq if valid else 0.0,
        confidence=confidence if valid else 0.0,
        rms_db=rms_db,
        method=method,
    )


# ---------------------------------------------------------------- 收集
def test_push_collects_valid_point() -> None:
    series = PitchSeries(smoothing=False)

    point = series.push(_snapshot(1000.0, t=0.5))

    assert point is not None
    assert (point.index, point.frame_index) == (0, 1)
    assert point.freq == pytest.approx(1000.0)
    assert point.smoothed == pytest.approx(1000.0)
    assert series.total == 1
    assert series.frames == 1
    assert series.recent() == [point]


def test_silent_frames_advance_counter_without_rows() -> None:
    series = PitchSeries(smoothing=False)

    assert series.push(_snapshot(0.0)) is None

    assert series.total == 0
    assert series.frames == 1
    assert series.recent() == []


def test_smoothing_uses_three_point_median() -> None:
    series = PitchSeries(smoothing=True)

    series.push(_snapshot(1000.0))
    series.push(_snapshot(1000.0))
    point = series.push(_snapshot(2100.0))

    assert point is not None
    assert point.freq == pytest.approx(2100.0)  # 原始值保留
    assert point.smoothed == pytest.approx(1000.0)  # 中值滤波结果


def test_set_smoothing_resets_filter_history() -> None:
    series = PitchSeries(smoothing=True)
    series.push(_snapshot(1000.0))
    series.push(_snapshot(1000.0))

    series.set_smoothing(False)
    point = series.push(_snapshot(2100.0, t=1.0))

    assert point is not None
    assert point.smoothed == pytest.approx(2100.0), "切换口径后不应混入旧历史"


def test_records_only_kept_when_recording() -> None:
    idle = PitchSeries()
    idle.reset(record_full=False)
    idle.push(_snapshot(1000.0))
    assert idle.records == []
    assert not idle.record_full

    recording = PitchSeries()
    recording.reset(record_full=True)
    recording.push(_snapshot(1000.0))
    assert len(recording.records) == 1
    assert recording.record_full


def test_reset_clears_history_and_counters() -> None:
    series = PitchSeries()
    series.reset(record_full=True)
    series.push(_snapshot(1000.0))

    series.reset(record_full=False)

    assert series.total == 0
    assert series.frames == 0
    assert series.recent() == []
    assert series.records == []
    assert series.dropped_records == 0


def test_points_after_returns_incremental_points() -> None:
    series = PitchSeries(smoothing=False)
    for index in range(5):
        series.push(_snapshot(1000.0 + index, t=index * 0.0625))

    tail = series.points_after(2)

    assert [item.index for item in tail] == [3, 4]
    assert series.points_after(4) == []


def test_recent_is_bounded_by_capacity() -> None:
    series = PitchSeries(smoothing=False, recent_capacity=3)
    for index in range(10):
        series.push(_snapshot(1000.0 + index, t=index * 0.0625))

    assert [item.index for item in series.recent()] == [7, 8, 9]
    assert [item.index for item in series.recent(2)] == [8, 9]
    assert series.total == 10, "total 统计全部有效点，不受显示容量影响"


def test_records_are_truncated_at_max_records() -> None:
    series = PitchSeries(smoothing=False, max_records=2)
    series.reset(record_full=True)

    for index in range(5):
        series.push(_snapshot(1000.0 + index))

    assert len(series.records) == 2
    assert series.dropped_records == 3


def test_note_of_follows_smoothing_switch() -> None:
    series = PitchSeries(smoothing=True)
    series.push(_snapshot(1000.0))
    series.push(_snapshot(1000.0))
    point = series.push(_snapshot(2000.0))
    assert point is not None

    assert PitchSeries.note_of(point, smoothing=True).name == "B5"  # 中值 1000 Hz
    assert PitchSeries.note_of(point, smoothing=False).name == "B6"  # 原始 2000 Hz


# ---------------------------------------------------------------- 导出
def test_write_pitch_csv_has_bom_header_and_rows(work_dir) -> None:
    series = PitchSeries(smoothing=False)
    series.reset(record_full=True)
    series.push(_snapshot(1000.0, t=1.0, confidence=0.9, rms_db=-12.0))
    series.push(_snapshot(1000.0, t=1.0625, confidence=0.8, rms_db=-13.0))
    target = work_dir / "tone.csv"

    rows = write_pitch_csv(target, series.records)

    assert rows == 2
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "Excel 直接打开需要 UTF-8 BOM"
    with target.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        data = list(reader)
    assert header == CSV_HEADER.split(",")
    assert len(data) == 2
    assert float(data[0][0]) == pytest.approx(1.0)
    assert float(data[0][1]) == pytest.approx(1000.0)
    assert data[0][3] == "B5"
    assert float(data[0][4]) == pytest.approx(21.3, abs=0.1)
    assert float(data[0][5]) == pytest.approx(0.9)
    assert float(data[0][6]) == pytest.approx(-12.0)
    assert data[0][7] == "fft_parabolic"


def test_write_pitch_csv_note_column_follows_smoothing(work_dir) -> None:
    series = PitchSeries(smoothing=True)
    series.reset(record_full=True)
    series.push(_snapshot(1000.0))
    series.push(_snapshot(1000.0))
    series.push(_snapshot(2000.0))
    assert len(series.records) == 3

    smoothed_path = work_dir / "smoothed.csv"
    raw_path = work_dir / "raw.csv"
    write_pitch_csv(smoothed_path, series.records, smoothing=True)
    write_pitch_csv(raw_path, series.records, smoothing=False)

    smoothed = list(csv.reader(smoothed_path.open(encoding="utf-8-sig")))[-1]
    raw = list(csv.reader(raw_path.open(encoding="utf-8-sig")))[-1]

    assert smoothed[3] == "B5", "平滑口径下音名取自 3 点中值"
    assert raw[3] == "B6", "关闭平滑时音名取自原始主频"
    assert smoothed[1] == raw[1] == "2000.00", "原始列与口径无关"
    assert smoothed[2] == "1000.00"


def test_write_pitch_csv_creates_parent_directory(work_dir) -> None:
    target = work_dir / "records" / "nested" / "tone.csv"

    rows = write_pitch_csv(target, [])

    assert rows == 0
    assert target.read_text(encoding="utf-8-sig").strip() == CSV_HEADER


def test_write_pitch_csv_uses_custom_reference(work_dir) -> None:
    series = PitchSeries(smoothing=False)
    series.reset(record_full=True)
    series.push(_snapshot(440.0))
    target = work_dir / "a4.csv"

    write_pitch_csv(target, series.records, a4=442.0)

    row = list(csv.reader(target.open(encoding="utf-8-sig")))[1]
    assert row[3] == "A4"
    assert float(row[4]) == pytest.approx(-7.9, abs=0.2)


def test_series_point_display_freq_switch() -> None:
    point = SeriesPoint(
        index=0,
        frame_index=1,
        t=0.0,
        freq=2100.0,
        smoothed=1000.0,
        confidence=0.9,
        rms_db=-10.0,
        method="fft_parabolic",
    )

    assert point.display_freq(smoothing=True) == pytest.approx(1000.0)
    assert point.display_freq(smoothing=False) == pytest.approx(2100.0)


def test_note_of_matches_reference_formula() -> None:
    series = PitchSeries(smoothing=False)
    point = series.push(_snapshot(note_frequency(69)))
    assert point is not None

    info = PitchSeries.note_of(point, smoothing=False)

    assert info is not None and info.name == "A4" and abs(info.cents) < 0.01


def test_invalid_constructor_arguments() -> None:
    with pytest.raises(ValueError, match="recent_capacity"):
        PitchSeries(recent_capacity=0)
    with pytest.raises(ValueError, match="max_records"):
        PitchSeries(max_records=0)
    with pytest.raises(ValueError, match="a4"):
        PitchSeries().set_a4(0.0)


# ---------------------------------------------------------------- 应用点（防回归）
def test_dominant_freq_is_zero_for_silence_snapshot() -> None:
    """静音快照必须没有有效主频（A6 前置条件，供上面的静音用例依赖）。"""
    assert not _snapshot(0.0).has_pitch
