"""主窗口（规格书 7.1 布局，M1 范围：波形 + 频谱 + 日志 + 控制条）。

GUI 线程只做绘图：波形从环形缓冲直读，频谱从控制器快照队列取最新帧，
主频时序图与右侧数据区共用分析线程收集的 :class:`PitchSeries` 序列；
FFT 与文件 IO 都在后台线程（规格书 5.2）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from zpyaudio import __version__
from zpyaudio.app.config import AppConfig, save_config
from zpyaudio.app.controller import HOP_SECONDS, AppState, AudioController
from zpyaudio.core.notes import A4_FREQ, LOW_FREQ_NOTE_WARN_HZ, format_note, freq_to_note
from zpyaudio.gui.controls import ControlBar
from zpyaudio.gui.log_view import LogView
from zpyaudio.gui.pianoroll_view import PianoRollView
from zpyaudio.gui.pitch_data_view import PitchDataView
from zpyaudio.gui.pitch_view import PitchView
from zpyaudio.gui.progress_panel import ProgressPanel
from zpyaudio.gui.spectrum_view import SpectrumView
from zpyaudio.gui.view_options import ViewOptionsBar
from zpyaudio.gui.waveform_view import WaveformView

__all__ = ["MainWindow"]

logger = logging.getLogger(__name__)

#: 波形重绘节拍（30 Hz，规格书 4.3）
WAVEFORM_INTERVAL_MS = 33

#: 频谱/主频刷新节拍（≈1/16 s，规格书 4.3）
SNAPSHOT_INTERVAL_MS = 63

#: 文件对话框过滤器（规格书 7.2）
AUDIO_FILE_FILTER = "音频文件 (*.wav *.mp3 *.flac *.ogg *.m4a *.aac);;MIDI (*.mid *.midi);;全部文件 (*)"

#: MIDI 后缀（M5 里程碑实现）
MIDI_SUFFIXES = frozenset({".mid", ".midi"})


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        source_factory=None,
        player_factory=None,
        persist_config: bool = True,
        quiet_errors: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._persist_config = persist_config
        #: 为 True 时不弹错误对话框（自动化测试用），只写日志
        self.quiet_errors = quiet_errors
        self.setWindowTitle("zpyaudio 音频分析")
        self.resize(1200, 780)

        self.controller = AudioController(
            config, source_factory=source_factory, player_factory=player_factory
        )
        self.controller.add_state_listener(self._on_state_changed)
        #: 数据区已消费到的时序点序号（增量刷新用，开始新采集时复位）
        self._series_seen = 0

        self._build_ui()
        self._build_timers()
        # 先接日志再打启动信息，日志区才能看到本次运行的参数（规格书 7.3）
        self.log_view.attach_to_root_logger()
        self._log_startup()

    # ---------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.control_bar = ControlBar(self.config, central)
        self.control_bar.startRequested.connect(self.start_monitoring)
        self.control_bar.recordRequested.connect(self.start_recording)
        self.control_bar.openFileRequested.connect(self.open_file)
        self.control_bar.pauseToggled.connect(self.toggle_pause)
        self.control_bar.stopRequested.connect(self.stop)
        self.control_bar.settingsChanged.connect(self._on_settings_changed)

        self.view_options = ViewOptionsBar(self.config, central)
        self.view_options.changed.connect(self._on_view_options_changed)

        self.waveform_view = WaveformView(
            self.config.sample_rate, self.config.waveform_seconds, central
        )
        self.spectrum_view = SpectrumView(
            self.config.sample_rate, self.config.spectrum_log_x, central
        )
        self.pitch_view = PitchView(
            self.config.pitch_seconds,
            fmin=self.config.fmin,
            fmax=self.config.fmax,
            smoothing=self.config.pitch_smoothing,
            parent=central,
        )

        # 规格书 7.1：波形独占一行；第二行左频谱、右主频
        plots = QSplitter(Qt.Orientation.Horizontal, central)
        plots.addWidget(self.spectrum_view)
        plots.addWidget(self.pitch_view)
        plots.setStretchFactor(0, 1)
        plots.setStretchFactor(1, 1)
        plots.setSizes([600, 600])

        self.log_view = LogView(central)
        log_container = QWidget(central)
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)
        log_layout.addWidget(QLabel("日志 / 工作状态"))
        log_layout.addWidget(self.log_view)

        # 规格书 7.1 区域④：钢琴卷帘。**只在打开 MIDI 时显示**（M5 / FR-4.2），
        # 因此默认隐藏；显示时由 _set_piano_roll_visible 给它一个合理的初始高度。
        self.piano_roll = PianoRollView(central)
        self.piano_roll.setMinimumHeight(110)
        self._piano_roll_shown = False
        self.piano_roll.setVisible(False)

        # 规格书 v0.5 变更 2：日志区右侧为主频时序数据输出区，约占 1/3 宽度
        self.pitch_data_view = PitchDataView(
            smoothing=self.config.pitch_smoothing, parent=central
        )
        data_container = QWidget(central)
        data_container.setMinimumWidth(260)
        data_layout = QVBoxLayout(data_container)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(2)
        data_layout.addWidget(
            QLabel(f"主频时序数据（* 低频段 < {LOW_FREQ_NOTE_WARN_HZ:.0f} Hz 音名仅供参考）")
        )
        data_layout.addWidget(self.pitch_data_view)

        bottom = QSplitter(Qt.Orientation.Horizontal, central)
        bottom.addWidget(log_container)
        bottom.addWidget(data_container)
        bottom.setStretchFactor(0, 2)
        bottom.setStretchFactor(1, 1)
        bottom.setSizes([800, 400])

        splitter = QSplitter(Qt.Orientation.Vertical, central)
        splitter.addWidget(self.waveform_view)
        splitter.addWidget(plots)
        splitter.addWidget(self.piano_roll)  # 隐藏时 splitter 自动跳过（规格书 7.1 区域④）
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 0)
        splitter.setStretchFactor(3, 2)
        splitter.setSizes([260, 280, 0, 200])
        self._splitter = splitter

        layout.addWidget(self.control_bar)
        layout.addWidget(self.view_options)
        layout.addWidget(splitter, 1)

        self.progress_panel = ProgressPanel(central)
        self.progress_panel.seekRequested.connect(self._on_seek_requested)
        layout.addWidget(self.progress_panel)
        self.setCentralWidget(central)
        self._build_menus()

    def _build_menus(self) -> None:
        """菜单栏（规格书 7.1：文件 / 视图 / 帮助）。"""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("文件(&F)")
        file_menu.addAction("打开音频文件…").triggered.connect(self.open_file)
        file_menu.addAction("开始监听").triggered.connect(self.start_monitoring)
        file_menu.addAction("开始录制").triggered.connect(self.start_recording)
        file_menu.addAction("停止").triggered.connect(self.stop)
        file_menu.addSeparator()
        file_menu.addAction("退出").triggered.connect(self.close)

        view_menu = menu_bar.addMenu("视图(&V)")
        self._action_log_x = QAction("频谱对数轴", self)
        self._action_log_x.setCheckable(True)
        self._action_log_x.setChecked(self.config.spectrum_log_x)
        self._action_log_x.toggled.connect(self.view_options.spectrum_log_check.setChecked)
        view_menu.addAction(self._action_log_x)

        self._action_smoothing = QAction("主频中值滤波", self)
        self._action_smoothing.setCheckable(True)
        self._action_smoothing.setChecked(self.config.pitch_smoothing)
        self._action_smoothing.toggled.connect(self.view_options.pitch_smooth_check.setChecked)
        view_menu.addAction(self._action_smoothing)

        view_menu.addSeparator()
        view_menu.addAction("恢复默认设置").triggered.connect(self.restore_defaults)

        help_menu = menu_bar.addMenu("帮助(&H)")
        help_menu.addAction("关于").triggered.connect(self.show_about)

        # 快捷键（规格书 7.1 控件清单）
        self.control_bar.open_button.setShortcut(QKeySequence.StandardKey.Open)
        self.control_bar.start_button.setShortcut(QKeySequence("F5"))
        self.control_bar.record_button.setShortcut(QKeySequence("F6"))
        self.control_bar.pause_button.setShortcut(QKeySequence("F7"))
        self.control_bar.stop_button.setShortcut(QKeySequence("F8"))

        self._status_state = QLabel()
        self._status_params = QLabel()
        self._status_level = QLabel()
        self._status_buffer = QLabel()
        status = QStatusBar(self)
        for widget in (
            self._status_state,
            self._status_params,
            self._status_level,
            self._status_buffer,
        ):
            status.addPermanentWidget(widget)
        self.setStatusBar(status)
        self._update_status()

    def _build_timers(self) -> None:
        self._wave_timer = QTimer(self)
        self._wave_timer.setInterval(WAVEFORM_INTERVAL_MS)
        self._wave_timer.timeout.connect(self._tick_waveform)
        self._wave_timer.start()

        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setInterval(SNAPSHOT_INTERVAL_MS)
        self._snapshot_timer.timeout.connect(self._tick_snapshot)
        self._snapshot_timer.start()

    def _log_startup(self) -> None:
        logger.info(
            "界面就绪：采样率 %d Hz，FFT %d，窗 %s，主频算法 %s，分析跳步 %.1f ms",
            self.config.sample_rate,
            self.config.fft_size,
            self.config.window,
            self.config.pitch_method,
            HOP_SECONDS * 1000.0,
        )
        logger.info("频率分辨率 Δf = %.2f Hz（Δf = fs / N）", self.controller.analyzer.bin_hz)
        logger.info(
            "音名口径：十二平均律 A4 = %.0f Hz%s；录制结束后主频时序导出到 records/*.csv",
            A4_FREQ,
            "，3 点中值滤波开启" if self.config.pitch_smoothing else "（原始主频）",
        )
        logger.info("提示：选择输入设备后点击「开始」；无麦克风时可用 --demo-tone 1000 验证 A1")

    # ---------------------------------------------------------------- 控制
    def start_monitoring(self) -> None:
        """开始监听（只分析，不写文件）。"""
        self.progress_panel.reset()
        try:
            source = self.controller.start_monitoring()
        except Exception as exc:
            self._report_error("启动失败", str(exc))
            return
        self._reset_pitch_display()
        logger.info("输入源：%s", source.name)

    def start_recording(self) -> None:
        """开始录制（采集 + 实时分析 + 写 wav）。"""
        self.progress_panel.reset()
        try:
            source = self.controller.start_recording()
        except Exception as exc:
            self._report_error("开始录制失败", str(exc))
            return
        self._reset_pitch_display()
        logger.info("输入源：%s", source.name)

    def open_file(self) -> None:
        """选择并播放音频文件（FR-3）。"""
        start_dir = str(self.config.records_path.parent)
        path, _ = QFileDialog.getOpenFileName(self, "打开音频文件", start_dir, AUDIO_FILE_FILTER)
        if not path:
            return
        self.play_file(path)

    def play_file(self, path: str) -> None:
        """打开指定文件（供测试与命令行复用）。

        ``.mid`` / ``.midi`` 走**符号分析**分支（M5 / FR-4）：不做音频播放，
        直接画钢琴卷帘 + 音符频率曲线；其余后缀仍走播放 + 声学分析（FR-3）。
        """
        from pathlib import Path

        suffix = Path(path).suffix.lower()
        if suffix in MIDI_SUFFIXES:
            self.load_midi(path)
            return
        self.progress_panel.reset()
        try:
            self.controller.start_playback(path)
        except Exception as exc:
            self._report_error("打开文件失败", str(exc))
            return
        self._reset_pitch_display()

    def load_midi(self, path: str) -> bool:
        """打开 MIDI：钢琴卷帘 + 音符频率曲线（M5 / FR-4.1–FR-4.4）。

        Returns:
            成功为 ``True``；解析失败会给出中文提示并返回 ``False``。
        """
        if self.controller.state is not AppState.IDLE:
            logger.info("打开 MIDI 前先停止当前任务")
            self.stop()
        try:
            document = self.controller.load_midi(path)
        except Exception as exc:
            self._report_error("打开 MIDI 失败", str(exc))
            return False

        self.piano_roll.set_document(document)
        self._set_piano_roll_visible(True)
        # FR-4.4：复用主频时序图区域显示音符频率曲线，并标注"符号数据"
        self.pitch_view.show_symbolic(
            document.notes, source_label="MIDI", duration=document.duration
        )
        # 主频数据区是声学口径（时间/频率/音名/置信度），MIDI 没有声学帧 → 清空
        self.pitch_data_view.clear_data()
        self._series_seen = 0
        logger.info(
            "钢琴卷帘已显示：%s（频率曲线按 f = 440×2^((n-69)/12)，属符号数据）",
            document.describe(),
        )
        self._update_status()
        return True

    def _set_piano_roll_visible(self, visible: bool) -> int:
        """显示/隐藏钢琴卷帘，并返回它当前占的高度（0 表示隐藏）。

        显示时给一个初始高度：卷帘默认高度是 0，不主动分配的话用户会以为"没打开"。
        """
        if visible == self._piano_roll_shown:
            return self._splitter.sizes()[2] if len(self._splitter.sizes()) == 4 else 0
        self._piano_roll_shown = bool(visible)
        self.piano_roll.setVisible(self._piano_roll_shown)
        sizes = self._splitter.sizes()
        if len(sizes) != 4:
            return 0
        if self._piano_roll_shown:
            height = max(self.piano_roll.minimumHeight(), 180)
            top = max(int(sizes[0] * 0.5), 120)
            middle = max(int(sizes[1] * 0.5), 120)
            self._splitter.setSizes([top, middle, height, max(sizes[3], 140)])
        else:
            self._splitter.setSizes([sizes[0], sizes[1], 0, sizes[3]])
        final = self._splitter.sizes()
        return final[2] if len(final) == 4 else 0

    def toggle_pause(self) -> None:
        """暂停/继续（规格书 5.3）。"""
        if self.controller.state is AppState.PAUSED:
            self.controller.resume()
        else:
            self.controller.pause()

    def stop(self) -> None:
        """停止当前任务并回到空闲；录制结束时由控制器导出主频时序（v0.5 变更 3）。"""
        self.controller.stop()
        self.progress_panel.reset()
        self._report_pitch_export()

    def stop_monitoring(self) -> None:
        """兼容旧调用：等价于 :meth:`stop`。"""
        self.stop()

    def _reset_pitch_display(self) -> None:
        """开始新一次采集前清空主频时序图与数据区（v0.5 变更 2），并退出 MIDI 符号模式。"""
        self._series_seen = 0
        self.pitch_view.clear_symbolic()
        self.pitch_view.reset_track()
        self.pitch_data_view.clear_data()
        self.piano_roll.clear_notes()
        self._set_piano_roll_visible(False)

    def _report_pitch_export(self) -> None:
        """导出失败时给中文提示（成功路径只写日志，避免打断用户）。"""
        export = self.controller.consume_pitch_export()
        if export is None or export.error is None or self.quiet_errors:
            return
        QMessageBox.warning(
            self,
            "保存主频时序失败",
            f"音频已保存，但主频时序写盘失败。\n\n{export.error}",
        )

    def _on_seek_requested(self, seconds: float) -> None:
        self.controller.seek(seconds)

    def _report_error(self, title: str, message: str) -> None:
        """统一错误出口：写日志 + 中文对话框（A8）。"""
        logger.error("%s：%s", title, message)
        if self.quiet_errors:
            return
        QMessageBox.warning(self, title, f"{title}\n\n{message}")

    def _on_settings_changed(self, settings: dict) -> None:
        """分析参数（设备/采样率/FFT/窗/主频算法）：下次开始生效。"""
        changed = []
        for key, value in settings.items():
            if getattr(self.config, key, object()) != value:
                setattr(self.config, key, value)
                changed.append(f"{key}={value}")
        if not changed:
            return
        logger.info("参数已更新：%s（下次开始生效）", "，".join(changed))
        self.waveform_view.set_sample_rate(self.config.sample_rate)
        self.spectrum_view.set_sample_rate(self.config.sample_rate)
        self._update_status()

    def _on_view_options_changed(self, settings: dict) -> None:
        """视图选项（时间窗/对数轴/平滑）：立即生效。"""
        changed = []
        for key, value in settings.items():
            if getattr(self.config, key, object()) != value:
                setattr(self.config, key, value)
                changed.append(f"{key}={value}")
        if not changed:
            return
        self._apply_view_options()
        logger.info("视图已更新：%s", "，".join(changed))

    def _apply_view_options(self) -> None:
        """把视图相关配置同步到视图、数据区与菜单勾选状态。"""
        self.waveform_view.set_seconds(self.config.waveform_seconds)
        self.waveform_view.set_autoscale(self.config.waveform_autoscale)
        self.spectrum_view.set_log_x(self.config.spectrum_log_x)
        self.pitch_view.set_seconds(self.config.pitch_seconds)
        self.pitch_view.set_smoothing(self.config.pitch_smoothing)
        # 数据区与音名口径必须跟图一致（v0.5 变更 1/2）
        self.pitch_data_view.set_smoothing(self.config.pitch_smoothing)
        self.controller.pitch_series.set_smoothing(self.config.pitch_smoothing)
        for action, checked in (
            (self._action_log_x, self.config.spectrum_log_x),
            (self._action_smoothing, self.config.pitch_smoothing),
        ):
            action.blockSignals(True)
            action.setChecked(bool(checked))
            action.blockSignals(False)

    # ---------------------------------------------------------------- 菜单动作
    def restore_defaults(self) -> None:
        """FR-6.3：一键恢复默认设置（先停止当前任务）。"""
        if self.controller.state is not AppState.IDLE:
            logger.info("恢复默认设置：先停止当前任务")
            self.stop()
        defaults = AppConfig()
        defaults.validate()
        for key, value in defaults.to_dict().items():
            setattr(self.config, key, value)
        try:
            self.controller.apply_config(self.config)
        except Exception as exc:
            self._report_error("恢复默认设置失败", str(exc))
            return
        self.control_bar.reload_devices()
        self.control_bar.apply_config(self.config)
        self.view_options.apply_config(self.config)
        self.waveform_view.set_sample_rate(self.config.sample_rate)
        self.spectrum_view.set_sample_rate(self.config.sample_rate)
        self._apply_view_options()
        self.progress_panel.reset()
        # 恢复默认设置 = 完全复位，MIDI 符号数据也一并清掉（M5）
        self.controller.clear_midi()
        self._reset_pitch_display()
        self._update_status()
        logger.info("已恢复默认设置")

    def show_about(self) -> None:
        """帮助 → 关于。"""
        QMessageBox.about(
            self,
            "关于 zpyaudio",
            f"zpyaudio {__version__}\n\n"
            "实时音频分析：波形 / 频谱 / 主频时序\n"
            "需求规格书：docs/requirements.md\n\n"
            "快捷键：F5 开始　F6 录制　F7 暂停/继续　F8 停止",
        )

    def _on_state_changed(self, old_state: AppState, new_state: AppState) -> None:
        self.control_bar.set_state(new_state)
        if new_state is AppState.MONITORING:
            logger.info("开始实时分析（每 %.1f ms 一帧）", HOP_SECONDS * 1000.0)
        elif new_state is AppState.PAUSED:
            logger.info("已暂停")
        elif new_state is AppState.IDLE and old_state is not AppState.IDLE:
            logger.info("实时分析已停止")
        self._update_status()

    # ---------------------------------------------------------------- 定时器
    def _tick_waveform(self) -> None:
        self.waveform_view.update_from_ring(self.controller.ring)

    def _tick_snapshot(self) -> None:
        snapshot = self.controller.latest_snapshot()
        series = self.controller.pitch_series
        points = series.points_after(self._series_seen)
        if points:
            self._series_seen = points[-1].index
        if points or snapshot is not None:
            # 图与数据区共用同一份序列；无新有效点但有新帧时在末尾补断点（A6 静音断线）
            self.pitch_view.update_points(points, total_frames=series.frames)
            self.pitch_data_view.append_points(points)
        if snapshot is not None:
            self.spectrum_view.update_snapshot(snapshot)
        self._update_progress()
        self._update_status(snapshot, points[-1] if points else None)

    def _update_progress(self) -> None:
        """刷新进度条/时间标签，并处理播放自然结束（FR-3）。"""
        if self.controller.state is AppState.IDLE:
            return
        position = self.controller.position_seconds
        self.progress_panel.update_position(position, self.controller.duration_seconds)
        if self.controller.poll_completion():
            logger.info("文件播放结束：已播放 %.2f s", position)
            self.stop()

    def _update_status(self, snapshot=None, point=None) -> None:
        self._status_state.setText(f"状态：{self.controller.state.label()}")
        self._status_params.setText(
            f"采样率 {self.config.sample_rate} Hz ｜ Δf {self.controller.analyzer.bin_hz:.2f} Hz"
        )
        if snapshot is not None:
            if snapshot.has_pitch:
                freq = float(snapshot.dominant_freq)
                if point is not None:
                    freq = point.display_freq(smoothing=self.config.pitch_smoothing)
                info = freq_to_note(freq)
                note_text = ""
                if info is not None:
                    note_text = " ＝ " + format_note(
                        info, low_band_hint="full" if info.low_band else None
                    )
                self._status_level.setText(
                    f"RMS {snapshot.rms_db:.1f} dBFS ｜ 主频 {freq:.1f} Hz{note_text}"
                    f"（置信度 {snapshot.confidence:.2f}）"
                )
            else:
                self._status_level.setText(f"RMS {snapshot.rms_db:.1f} dBFS ｜ 主频 —")
        elif self.controller.midi_document is not None:
            document = self.controller.midi_document
            low, high = document.note_range
            self._status_level.setText(
                f"MIDI 符号数据 ｜ {document.note_count} 个音符 ｜ "
                f"音域 {low}–{high} ｜ {document.duration:.1f} s"
            )
        stats = self.controller.stats()
        buffer_text = (
            f"帧 {stats['snapshots']} ｜ overrun {stats['overrun']} ｜ 迟到 {stats['late_events']}"
        )
        if stats["playback_underruns"]:
            buffer_text += f" ｜ 播放欠载 {stats['playback_underruns']}"
        self._status_buffer.setText(buffer_text)
        if stats["overrun"] or stats["playback_underruns"]:
            self._status_buffer.setStyleSheet("color: #b8860b;")
        else:
            self._status_buffer.setStyleSheet("")

    # ---------------------------------------------------------------- 退出
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """停止采集、断开日志并保存配置（FR-6.1）。"""
        self._wave_timer.stop()
        self._snapshot_timer.stop()
        self.controller.shutdown()
        self.log_view.detach()
        if self._persist_config:
            try:
                save_config(self.config)
            except Exception:
                logger.exception("保存配置失败")
        super().closeEvent(event)
