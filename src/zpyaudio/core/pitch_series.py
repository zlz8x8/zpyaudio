"""主频时序数据收集与 CSV 导出（规格书 v0.5 变更 2 / 变更 3）。

设计要点
--------
* **收集发生在分析线程**：由 :meth:`AudioController._publish` 调用
  :meth:`PitchSeries.push`，单写、无锁、无文件 IO，不阻塞 1/16 s 节拍；
* **显示与导出解耦**：界面只读 :meth:`PitchSeries.points_after`（最近
  ``recent_capacity`` 点），CSV 导出本次录制的全量点。GUI 定时器丢帧（例如被
  模态对话框阻塞）只影响显示，不影响导出内容的完整性；
* **平滑口径与界面一致**：开启 3 点中值滤波时用平滑值（``smoothed``）生成音名，
  关闭时用原始值（``freq``）；CSV 两列都写，便于复算；
* **静音帧不入列**：静音/无有效主频的帧只推进 :attr:`PitchSeries.frames`
  （用于界面断线），不产生数据行。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from zpyaudio.core.frames import AnalysisSnapshot
from zpyaudio.core.notes import A4_FREQ, freq_to_note
from zpyaudio.core.pitch_track import PitchTrack

__all__ = [
    "SeriesPoint",
    "PitchSeries",
    "CSV_HEADER",
    "DEFAULT_RECENT_CAPACITY",
    "DEFAULT_MAX_RECORDS",
    "write_pitch_csv",
]

logger = logging.getLogger(__name__)

#: 界面数据区保留的最近帧数（规格书 v0.5 变更 2）
DEFAULT_RECENT_CAPACITY = 2000

#: 单次录制的最大导出行数（≈ 3.5 h @16 Hz），超出后截断并告警（防止无界增长）
DEFAULT_MAX_RECORDS = 200_000

#: CSV 列头（ASCII，便于 pandas / Excel 直接解析）
CSV_HEADER = "t_s,freq_hz,freq_smoothed_hz,note,cents,confidence,rms_dbfs,method"


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """一帧有效主频的时序数据点。"""

    index: int
    """有效点序号（从 0 起，单调递增；静音帧不占号）。"""

    frame_index: int
    """分析帧序号（从 1 起，含静音帧），用于界面在静音处断线。"""

    t: float
    """流相对时刻（秒）。"""

    freq: float
    """原始主频（Hz）。"""

    smoothed: float
    """3 点中值滤波后的主频（Hz）。"""

    confidence: float
    """置信度 0..1。"""

    rms_db: float
    """该帧 RMS 电平（dBFS）。"""

    method: str
    """主频算法标识（如 ``fft_parabolic`` / ``hps`` / ``yin``）。"""

    suspect: bool = False
    """是否被标记为可疑跳变（仅平滑开启时有意义）。"""

    def display_freq(self, *, smoothing: bool) -> float:
        """界面/音名使用的频率：平滑开启取中值，关闭取原始值。"""
        return float(self.smoothed if smoothing else self.freq)


class PitchSeries:
    """按帧累积主频时序：最近点供界面显示，全量点供录制结束后导出。"""

    def __init__(
        self,
        *,
        smoothing: bool = True,
        recent_capacity: int = DEFAULT_RECENT_CAPACITY,
        max_records: int = DEFAULT_MAX_RECORDS,
        a4: float = A4_FREQ,
    ) -> None:
        if recent_capacity <= 0:
            raise ValueError(f"recent_capacity 必须为正，收到 {recent_capacity}")
        if max_records <= 0:
            raise ValueError(f"max_records 必须为正，收到 {max_records}")
        self._smoothing = bool(smoothing)
        self._a4 = float(a4)
        self._max_records = int(max_records)
        self._track = PitchTrack()
        self._recent: deque[SeriesPoint] = deque(maxlen=int(recent_capacity))
        self._records: list[SeriesPoint] = []
        self._total = 0
        self._frames = 0
        self._record_full = False
        self._dropped_records = 0
        self._truncation_logged = False

    # ---------------------------------------------------------------- 属性
    @property
    def smoothing(self) -> bool:
        """是否使用 3 点中值滤波口径。"""
        return self._smoothing

    @property
    def a4(self) -> float:
        """音名参考频率（Hz）。"""
        return self._a4

    @property
    def total(self) -> int:
        """已收集的有效主频点数（含已被 recent 淘汰的点）。"""
        return self._total

    @property
    def frames(self) -> int:
        """已推入的分析帧数（含静音帧）。"""
        return self._frames

    @property
    def record_full(self) -> bool:
        """是否正在保留全量点（录制中为 ``True``）。"""
        return self._record_full

    @property
    def dropped_records(self) -> int:
        """因超过 ``max_records`` 而未保留的点数。"""
        return self._dropped_records

    @property
    def records(self) -> list[SeriesPoint]:
        """本次录制保留的全量点（副本）。"""
        return list(self._records)

    # ---------------------------------------------------------------- 操作
    def reset(self, *, record_full: bool = False) -> None:
        """开始新一次采集：清空历史、复位滤波与计数。"""
        self._track.reset()
        self._recent.clear()
        self._records.clear()
        self._total = 0
        self._frames = 0
        self._record_full = bool(record_full)
        self._dropped_records = 0
        self._truncation_logged = False

    def set_smoothing(self, enabled: bool) -> None:
        """切换平滑口径（会复位滤波历史，避免新旧口径混在同一序列里）。"""
        enabled = bool(enabled)
        if enabled == self._smoothing:
            return
        self._smoothing = enabled
        self._track.reset()

    def set_a4(self, a4: float) -> None:
        """设置音名参考频率（Hz）。"""
        if a4 <= 0.0:
            raise ValueError(f"a4 必须为正，收到 {a4}")
        self._a4 = float(a4)

    def push(self, snapshot: AnalysisSnapshot) -> SeriesPoint | None:
        """推入一帧快照；无有效主频时返回 ``None``（静音帧不入列）。"""
        self._frames += 1
        point = self._track.push(snapshot)
        if point is None:
            return None
        item = SeriesPoint(
            index=self._total,
            frame_index=self._frames,
            t=float(point.t),
            freq=float(point.raw_freq),
            smoothed=float(point.freq),
            confidence=float(point.confidence),
            rms_db=float(snapshot.rms_db),
            method=str(snapshot.method),
            suspect=bool(point.suspect),
        )
        self._total += 1
        self._recent.append(item)
        if self._record_full:
            self._append_record(item)
        return item

    def recent(self, count: int | None = None) -> list[SeriesPoint]:
        """最近 ``count`` 个点（``None`` = 全部保留的最近点），按时间升序。"""
        if count is None:
            return list(self._recent)
        if count <= 0:
            return []
        if count >= len(self._recent):
            return list(self._recent)
        items = list(self._recent)
        return items[-int(count) :]

    def points_after(self, index: int) -> list[SeriesPoint]:
        """返回序号大于 ``index`` 的点（界面增量刷新用）。"""
        return [item for item in self._recent if item.index > int(index)]

    @staticmethod
    def note_of(point: SeriesPoint, *, smoothing: bool = True, a4: float = A4_FREQ):
        """点 → 音名信息（口径与 :meth:`SeriesPoint.display_freq` 一致）。"""
        return freq_to_note(point.display_freq(smoothing=smoothing), a4=a4)

    # ---------------------------------------------------------------- 内部
    def _append_record(self, item: SeriesPoint) -> None:
        if len(self._records) < self._max_records:
            self._records.append(item)
            return
        self._dropped_records += 1
        if not self._truncation_logged:
            self._truncation_logged = True
            logger.warning(
                "主频时序已达上限 %d 行，后续点不再保留（导出文件将被截断）",
                self._max_records,
            )


def write_pitch_csv(
    path: str | Path,
    points: Iterable[SeriesPoint],
    *,
    smoothing: bool = True,
    a4: float = A4_FREQ,
) -> int:
    """把主频时序写成 CSV（UTF-8 with BOM，便于 Excel 直接打开），返回行数。

    列：``t_s, freq_hz, freq_smoothed_hz, note, cents, confidence, rms_dbfs, method``；
    其中 ``note`` / ``cents`` 按 ``smoothing`` 指定的口径生成（与界面显示一致）。
    父目录不存在时自动创建。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write(CSV_HEADER + "\n")
        for item in points:
            freq = item.display_freq(smoothing=smoothing)
            info = freq_to_note(freq, a4=a4)
            note = info.name if info is not None else ""
            cents = f"{info.cents:.1f}" if info is not None else ""
            handle.write(
                f"{item.t:.4f},{item.freq:.2f},{item.smoothed:.2f},{note},{cents},"
                f"{item.confidence:.3f},{item.rms_db:.2f},{item.method}\n"
            )
            rows += 1
    return rows
