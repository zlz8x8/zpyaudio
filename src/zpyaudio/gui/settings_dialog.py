"""设置对话框（FR-6.4：ffmpeg 路径）。

背景：mp3/m4a/aac 等有损格式交给外部 ``ffmpeg`` 解码，而 ffmpeg 不在
Python 依赖里，用户环境可能装在任意位置。因此：

* 程序启动时**自动检测** ffmpeg（环境变量 → 默认目录 → 常见安装位置 → ``PATH``），
  检测到就写回配置，用户无需手工配置；
* 检测不到（或想用另一个版本）时，用户在**本对话框**里指定可执行文件或所在目录，
  用户指定的路径优先于自动检测结果；
* 勾选「启动时自动检测」可让程序在配置路径失效时自动改回检测结果。

对话框只负责采集用户输入，写回配置与写盘由 :class:`~zpyaudio.gui.main_window.MainWindow`
完成；它同时提供一个「自动检测」按钮供用户即时查看检测结果。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from zpyaudio.app.config import AppConfig
from zpyaudio.media.decoder import ffmpeg_version, find_ffmpeg, find_ffmpeg_in

__all__ = ["SettingsDialog", "EXECUTABLE_FILTER"]

logger = logging.getLogger(__name__)

#: 「浏览」文件对话框过滤器
EXECUTABLE_FILTER = "ffmpeg 可执行文件 (ffmpeg.exe ffmpeg);;所有文件 (*)"

#: 状态提示配色
OK_COLOR = "#2e7d32"
WARN_COLOR = "#b8860b"


class SettingsDialog(QDialog):
    """ffmpeg 路径设置。

    路径留空表示"未指定"，程序每次启动都会自动检测；填写后该路径优先。
    """

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置 ffmpeg 路径")
        self.setMinimumWidth(600)

        self.path_edit = QLineEdit(config.ffmpeg_dir, self)
        self.path_edit.setPlaceholderText(r"留空表示自动检测（例如 C:\ffmpeg\bin 或 …\ffmpeg.exe）")
        self.path_edit.setClearButtonEnabled(True)
        self.browse_button = QPushButton("浏览…", self)
        self.detect_button = QPushButton("自动检测", self)
        self.autodetect_check = QCheckBox("启动时自动检测 ffmpeg（推荐）", self)
        self.autodetect_check.setChecked(bool(config.ffmpeg_autodetect))
        self.autodetect_check.setToolTip(
            "勾选：配置的路径失效时，启动过程会自动改用检测到的 ffmpeg；\n"
            "取消：始终只使用下面指定的路径，不自动改写。"
        )
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        #: 版本查询要起子进程：同一路径只查一次（对话框生命周期内）
        self._version_cache: dict[str, str] = {}

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("ffmpeg 路径"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.detect_button)

        hint = QLabel(
            "可指定 ffmpeg 可执行文件（ffmpeg.exe）或它所在的目录；"
            "留空则每次启动自动检测。",
            self,
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(path_row)
        layout.addWidget(hint)
        layout.addWidget(self.autodetect_check)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addWidget(buttons)

        self.browse_button.clicked.connect(self.browse)
        self.detect_button.clicked.connect(self.autodetect)
        self.path_edit.textChanged.connect(self._refresh_status)
        self.autodetect_check.toggled.connect(self._refresh_status)
        self._refresh_status()

    # ---------------------------------------------------------------- 取值
    def ffmpeg_path(self) -> str:
        """用户填写的 ffmpeg 路径（空串表示交给自动检测）。"""
        return self.path_edit.text().strip()

    def autodetect_enabled(self) -> bool:
        """是否启用「启动时自动检测」。"""
        return bool(self.autodetect_check.isChecked())

    # ---------------------------------------------------------------- 交互
    def autodetect(self) -> None:
        """立即执行一次自动检测，并把结果填入输入框。"""
        tool = find_ffmpeg()
        if tool is None:
            QMessageBox.warning(
                self,
                "自动检测",
                "未在 PATH 与常见安装位置找到 ffmpeg。\n\n"
                "请用「浏览…」手动指定 ffmpeg 可执行文件或所在目录。",
            )
            self._refresh_status()
            return
        self.path_edit.setText(str(tool))
        self.autodetect_check.setChecked(True)
        self._refresh_status()

    def browse(self) -> None:
        """选择 ffmpeg 可执行文件。"""
        current = self.ffmpeg_path()
        start = ""
        if current:
            candidate = Path(current)
            start = str(candidate.parent if candidate.is_file() else candidate)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg 可执行文件", start, EXECUTABLE_FILTER
        )
        if path:
            self.path_edit.setText(path)
            self._refresh_status()

    def accept(self) -> None:
        """校验路径可用性后再关闭（不可用时让用户确认）。"""
        configured = self.ffmpeg_path()
        if configured and find_ffmpeg_in(configured) is None:
            answer = QMessageBox.question(
                self,
                "路径不可用",
                f"在 {configured} 未找到 ffmpeg，仍要保存吗？\n\n"
                "保存后：勾选了自动检测则启动时改用检测结果，否则有损格式无法解码。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().accept()

    # ---------------------------------------------------------------- 状态
    def _refresh_status(self) -> None:
        """刷新状态行：当前输入是否可用 + 命中的 ffmpeg 版本。"""
        configured = self.ffmpeg_path()
        # 用户填了路径就严格只看该路径；留空才回退到自动检测（避免"路径写错却显示已找到别处的 ffmpeg"）
        tool = find_ffmpeg_in(configured) if configured else find_ffmpeg()
        if tool is None:
            self.status_label.setStyleSheet(f"color: {WARN_COLOR};")
            if configured:
                self.status_label.setText(f"⚠ 未找到 ffmpeg：{configured}")
            elif self.autodetect_check.isChecked():
                self.status_label.setText(
                    "⚠ 当前环境未检测到 ffmpeg：有损格式（mp3/m4a/aac）将无法解码"
                )
            else:
                self.status_label.setText("⚠ 未指定路径且已关闭自动检测：有损格式将无法解码")
            return
        version = self._ffmpeg_version_text(tool)
        prefix = "✅ 已找到" if configured else "✅ 自动检测到"
        self.status_label.setStyleSheet(f"color: {OK_COLOR};")
        self.status_label.setText(f"{prefix}：{tool}\n{version}")

    def _ffmpeg_version_text(self, tool: Path) -> str:
        """``ffmpeg -version`` 首行（带缓存，避免每次输入都起子进程）。"""
        key = str(tool)
        if key not in self._version_cache:
            self._version_cache[key] = ffmpeg_version(tool) or "版本未知"
        return self._version_cache[key]
