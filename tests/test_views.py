"""视图组件测试（规格书 7.1/7.2）。

重点：pyqtgraph 的对数模式要求**数据本身取 log10**（轴标签自动还原为 10^v），
这里用「数据必须落在视图范围内」来守住这个易错点。
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from zpyaudio.core.analyzer import Analyzer, AnalyzerConfig
from zpyaudio.core.ringbuffer import RingBuffer
from zpyaudio.gui.pianoroll_view import PianoRollView
from zpyaudio.gui.pitch_view import PitchView
from zpyaudio.gui.spectrum_view import SpectrumView
from zpyaudio.gui.waveform_view import WaveformView

pytest.importorskip("pytestqt")

FS = 48_000
N = 4096


def _snapshot(freq: float = 1000.0):
    analyzer = Analyzer(AnalyzerConfig(sample_rate=FS, window_size=N))
    t = np.arange(N, dtype=np.float64) / FS
    signal = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return analyzer.analyze(signal, t=0.0)


def _make_view(kind: str):
    if kind == "waveform":
        return WaveformView(FS, 0.5)
    if kind == "spectrum":
        return SpectrumView(FS)
    if kind == "pianoroll":
        return PianoRollView()
    return PitchView(10.0)


@pytest.mark.parametrize("kind", ["waveform", "spectrum", "pitch", "pianoroll"])
def test_view_members_are_not_shadowed_by_plotitem(qtbot, kind: str) -> None:
    """回归：``pg.PlotWidget.__getattr__`` 会转发 ``PlotItem`` 的同名成员，
    子类同名方法会被静默遮蔽（``PitchView.clear`` 曾实际调用到 ``PlotItem.clear``）。
    """
    view = _make_view(kind)
    qtbot.addWidget(view)
    shadowed: list[str] = []
    for name, member in vars(type(view)).items():
        # 只看本类定义的函数/属性（Shiboken 会注入 staticMetaObject 等非函数成员）
        if not (inspect.isfunction(member) or isinstance(member, property)):
            continue
        if not hasattr(view.plotItem, name):
            continue
        resolved = getattr(view, name)
        if isinstance(member, property):
            if getattr(resolved, "__self__", view) is not view:
                shadowed.append(name)
        elif getattr(resolved, "__func__", None) is not member:
            shadowed.append(name)

    assert not shadowed, f"{kind} 视图存在被 PlotItem 遮蔽的成员：{shadowed}"


# ---------------------------------------------------------------- 波形视图
def test_waveform_view_maps_latest_samples_to_right_edge(qtbot) -> None:
    view = WaveformView(FS, 0.1)
    qtbot.addWidget(view)
    samples = np.linspace(-1.0, 1.0, 4800, dtype=np.float32)

    view.update_samples(samples)

    xs, ys = view._curve.getData()
    assert ys[-1] == pytest.approx(1.0, abs=1e-6)
    assert xs[-1] == pytest.approx(0.0)
    assert xs[0] >= -view.seconds - 1e-6


def test_waveform_view_reads_from_ring_buffer(qtbot) -> None:
    view = WaveformView(FS, 0.05)
    qtbot.addWidget(view)
    ring = RingBuffer(capacity=FS, channels=1)

    assert view.update_from_ring(ring) is False  # 数据不足
    ring.write(np.ones((2400, 1), dtype=np.float32))
    assert view.update_from_ring(ring) is True
    assert len(view._curve.getData()[0]) == 2400


def test_decade_axis_formats_labels_readably() -> None:
    """对数轴刻度要还原成人类可读的十进制（避免出现 3.16228k 这种标签）。"""
    from zpyaudio.gui.log_axis import format_decade

    assert format_decade(1.0) == "10"
    assert format_decade(2.0) == "100"
    assert format_decade(3.0) == "1k"
    assert format_decade(3.5) == "3.16k"
    assert format_decade(4.0) == "10k"
    assert format_decade(6.0) == "1M"
    assert format_decade(0.0) == "1"
    assert format_decade(float("inf")) == ""


def test_waveform_view_autoscale_toggle(qtbot) -> None:
    """规格书 7.2：波形区可切换自动量程。"""
    view = WaveformView(FS, 0.1)
    qtbot.addWidget(view)
    assert view.autoscale is False

    view.set_autoscale(True)

    assert view.autoscale is True
    assert view.getViewBox().autoRangeEnabled()[1]

    view.set_autoscale(False)

    assert not view.getViewBox().autoRangeEnabled()[1]
    low, high = view.getViewBox().viewRange()[1]
    assert (low, high) == pytest.approx((-1.05, 1.05), abs=0.01)


def test_waveform_view_rejects_non_positive_window(qtbot) -> None:
    view = WaveformView(FS, 0.5)
    qtbot.addWidget(view)

    with pytest.raises(ValueError, match="seconds"):
        view.set_seconds(0.0)


# ---------------------------------------------------------------- 频谱视图
def test_spectrum_view_linear_mode_uses_hz(qtbot) -> None:
    view = SpectrumView(FS, log_x=False)
    qtbot.addWidget(view)

    view.update_snapshot(_snapshot())

    xs, _ = view._curve.getData()
    assert xs.max() == pytest.approx(FS / 2, rel=1e-6)
    assert "峰值" in view.title_text()


def test_spectrum_view_log_mode_uses_log10_and_lands_in_range(qtbot) -> None:
    """回归：对数模式下必须传 log10，否则曲线会跑出视图范围。"""
    view = SpectrumView(FS, log_x=True)
    qtbot.addWidget(view)

    view.update_snapshot(_snapshot())

    xs, _ = view._curve.getData()
    x_min, x_max = view.getViewBox().viewRange()[0]
    assert x_max == pytest.approx(np.log10(FS / 2), rel=0.05)
    assert x_min == pytest.approx(np.log10(20.0), rel=0.05)
    # 视图范围的单位必须是 log10：若传了原始 Hz，绝大多数点会跑到范围外
    inside = np.mean((xs >= x_min) & (xs <= x_max))
    assert inside > 0.95


def test_spectrum_view_peak_marker_follows_log_mode(qtbot) -> None:
    view = SpectrumView(FS, log_x=False)
    qtbot.addWidget(view)
    view.update_snapshot(_snapshot())
    assert view._peak_line.value() == pytest.approx(1000.0, abs=12.0)

    view.set_log_x(True)
    view.update_snapshot(_snapshot())

    assert view._peak_line.value() == pytest.approx(np.log10(1000.0), abs=0.2)


# ---------------------------------------------------------------- 主频视图
def test_pitch_view_plots_log10_frequencies_in_range(qtbot) -> None:
    view = PitchView(10.0)
    qtbot.addWidget(view)

    view.update_snapshot(_snapshot(1000.0))

    xs, ys = view._curve.getData()
    view_min, view_max = view.getViewBox().viewRange()[1]
    assert ys[0] == pytest.approx(np.log10(1000.0), abs=0.01)
    assert xs[0] <= 0.0
    assert view_min - 1e-9 <= ys[0] <= view_max + 1e-9


def test_pitch_view_inserts_gap_on_silence(qtbot) -> None:
    view = PitchView(10.0)
    qtbot.addWidget(view)

    view.update_snapshot(_snapshot(1000.0))
    view.update_snapshot(_snapshot(0.0))

    ys = view._curve.getData()[1]
    assert np.isnan(ys[-1]), "静音帧应断线"
    assert view.point_count == 2


def test_pitch_view_smoothing_toggle_switches_between_raw_and_median(qtbot) -> None:
    view = PitchView(10.0, smoothing=True)
    qtbot.addWidget(view)
    view.update_snapshot(_snapshot(1000.0))
    view.update_snapshot(_snapshot(1000.0))
    view.update_snapshot(_snapshot(2100.0))

    # 3 点中值滤波：(1000, 1000, 2100) → 1000
    assert view._curve.getData()[1][-1] == pytest.approx(np.log10(1000.0), abs=0.02)

    view.set_smoothing(False)
    view.update_snapshot(_snapshot(2100.0))

    # 关闭平滑后直接使用原始主频
    assert view._curve.getData()[1][-1] == pytest.approx(np.log10(2100.0), abs=0.02)


def test_pitch_view_window_change_keeps_recent_points(qtbot) -> None:
    view = PitchView(10.0, hop_seconds=0.0625)
    qtbot.addWidget(view)
    for index in range(100):
        view.update_snapshot(_snapshot(1000.0 + index))
    assert view.point_count == 100

    view.set_seconds(5.0)  # 容量 = 5/0.0625 + 2 = 82

    assert view.point_count == 82
    assert view.seconds == 5.0


def test_pitch_view_clear_resets_state(qtbot) -> None:
    view = PitchView(10.0)
    qtbot.addWidget(view)
    view.update_snapshot(_snapshot(1000.0))

    view.reset_track()

    assert view.point_count == 0
    assert view.last_freq == 0.0
    data = view._curve.getData()[0]
    assert data is None or len(data) == 0


def test_pitch_view_rejects_non_positive_window(qtbot) -> None:
    view = PitchView(10.0)
    qtbot.addWidget(view)

    with pytest.raises(ValueError, match="seconds"):
        view.set_seconds(-1.0)


def test_pitch_view_marks_suspect_jumps(qtbot) -> None:
    view = PitchView(10.0, smoothing=False)
    qtbot.addWidget(view)
    for _ in range(3):
        view.update_snapshot(_snapshot(1000.0))

    view.update_snapshot(_snapshot(1370.0))

    suspects = view._suspect_item.getData()
    # 关闭平滑时不标可疑点，数据仍在主曲线上
    assert len(suspects[0]) == 0
    assert view._curve.getData()[1][-1] == pytest.approx(np.log10(1370.0), abs=0.02)
