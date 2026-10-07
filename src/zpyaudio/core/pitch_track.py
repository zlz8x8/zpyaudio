"""主频轨迹后处理（规格书 4.4「平滑」行）。

* **3 点中值滤波**：抑制单帧倍频跳变（如基频帧被误判为二次谐波）；
* **跳变检测**：相邻输出点的相对变化超过 12% 且**不是**整数倍/简单分数关系时，
  标记为可疑点，由界面用不同颜色提示；
* **静音断点**：无有效主频时复位滤波历史，避免跨静音段做中值。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from zpyaudio.core.frames import AnalysisSnapshot

__all__ = ["PitchPoint", "PitchTrack", "HARMONIC_RATIOS"]

#: 中值滤波窗口（规格书 4.4：3 点）
DEFAULT_WINDOW = 3

#: 跳变判定阈值：相对变化超过该比例才进一步判断是否为谐波关系
DEFAULT_JUMP_RATIO = 0.12

#: 谐波关系容差（相对）
HARMONIC_TOLERANCE = 0.05

#: 视为"整数倍/简单分数"的频率比
HARMONIC_RATIOS: tuple[float, ...] = (
    1 / 4,
    1 / 3,
    1 / 2,
    2 / 3,
    3 / 4,
    1.0,
    4 / 3,
    3 / 2,
    2.0,
    5 / 2,
    3.0,
    4.0,
)


@dataclass(frozen=True, slots=True)
class PitchPoint:
    """平滑后的主频点。"""

    t: float
    freq: float
    confidence: float
    suspect: bool = False
    raw_freq: float = 0.0


class PitchTrack:
    """把逐帧快照转换成可绘制的主频轨迹点。"""

    def __init__(
        self,
        *,
        window: int = DEFAULT_WINDOW,
        jump_ratio: float = DEFAULT_JUMP_RATIO,
        harmonic_tolerance: float = HARMONIC_TOLERANCE,
    ) -> None:
        if window < 1:
            raise ValueError(f"window 必须 ≥ 1，收到 {window}")
        self.window = int(window)
        self.jump_ratio = float(jump_ratio)
        self.harmonic_tolerance = float(harmonic_tolerance)
        self._history: deque[float] = deque(maxlen=self.window)
        self._last: PitchPoint | None = None
        self._last_freq = 0.0

    # ---------------------------------------------------------------- 属性
    @property
    def last(self) -> PitchPoint | None:
        """最近一次输出的点。"""
        return self._last

    @property
    def history(self) -> tuple[float, ...]:
        """当前滤波窗口内的原始主频。"""
        return tuple(self._history)

    # ---------------------------------------------------------------- 操作
    def reset(self) -> None:
        """复位滤波器（切换文件/停止后调用）。"""
        self._history.clear()
        self._last = None
        self._last_freq = 0.0

    def push(self, snapshot: AnalysisSnapshot) -> PitchPoint | None:
        """推入一帧快照，返回平滑后的点；无有效主频时返回 ``None``。"""
        if not snapshot.has_pitch:
            self._history.clear()
            self._last = None
            self._last_freq = 0.0
            return None

        raw = float(snapshot.dominant_freq)
        self._history.append(raw)
        smoothed = float(np.median(self._history))
        suspect = self._is_suspect(smoothed)
        point = PitchPoint(
            t=float(snapshot.t),
            freq=smoothed,
            confidence=float(snapshot.confidence),
            suspect=suspect,
            raw_freq=raw,
        )
        self._last = point
        self._last_freq = smoothed
        return point

    # ---------------------------------------------------------------- 内部
    def _is_suspect(self, freq: float) -> bool:
        """相对上一输出点跳变超过阈值且非谐波关系 → 可疑。"""
        previous = self._last_freq
        if previous <= 0.0 or freq <= 0.0:
            return False
        ratio = freq / previous
        if abs(ratio - 1.0) <= self.jump_ratio:
            return False
        return not self.is_harmonic_relation(ratio)

    def is_harmonic_relation(self, ratio: float) -> bool:
        """判断频率比是否近似为整数倍/简单分数（如 ×2、÷2、×3/2）。"""
        if ratio <= 0.0:
            return False
        return any(
            abs(ratio - candidate) <= self.harmonic_tolerance * candidate
            for candidate in HARMONIC_RATIOS
        )
