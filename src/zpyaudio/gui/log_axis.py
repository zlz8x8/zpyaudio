"""十进制对数坐标轴。

为什么不用 pyqtgraph 自带的 ``setLogMode``
------------------------------------------
pyqtgraph 0.14 只对 :class:`~pyqtgraph.PlotDataItem` 应用 ``np.log10`` 映射
（``PlotDataItem.applyLogMapping``），而 :class:`~pyqtgraph.ScatterPlotItem`、
``InfiniteLine`` 等图元**不会**被映射。同一张图里混用这两类图元时，
一半数据要传线性值、一半要传 log10 值，极易出错（实测会把主频坐标取两次对数）。

这里的做法：**调用方统一传 log10 值**，坐标轴负责把刻度还原成十进制标签。
所有图元处于同一坐标系，行为一致、可测试。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg

__all__ = ["DecadeAxisItem", "format_decade"]


def format_decade(value: float) -> str:
    """把 log10 坐标值格式化成十进制标签（1 → "10"，2 → "100"，3.5 → "3.16k"）。"""
    try:
        real = float(np.power(10.0, float(value)))
    except (OverflowError, ValueError):
        return ""
    if not np.isfinite(real) or real <= 0.0:
        return ""
    if real >= 1_000_000.0:
        return f"{real / 1e6:.3g}M"
    if real >= 1000.0:
        return f"{real / 1000.0:.3g}k"
    if real >= 10.0:
        return f"{real:.0f}"
    if real >= 1.0:
        return f"{real:g}"
    return f"{real:.2g}"


class DecadeAxisItem(pg.AxisItem):
    """数据为 log10 值、标签为十进制数的坐标轴。"""

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - pyqtgraph 接口
        return [format_decade(value / scale if scale else value) for value in values]
