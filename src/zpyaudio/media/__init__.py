"""媒体读写层：decoder / recorder / player / midi_loader / midi_render。

* ``decoder`` / ``recorder`` / ``player``：M3 落地的音频读写与播放；
* ``midi_loader``（M5）：``mido`` → :class:`~zpyaudio.media.midi_loader.NoteEvent`，
  MIDI 是**符号数据**，本层只解析、不合成；
* ``midi_render``（M7 预留）：``MidiRenderer`` 接口已就位，``render()`` 当前抛
  ``NotImplementedError``（FR-4.5）。
"""
