"""工具条控件（规格书 7.2 控件清单）。

只发出信号，不直接操作控制器；参数选择在「开始」时生效。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from zpyaudio.app.config import SAMPLE_RATES, AppConfig
from zpyaudio.core.analyzer import FFT_SIZES, WINDOW_FUNCTIONS
from zpyaudio.core.pitch import PITCH_METHODS

__all__ = ["ControlBar"]

logger = logging.getLogger(__name__)

#: 窗函数显示名
WINDOW_LABELS = {"hann": "Hann", "hamming": "Hamming", "blackman": "Blackman"}

#: 主频算法显示名（规格书 4.4 档位）
PITCH_LABELS = {
    "fft_parabolic": "P1 谱峰+插值（默认）",
    "hps": "P2 谐波积谱（M2）",
    "yin": "P3 自相关/YIN（M2）",
}


class ControlBar(QWidget):
    """设备 / 采样率 / FFT / 窗函数 / 主频算法 + 开始·录制·打开文件·暂停·停止。"""

    startRequested = Signal()
    recordRequested = Signal()
    openFileRequested = Signal()
    pauseToggled = Signal()
    stopRequested = Signal()
    settingsChanged = Signal(dict)

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(240)
        self.rate_combo = QComboBox()
        self.fft_combo = QComboBox()
        self.window_combo = QComboBox()
        self.pitch_combo = QComboBox()

        for rate in SAMPLE_RATES:
            self.rate_combo.addItem(f"{rate} Hz", rate)
        for size in FFT_SIZES:
            self.fft_combo.addItem(f"FFT {size}", size)
        for name in WINDOW_FUNCTIONS:
            self.window_combo.addItem(WINDOW_LABELS.get(name, name), name)
        for name in PITCH_METHODS:
            self.pitch_combo.addItem(PITCH_LABELS.get(name, name), name)

        self.start_button = QPushButton("开始")
        self.record_button = QPushButton("录制")
        self.open_button = QPushButton("打开文件")
        self.pause_button = QPushButton("暂停")
        self.stop_button = QPushButton("停止")
        for button in (self.pause_button, self.stop_button):
            button.setEnabled(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("输入设备"))
        layout.addWidget(self.device_combo)
        layout.addWidget(QLabel("采样率"))
        layout.addWidget(self.rate_combo)
        layout.addWidget(QLabel("FFT"))
        layout.addWidget(self.fft_combo)
        layout.addWidget(QLabel("窗函数"))
        layout.addWidget(self.window_combo)
        layout.addWidget(QLabel("主频算法"))
        layout.addWidget(self.pitch_combo)
        layout.addStretch(1)
        layout.addWidget(self.open_button)
        layout.addWidget(self.record_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.stop_button)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.start_button.clicked.connect(self.startRequested)
        self.record_button.clicked.connect(self.recordRequested)
        self.open_button.clicked.connect(self.openFileRequested)
        self.pause_button.clicked.connect(self.pauseToggled)
        self.stop_button.clicked.connect(self.stopRequested)
        for combo in (
            self.device_combo,
            self.rate_combo,
            self.fft_combo,
            self.window_combo,
            self.pitch_combo,
        ):
            combo.currentIndexChanged.connect(self._emit_settings)

        self.reload_devices()
        self.apply_config(config)

    # ---------------------------------------------------------------- 设备
    def reload_devices(self) -> None:
        """枚举输入设备填充下拉框（失败时只保留「系统默认」）。"""
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("系统默认设备", None)
        try:
            from zpyaudio.sources.device_source import list_input_devices

            for device in list_input_devices():
                label = f"[{device.index}] {device.name}" + ("（默认）" if device.is_default else "")
                self.device_combo.addItem(label, device.index)
        except Exception as exc:
            logger.warning("无法枚举输入设备：%s", exc)
        self.device_combo.blockSignals(False)

    # ---------------------------------------------------------------- 配置
    def apply_config(self, config: AppConfig) -> None:
        """按配置设置各下拉框初值。"""
        self._select_data(self.rate_combo, config.sample_rate, 48_000)
        self._select_data(self.fft_combo, config.fft_size, 4096)
        self._select_data(self.window_combo, config.window, "hann")
        self._select_data(self.pitch_combo, config.pitch_method, "fft_parabolic")
        self._select_data(self.device_combo, config.device, None)

    @staticmethod
    def _select_data(combo: QComboBox, value, fallback) -> None:
        index = combo.findData(value)
        if index < 0:
            index = combo.findData(fallback)
        if index >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def settings(self) -> dict:
        """当前控件选择。"""
        return {
            "device": self.device_combo.currentData(),
            "sample_rate": self.rate_combo.currentData(),
            "fft_size": self.fft_combo.currentData(),
            "window": self.window_combo.currentData(),
            "pitch_method": self.pitch_combo.currentData(),
        }

    def _emit_settings(self) -> None:
        self.settingsChanged.emit(self.settings())

    # ---------------------------------------------------------------- 状态
    def set_state(self, state) -> None:
        """按状态机置灰按钮与参数（规格书 5.3 / 7.2）。"""
        from zpyaudio.app.controller import AppState

        idle = state is AppState.IDLE
        paused = state is AppState.PAUSED
        self.start_button.setEnabled(idle)
        self.record_button.setEnabled(idle)
        self.open_button.setEnabled(idle)
        self.pause_button.setEnabled(not idle)
        self.pause_button.setText("继续" if paused else "暂停")
        self.stop_button.setEnabled(not idle)
        for combo in (
            self.device_combo,
            self.rate_combo,
            self.fft_combo,
            self.window_combo,
            self.pitch_combo,
        ):
            combo.setEnabled(idle)

    def set_running(self, running: bool) -> None:
        """兼容旧调用：仅按"是否运行"切换（测试与外部脚本用）。"""
        from zpyaudio.app.controller import AppState

        self.set_state(AppState.MONITORING if running else AppState.IDLE)
