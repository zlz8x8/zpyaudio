"""MIDI → PCM 渲染接口（规格书 3.4 FR-4.5，M7 预留）。

**本期只有接口**（M5 交付物）：构造可用、``describe()`` 可读，但 :meth:`MidiRenderer.render`
一定抛 :class:`NotImplementedError`。这样做的目的是把"符号分析"和"声学分析"的边界
先钉死（决策 D3 / N1）：

* M5 只做**符号**分析（钢琴卷帘 + 音符频率曲线），不合成音频；
* 需要声学分析时，走 M7 用 FluidSynth + SoundFont 渲染出 PCM，
  再作为普通 ``AudioFrame`` 进入现有管线（环形缓冲 → 分析线程），
  这样波形/频谱/主频三张图不必为 MIDI 写第二套代码。

接口形状照 FR-4.5 保留：调用方（控制器/命令行）现在就可以按最终签名写，
M7 实现时只替换方法体。
"""

from __future__ import annotations

import logging
from pathlib import Path

from zpyaudio.core.frames import AudioFrame
from zpyaudio.media.midi_loader import MidiDocument

__all__ = ["MidiRenderer", "MIDI_RENDER_HINT"]

logger = logging.getLogger(__name__)

#: 给使用者的中文说明（CLI 与界面共用）
MIDI_RENDER_HINT = "MIDI → 音频渲染计划在 M7 里程碑提供（FluidSynth + SoundFont，规格书 N1）"


class MidiRenderer:
    """把 :class:`~zpyaudio.media.midi_loader.MidiDocument` 渲染成 PCM 的接口。

    Args:
        sample_rate: 目标采样率（Hz）。
        soundfont: SoundFont 文件路径；``None`` 表示使用 M7 的默认音色库。
    """

    def __init__(
        self,
        *,
        sample_rate: int = 48_000,
        soundfont: str | Path | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正，收到 {sample_rate}")
        self.sample_rate = int(sample_rate)
        self.soundfont = Path(soundfont) if soundfont is not None else None

    @property
    def available(self) -> bool:
        """渲染是否可用。M5 恒为 ``False``（M7 接入 FluidSynth 后由实现决定）。"""
        return False

    def describe(self) -> str:
        """一行中文描述，用于日志/界面提示。"""
        target = self.soundfont.name if self.soundfont is not None else "（默认音色库）"
        return f"MidiRenderer：{'可用' if self.available else '未实现'}，目标 {self.sample_rate} Hz，音色库 {target}"

    def render(self, document: MidiDocument, *, duration: float | None = None) -> AudioFrame:
        """把 MIDI 渲染成单声道/立体声 PCM。

        Args:
            document: 已解析的 MIDI 符号数据。
            duration: 期望时长（秒）；``None`` 表示按 :attr:`MidiDocument.duration`。

        Raises:
            NotImplementedError: 恒抛（M5 只有接口）。
        """
        logger.debug("MidiRenderer.render 被调用：%s", document.describe())
        raise NotImplementedError(MIDI_RENDER_HINT)
