zpyaudio 0.1.0 —— Windows x64 便携版（免安装）
================================================

用法
----
1. 把整个文件夹拷到任意位置（U 盘、桌面均可），双击 zpyaudio.exe 启动。
2. 界面里选择输入设备后点「开始」监听；「录制」边录边分析；「打开文件」播放 wav/mp3/MIDI。
3. 本文件夹内直接生成：
     config.json     界面设置的持久化文件（删除即恢复默认）
     logs/           运行日志（zpyaudio_YYYYmmdd_HHMMSS.log，出问题请先看这里）
     records/        录制的 wav 与同名主频时序 CSV

关于 ffmpeg（本版本**不含** ffmpeg）
-----------------------------------
* mp3 / m4a / aac 等有损格式由外部程序 ffmpeg 解码，需自行安装或放置。
* 程序启动时会**自动检测** ffmpeg：环境变量 ZPYAUDIO_FFMPEG_DIR / FFMPEG_DIR / FFMPEG_HOME、
  C:\ffmpeg\bin、常见安装位置（winget / chocolatey / scoop / Program Files）、PATH 依次查找，
  检测到即写进 config.json，并在日志里注明来源。
* 也可以「文件 → 设置 ffmpeg 路径…」手动指定 ffmpeg.exe 或它所在目录，
  用户指定的路径优先于自动检测结果。
* 未安装 ffmpeg 时：wav / flac / ogg 等无损格式与 MIDI 分析完全不受影响，
  只有打开 mp3/m4a/aac 时会给出中文提示。

命令行（可选，供自检/排障）
---------------------------
本程序是 GUI 子系统程序（双击不弹控制台），命令行参数仍然有效，结论写在 logs/*.log：
    zpyaudio.exe --list-devices            列出输入设备
    zpyaudio.exe --audio-check 3           无界面端到端自检（合成 1 kHz）
    zpyaudio.exe --file-check path\to.mp3  文件播放自检
    zpyaudio.exe --midi-check path\to.mid  MIDI 解析自检
    zpyaudio.exe --self-test               离屏起窗口自检

系统要求
--------
Windows 10/11 x64。不需要安装 Python 或任何依赖（已随包附带）。

许可证
------
MIT License，Copyright (c) 2026 zlz8x8 (zlz8x8@outlook.com)
第三方组件：Qt / PySide6（LGPLv3）、numpy / scipy（BSD）、pyqtgraph（MIT）、
sounddevice（MIT）、soundfile（BSD-3）、mido（MIT）。ffmpeg 不在本包内。
