"""视图选项栏（规格书 7.2：波形区 / 频谱区 / 主频区控件）。

与工具条分离，因为这些选项**立即生效**（不需要重启采集），而采样率/FFT 等
分析参数在「开始」时生效。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from zpyaudio.app.config import PITCH_SECONDS, WAVEFORM_SECONDS, AppConfig

__all__ = ["ViewOptionsBar"]


class ViewOptionsBar(QWidget):
    """波形时间窗 / 频谱对数轴 / 主频时间窗 / 主频平滑。"""

    changed = Signal(dict)

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.waveform_combo = QComboBox()
        for seconds in WAVEFORM_SECONDS:
            self.waveform_combo.addItem(f"{seconds:g} s", float(seconds))
        self.pitch_combo = QComboBox()
        for seconds in PITCH_SECONDS:
            self.pitch_combo.addItem(f"{seconds:g} s", float(seconds))
        self.spectrum_log_check = QCheckBox("频谱对数轴")
        self.pitch_smooth_check = QCheckBox("主频中值滤波")
        self.waveform_autoscale_check = QCheckBox("波形自动量程")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 4)
        layout.addWidget(QLabel("波形时间窗"))
        layout.addWidget(self.waveform_combo)
        layout.addWidget(self.waveform_autoscale_check)
        layout.addSpacing(12)
        layout.addWidget(self.spectrum_log_check)
        layout.addSpacing(12)
        layout.addWidget(QLabel("主频时间窗"))
        layout.addWidget(self.pitch_combo)
        layout.addWidget(self.pitch_smooth_check)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.apply_config(config)
        self.waveform_combo.currentIndexChanged.connect(self._emit)
        self.pitch_combo.currentIndexChanged.connect(self._emit)
        self.spectrum_log_check.toggled.connect(self._emit)
        self.pitch_smooth_check.toggled.connect(self._emit)
        self.waveform_autoscale_check.toggled.connect(self._emit)

    # ---------------------------------------------------------------- 配置
    def apply_config(self, config: AppConfig) -> None:
        """按配置设置初值（不触发 changed 信号）。"""
        widgets = (
            self.waveform_combo,
            self.pitch_combo,
            self.spectrum_log_check,
            self.pitch_smooth_check,
            self.waveform_autoscale_check,
        )
        for widget in widgets:
            widget.blockSignals(True)
        _select(self.waveform_combo, config.waveform_seconds, 0.5)
        _select(self.pitch_combo, config.pitch_seconds, 10.0)
        self.spectrum_log_check.setChecked(bool(config.spectrum_log_x))
        self.pitch_smooth_check.setChecked(bool(config.pitch_smoothing))
        self.waveform_autoscale_check.setChecked(bool(config.waveform_autoscale))
        for widget in widgets:
            widget.blockSignals(False)

    def settings(self) -> dict:
        """当前视图选项。"""
        return {
            "waveform_seconds": self.waveform_combo.currentData(),
            "waveform_autoscale": self.waveform_autoscale_check.isChecked(),
            "spectrum_log_x": self.spectrum_log_check.isChecked(),
            "pitch_seconds": self.pitch_combo.currentData(),
            "pitch_smoothing": self.pitch_smooth_check.isChecked(),
        }

    def _emit(self) -> None:
        self.changed.emit(self.settings())


def _select(combo: QComboBox, value, fallback) -> None:
    index = combo.findData(value)
    if index < 0:
        index = combo.findData(fallback)
    if index >= 0:
        combo.setCurrentIndex(index)
