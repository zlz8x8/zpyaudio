# 音频分析软件需求规格书（zpyaudio）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v0.5.1（v0.5 + 运行环境基线更新与复测记录） |
| 状态 | 待评审 |
| 开发环境 | Windows 10/11 x64，Python 3.14.7（conda 环境 `ibase`） |
| 相关文档 | `docs/requirements.md`（本文）、`README.md`（已补） |

**v0.5.1 相对 v0.5 的变更（文档级，代码未改动）**：

1. 运行环境基线由 Python 3.13.16 / PySide6 6.9.3 更新为 **Python 3.14.7 / PySide6 6.12.0**（2.1、2.2、`requirements.txt`）。
2. 新增「新运行环境复测记录」（附录 C 末尾）：333 项单测全绿，GUI（离屏 + 真实桌面）、采集、录制 + CSV 导出、
   MIDI 自检全部通过。
3. 记录唯一失败项 **A5（文件播放进度偏差）**：其读数由输出设备/主机 API 决定（MME/蓝牙默认输出下 212–314 ms，
   WASAPI/WDM-KS 下 12–43 ms），并在 2.1 增加对应环境坑说明与待评审建议。

**v0.5 相对 v0.4 的主要变更（需求变更单）**：

1. **主频同时输出音名**（4.7、FR-2.7）：十二平均律 A4 = 440 Hz、科学音高记号、
   附音分偏差；低频段（< 250 Hz）必须标注"仅供参考"（Δf 粗于半音间距）。
2. **新增主频时序数据输出区**（7.1 区域⑥、7.2）：位于日志区右侧，约占窗口总宽度 1/3，
   最多显示最近 2000 帧；数据来自分析线程的序列收集器，而非 GUI 快照队列。
3. **录制结束后导出主频时序**（FR-1.8）：写入 `./records/*.csv`，与 wav 同文件名，
   UTF-8 with BOM，ASCII 列头；只导出有效主频帧。
4. 关闭第十一章原第 1 条待确认事项（主频序列是否导出），并新增验收项 A10/A11。

**v0.4 相对 v0.3 的主要变更**：

1. 回填 M2/M3/M4 实施结果，附录 C 扩写为 M0–M4 全量实测记录。
2. 4.2 明确**文件重采样统一交给 ffmpeg**（逐块 `resample_poly` 会在块边界产生周期性失真），
   并给出文件源的起播预填与节拍基准约定（4.5）。
3. 4.4 的 P2/P3 档位由"可切换"改为**已实现**，并补充各自的门限/退化处理。
4. 5.1 目录结构与 7.2 控件清单按实现补全（`media/`、`file_source`、`pitch_track`、对数轴、进度条、菜单与快捷键）。
5. 验收标准（第八章）标注实测结论，差异清单见附录 C。

**v0.3 相对 v0.2 的主要变更**：

1. 回填 M0/M1 实施结果，新增附录 C（实测记录与差异说明）。
2. 状态机新增 `MONITORING` 状态：M1 只监听不写文件，M3 起由 `RECORDING` 承担（见 5.3）。
3. 依赖清单状态更新为实测安装版本（2.2）。

**v0.2 相对 v0.1 的主要变更**（详见附录 A）：

1. 补全范围边界（明确 `mp4`、`librosa`、MIDI 合成的取舍），消除前后矛盾。
2. 把"统一音频数据流"从 4 行伪代码升级为有字段约定、声道/采样率/时间戳规则的正式接口。
3. 明确 1/16 秒刷新率对应的**跳步 / 窗长 / 频率分辨率**参数与默认值，并给出定量验收阈值。
4. 新增"主频估计"专章：区分**频谱峰值（主频）**与**基频/音高**，给出算法档位与降级策略。
5. 新增状态机、线程模型、异常处理、性能与打包要求（原文档仅一句话带过）。
6. MIDI 问题闭环：**本期只做符号分析**，FluidSynth 渲染列为可选里程碑 M7。
7. 记录已实测的环境事实与已知坑（如默认 `python` 命令解析到应用商店占位符）。

---

## 一、项目范围

### 1.1 目标

对音频信号进行**实时**分析，并在 GUI 上显示三类视图：

1. 波形时序图；
2. 以 **1/16 秒（62.5 ms）** 为刷新间隔的频谱图；
3. 以 **1/16 秒** 为刷新间隔的主频率时序图。

音频信号来源：**音频输入设备**（麦克风/线路输入）、**wav/mp3 文件**、**MIDI 文件**（符号分析，见 1.2/1.3）。

### 1.2 范围内（本期）

| 编号 | 内容 | 说明 |
| --- | --- | --- |
| S1 | 音频输入设备采集 | `sounddevice`，可选设备与采样率 |
| S2 | 录制为 `./records/*.wav` | `soundfile` 写盘，PCM_16 默认 |
| S3 | wav / mp3 文件读取与播放 | 播放有声音输出 |
| S4 | 实时波形 / 频谱 / 主频时序图 | 1/16 s 分析刷新 |
| S5 | 边录边实时分析 | 录制线程与分析链路共用同一路输入流 |
| S6 | MIDI 符号分析 | 钢琴卷帘图 + 音符频率曲线 |
| S7 | GUI 控制与日志 | 见第七章 |
| S8 | 单元测试与打包 | pytest + PyInstaller |
| S9 | 主频音名与录制导出 | 主频读数附音名（FR-2.7）；录制结束导出主频时序 CSV（FR-1.8） |

### 1.3 范围外（本期不做，仅预留接口）

| 编号 | 内容 | 处理方式 |
| --- | --- | --- |
| N1 | MIDI → 音频波形 | 不做合成；`MidiRenderer` 接口预留，列入可选里程碑 M7（FluidSynth + SoundFont） |
| N2 | 视频解码（mp4/mkv 等） | 不承诺支持。解码层基于 ffmpeg，若文件可用 ffmpeg 抽出音轨则"能播不保证"，不作为验收项 |
| N3 | 复音基频估计、音高转谱、和弦识别 | 主频视图仅对单音/强周期信号给出可信结果（见 4.4） |
| N4 | 实时效果器、混音、均衡 | — |
| N5 | 网络流（RTSP/HTTP）音频 | — |

### 1.4 术语约定

| 术语 | 含义 |
| --- | --- |
| 块（block） | 音频回调一次交付的样本数组，如 512 / 1024 样本 |
| 窗（window） | 一次 FFT 使用的样本数 N |
| 跳步（hop） | 相邻两次分析起点间隔。本项目固定 hop = 1/16 s = 62.5 ms |
| 帧（frame） | 本文中"帧"指一次分析输出的快照（频谱 + 主频），非音频单样本帧 |
| 主频 | 频谱幅度最大处对应的频率（dominant frequency） |
| 基频 | 周期信号的 f0（pitch），与主频在复音/含强泛音时可能不同 |
| 音名 | 把主频按十二平均律就近命名的结果（v0.5 新增，见 4.7），**不是**音高估计 |
| 音分（cent） | 十二平均律中半音的 1/100；偏差 = 1200 × log2(f / f_就近音名) |
| dBFS | 相对满量程的分贝，0 dBFS = 幅度 1.0 |

---

## 二、运行环境与依赖基线（已实测）

### 2.1 环境事实

| 项 | 实测值 | 备注 |
| --- | --- | --- |
| 解释器 | `C:\miniconda3\envs\ibase\python.exe` = **Python 3.14.7**（Anaconda 打包版，MSC v.1942 x64） | 基线环境（2026-10-08 由 3.13.16 升级） |
| 已安装 | numpy 2.5.3、scipy 1.18.1、PySide6 6.12.0、pyqtgraph 0.14.0 | 直接可用 |
| 音频 I/O | sounddevice 0.5.6、soundfile 0.14.0 | Windows wheel 自带 PortAudio / libsndfile |
| MIDI / 测试 | mido 1.3.3、pytest 9.1.1 | 直接可用 |
| ffmpeg | `C:\ffmpeg\bin\ffmpeg.exe`，版本 `N-122544-g8966101fa6-20260125` | 已含 libmp3lame / libsoxr |
| ffprobe | `C:\ffmpeg\bin\ffprobe.exe` | 用于探测时长/采样率/声道 |

> ⚠️ 环境坑（必须处理）
> `python` 命令当前解析到 `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`（应用商店占位符），执行后无输出。文档与脚本一律使用 conda 环境路径或 `conda run -n ibase`。

> ⚠️ 环境坑（2026-10-08 新发现）：**系统默认输出设备会显著影响 A5 的进度偏差读数**。
> `--file-check` 用「已送出声卡的帧数」对比墙钟，两者之差约等于「输出队列预填 + 输出缓冲延迟」。
> 实测：WASAPI / WDM-KS 主机 API（缓冲 3–43 ms）偏差 **12–43 ms**（符合 A5 的 < 200 ms）；
> 若系统默认输出是 **MME**（缓冲 0.09–0.18 s，且消费速率偏慢、源侧出现丢块）或蓝牙耳机，
> 偏差会到 **0.21–0.36 s** 而判 FAIL。属设备属性，非程序回归；详见附录 C。

### 2.2 依赖清单

| 用途 | 库 | 状态 | 说明 |
| --- | --- | --- | --- |
| 音频输入/输出 | `sounddevice` | 已装 0.5.6 | Windows wheel 自带 PortAudio DLL |
| wav/flac/ogg 读写 | `soundfile` | 已装 0.14.0 | 基于 libsndfile（M3 起使用） |
| DSP | `numpy`、`scipy.signal` | 已装 | FFT、`resample_poly`、`get_window`、`find_peaks` |
| mp3 等有损格式解码 | `ffmpeg`（外部程序） | 已装 | 本程序不做 mp3 解码 |
| MIDI 解析 | `mido` | 已装 1.3.3 | **首选**，轻量且维护活跃（M5 使用） |
| MIDI 高级解析 | `pretty_midi` | 待评估 | 仅当需要 tempo/instrument 细分时才引入；若在 3.14 上安装失败则降级为纯 `mido` 实现 |
| 高级音频分析 | `librosa` | **可选** | 体积大、依赖多。仅在需要梅尔谱/chroma/节拍等特性时引入；基础功能不依赖它 |
| GUI | `PySide6`、`pyqtgraph` | 已装 6.12.0 / 0.14.0 | 实时绘图只用 pyqtgraph，禁用 matplotlib 实时刷新 |
| 测试 | `pytest`、`pytest-qt` | 已装 9.1.1 / 4.5.0 | GUI 测试使用 `QT_QPA_PLATFORM=offscreen` |
| 打包 | `pyinstaller` | 待安装 | 需 `pyinstaller-hooks-contrib` 以正确处理 sounddevice/PySide6 |

依赖必须固定小版本并写入 `requirements.txt`（或 `pyproject.toml`），保证可复现构建。

---

## 三、功能需求

需求编号规则：`FR-x.y`；每条需求给出**验收条件**（可测）。

### 3.1 FR-1 录制

| 编号 | 需求 | 验收条件 |
| --- | --- | --- |
| FR-1.1 | 从选定输入设备采集音频并实时写入 `./records/*.wav` | 录制 10 s 后文件可被 `soundfile` 读取，时长误差 < 50 ms |
| FR-1.2 | 文件命名 `YYYYmmdd_HHMMSS_<设备名>.wav` | 目录中不重名，时间戳与日志一致 |
| FR-1.3 | 采样率与声道沿用设备实际参数，默认 PCM_16，可选 PCM_24 / FLOAT | 文件头字段与设置一致 |
| FR-1.4 | 录制中可暂停/继续，暂停期间不写入样本但保留同一文件 | 暂停 5 s 后续录，文件总时长 ≈ 实际录制时长（不含暂停） |
| FR-1.5 | 录制同时进行实时分析（S5） | 录制期间波形/频谱/主频持续刷新，wav 内容与视图一致 |
| FR-1.6 | 停止时刷新并关闭文件，日志输出开始时间/结束时间/时长/路径/大小 | 日志字段齐全（见 7.3） |
| FR-1.7 | 磁盘写入在独立线程，不阻塞分析链路 | 写盘耗时不影响 1/16 s 分析节拍（见 NFR-2） |
| FR-1.8 | 录制结束后把本次主频时序导出为 `./records/*.csv`（v0.5） | 文件名与 wav 同 stem；行数 = 本次有效主频帧数；列头与 4.7 一致；导出失败只提示、不影响 wav 收尾 |

### 3.2 FR-2 音频分析

| 编号 | 需求 | 验收条件 |
| --- | --- | --- |
| FR-2.1 | 输出时序波形图（滚动窗口） | 窗口默认 0.5 s，可选 0.1/0.25/0.5/1/2 s；说话时波形可见变化 |
| FR-2.2 | 输出 1/16 s 刷新的频谱图 | 峰值频率与真实值偏差 ≤ 1 个 bin，插值后 ≤ 2 Hz（验收项 A1） |
| FR-2.3 | 输出 1/16 s 刷新的主频时序图 | 1 kHz 稳定正弦下主频读数 1000 ± 5 Hz（验收项 A2） |
| FR-2.4 | 文件播放时同步有声音输出 | 播放 mp3/wav 有声音，进度显示与实际偏差 < 200 ms |
| FR-2.5 | 分析参数可配置：FFT 长度 1024/2048/4096、窗函数 Hann/Hamming/Blackman | 切换后频谱形态与分辨率符合 4.3 表格 |
| FR-2.6 | 静音或非周期信号不产生虚假主频 | 静音（< -60 dBFS）时主频视图断开，不画点 |
| FR-2.7 | 输出主频时同时输出音名（v0.5） | A4=440、科学音高记号、附音分偏差；1 kHz 显示 `B5 +21.3¢`（±0.5 音分）；< 250 Hz 标注"仅供参考"；静音显示 `—` |
| FR-2.8 | 主频时序数据区逐帧显示（v0.5） | 数据区显示时刻/频率/音名/置信度，最多保留最近 2000 帧；**显示丢帧不影响 CSV 完整性** |

### 3.3 FR-3 文件播放

| 编号 | 需求 | 验收条件 |
| --- | --- | --- |
| FR-3.1 | 支持 wav / mp3 打开并准实时分析 | 打开后按 1/16 s 节拍推进，与播放位置同步 |
| FR-3.2 | 支持播放/暂停/停止/进度拖动（seek） | 拖动后音频与视图同时跳转 |
| FR-3.3 | 播放与分析共用同一解码数据源 | 不存在"听得到但图不动"或反之 |
| FR-3.4 | 文件信息（路径、总时长、格式、采样率、声道）写入日志区 | 打开文件即展示 |

### 3.4 FR-4 MIDI 符号分析（本期方案）

| 编号 | 需求 | 验收条件 |
| --- | --- | --- |
| FR-4.1 | 解析 MIDI 为音符事件序列 `(start, end, note, velocity, channel, track)` | 解析结果与 `mido` 读出的消息数一致 |
| FR-4.2 | 显示钢琴卷帘图（横轴时间、纵轴音高） | 音符起止位置正确 |
| FR-4.3 | 按 `f = 440 × 2^((note-69)/12)` 输出音符频率曲线 | C4（note=60）显示 261.63 Hz，误差 < 0.01 Hz |
| FR-4.4 | 复用主频时序图区域显示频率曲线，并标注"符号数据" | 图上有明确标识，不与声学主频混淆 |
| FR-4.5 | 保留 `MidiRenderer` 接口（默认抛"未实现"） | 接口存在且被测试覆盖 |

> 决策记录（D3）：MIDI 不做波形/频谱分析——**MIDI 没有音频波形，只有音符事件**。本期按符号数据绘制；若后续需要声学分析，走 M7 用 FluidSynth 渲染 PCM 后再进入统一管线。

**实现口径（M5 落地，实测后补充）**：

| 项 | 口径 | 为什么 |
| --- | --- | --- |
| 时间换算 | ``mido`` 逐轨迭代给出的 ``Message.time`` 单位是 **tick**（迭代整个 ``MidiFile`` 才是秒，但那样拿不到轨道号）。本实现自建 tempo map，把绝对 tick 换算成秒 | FR-4.1 要求事件带 ``track``；照抄 ``msg.time`` 会得到"整体被拉长几百倍"的错时间轴 |
| ``note_on velocity=0`` | 按 MIDI 惯例视为 ``note_off`` | 相当一部分文件（含部分音序器导出）用它表示松键 |
| 同音高重叠 | 同一 ``(channel, note)`` 用**队列**逐个配对 | 长音/踏板段落会有嵌套的同音高音符；用"字典覆盖"会直接丢音符，``note_on`` 计数因此对不上 |
| 轨末未闭合 | 不丢弃，按轨末闭合 | 宁可长一点，也不要凭空少一个音 |
| ``type 2`` | 各轨独立，逐轨用**自己的** tempo map | type 2 的轨道是互不同步的独立序列 |
| 横轴 | 符号模式下是绝对时间 ``0…曲长``（声学模式才是"最近 N 秒"） | FR-4.4 复用同一区域但语义不同，必须一眼可辨 |

### 3.5 FR-5 GUI

见第七章（布局、控件清单、日志字段）。

### 3.6 FR-6 配置与持久化

| 编号 | 需求 | 验收条件 |
| --- | --- | --- |
| FR-6.1 | 记忆上次使用的设备、FFT 长度、窗函数、视图时间窗 | 重启后自动恢复 |
| FR-6.2 | 配置写入 `./config.json`，缺失或损坏时回退默认值 | 删除文件后程序正常启动 |
| FR-6.3 | 提供"恢复默认设置" | 一键复位 |

---

## 四、关键处理链路规格

### 4.1 统一音频数据流（正式接口）

不论来自麦克风、wav、mp3 还是未来的 MIDI 合成，一律规整为 `AudioFrame`：

```python
@dataclass(frozen=True, slots=True)
class AudioFrame:
    samples: np.ndarray   # dtype=float32, C 连续
                          # shape=(n,) 单声道，或 (n, channels) 多声道交织
    sample_rate: int      # Hz
    channels: int         # 1 或 2（本期上限 2）
    timestamp: float      # 秒，相对本次数据流起点的**首样本**时刻，单调递增
```

字段约定（原文档未定义，属新增约束）：

| 约定 | 内容 |
| --- | --- |
| 值域 | `[-1.0, 1.0]`，超出即裁剪，不做自动增益 |
| dtype | 一律 `float32`；整型解码后除以满量程归一 |
| 时间戳 | `timestamp[i+1] = timestamp[i] + n[i] / sample_rate`，由样本计数推导，**不使用墙钟累加**，避免漂移 |
| 声道 | 分析链路统一转单声道；播放与录制链路保留原始声道 |
| 块大小 | 默认 1024 样本（可配 512/1024/2048）；文件源按 `hop` 切块以模拟实时 |
| 末块 | 数据流结束时允许长度 < 块大小，分析时右侧补零并标记 `is_final` |

### 4.2 采样率与声道规整

1. **工程采样率** `fs_analysis`：默认 48000 Hz，可选 44100 / 16000（16000 可显著降低 CPU，适合只看语音低频）。
2. 输入采样率 ≠ `fs_analysis` 时**统一交给 ffmpeg 重采样**（`-ar <fs>`）。
   仅在内存中的短数据块上使用 `scipy.signal.resample_poly`；**逐块重采样文件流会在块边界产生周期性失真**，
   因为多相滤波器没有跨块状态，故不用于文件播放链路。
3. 多声道 → 单声道：分析用 `samples.mean(axis=1)`；**播放/录制不做降混**。
4. 播放采样率若设备不支持，先重采样到设备默认采样率，再送入 `OutputStream`。

### 4.3 分析参数与频率分辨率

固定刷新节拍：`hop = 1/16 s = 62.5 ms` → 分析帧率 **16 Hz**；GUI 定时器以 30–60 Hz 重绘最新快照（重绘不等于重算）。

窗长默认 **4096**（@48 kHz = 85.3 ms > hop，形成约 27% 重叠，主频估计更稳）。频率分辨率 Δf = fs / N：

| N \ fs | 44100 Hz | 48000 Hz | 16000 Hz |
| --- | --- | --- | --- |
| 1024 | 43.1 Hz | 46.9 Hz | 15.6 Hz |
| 2048 | 21.5 Hz | 23.4 Hz | 7.8 Hz |
| **4096（默认）** | **10.8 Hz** | **11.7 Hz** | 3.9 Hz |

> 注意：Δf = 11.7 Hz 意味着**裸 FFT 峰值无法满足 ±5 Hz 的主频精度**，必须做谱线插值（见 4.4）。若需要更细分辨率，可在不改变窗长的前提下补零（zero padding）到 2×N，并对结果说明"插值分辨率 ≠ 真实分辨率"。

窗函数：默认 Hann（幅度精度与泄漏折中），可选 Hamming / Blackman。幅度谱按窗函数相干增益归一，显示用 dBFS。

### 4.4 主频估计（专章）

**关键区分：主频 ≠ 基频。** 复音信号、强泛音信号下"最大谱峰"未必是 f0。本软件按使用场景分档，并在界面上标注所用方法：

| 档位 | 算法 | 适用 | 状态 |
| --- | --- | --- | --- |
| P1 | FFT 峰值 + 抛物线插值（对数幅度） | 单音、正弦、乐器单音 | ✅ 默认 |
| P2 | 谐波积谱 HPS（1–5 次谐波相乘） | 含强泛音的单音（人声、弦乐） | ✅ 可切换 |
| P3 | 自相关 / YIN（时域 CMND + 抛物线插值） | 低频与低信噪比 | ✅ 可切换 |

> P2 的退化处理：候选基频必须高于（全局峰值 − 35 dB）才被考虑，否则纯音的 h 次谐波会与真实基频并列。
> P3 用「能量前缀和 + 互相关」计算差分函数，单帧耗时实测约 0.27 ms（NFR-1 预算 5 ms）。

公共规则：

| 规则 | 取值 |
| --- | --- |
| 搜索范围 | `fmin=50 Hz`，`fmax=min(5000, 0.45 × fs_analysis)` |
| 静音门限 | RMS < **-60 dBFS** ⇒ 无有效主频，视图断开 |
| 置信度 | P1：峰值 dB 与谱底噪中位数之差归一化；P2/P3：算法自身指标。输出 `confidence ∈ [0,1]` |
| 平滑 | 时序图对主频序列做 3 点中值滤波，抑制倍频跳变（跳变检测：Δf 绝对值 > 12% 且非整数倍关系时标为可疑点） |
| 显示 | 频率轴对数；点颜色/透明度随置信度变化 |

输出结构：

```python
@dataclass(frozen=True)
class AnalysisSnapshot:
    t: float                  # 快照时刻（秒）
    spectrum_freqs: np.ndarray  # (N//2+1,)
    spectrum_db: np.ndarray     # (N//2+1,) dBFS
    peak_freq: float            # 频谱峰值频率（Hz），仅用于谱峰标注
    dominant_freq: float        # 主频（Hz），0 表示无有效值
    confidence: float
    rms_db: float
```

### 4.5 文件解码与播放链路

- **wav / flac / ogg**：`soundfile.read(..., dtype="float32", always_2d=True)`。
- **mp3 / m4a / aac**：**不自研解码**，调用 ffmpeg 输出裸 PCM 流：

```bat
ffmpeg -hide_banner -v error -nostdin -i "<file>" ^
       -f f32le -acodec pcm_f32le -ac <channels> -ar <fs> pipe:1
```

  要点：`-v error` 抑制日志噪声；`-nostdin` 防止 ffmpeg 抢占控制台输入；Windows 下 `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)` 避免弹黑窗；seek 用 `-ss <秒>` 重启进程或从头丢弃样本。
- **元信息**：`ffprobe -v error -show_entries format=duration:stream=sample_rate,channels,codec_name -of json "<file>"`。
- **播放**：`sounddevice.OutputStream(samplerate, channels, dtype="float32", callback=...)` 从解码缓冲取数据；暂停 = 停止消费并保留位置；全双工（边录边放）需保证输入输出环缓冲独立。
- **准实时**：文件源按 `hop` 切块推进，逻辑时间由样本计数决定，不用 `time.sleep` 累加（避免系统抖动导致漂移）。
- **起播预填**：起播前先全速读出若干块把播放队列填到接近满（默认 6/8 块），
  并把节拍基准**回拨预填时长**——否则源会干等一个预填时长 → 队列被抽干 → 输出欠载。
  实测预填修正后起播欠载由 9 次降为 **0 次**（见附录 C）。
- **依赖收敛**：解码只用 `soundfile`（无损格式）与 `ffmpeg`（有损格式）两条路径，不引入同职责的第三方解码封装，避免依赖链膨胀与双解码结果不一致。

### 4.6 环形缓冲区

```python
class RingBuffer:                     # 单写多读，float32
    def __init__(self, capacity: int, channels: int) -> None: ...
    def write(self, block: np.ndarray) -> int: ...        # 满则覆盖最旧，累加 overrun 计数
    def read_latest(self, n: int) -> np.ndarray: ...      # 返回最近 n 样本的副本
    def read_range(self, start: int, n: int) -> np.ndarray: ...
    @property
    def overrun(self) -> int: ...                          # 丢样本统计，用于日志告警
```

容量默认 **5 s**（波形最大显示 2 s + 主频 1 s 余量 + 安全边界）。回调线程**只做写入**，不做任何分配以外的计算。

### 4.7 音名与主频时序数据（v0.5 新增）

**音名口径**（`core/notes.py`，纯计算、可单测）：

| 规则 | 取值 |
| --- | --- |
| 律制 / 参考音 | 十二平均律，A4 = **440 Hz** |
| 记法 | 科学音高记号（C4 = 261.63 Hz），升号写法（C#/D#/F#/G#/A#） |
| 音分偏差 | `1200 × log2(f / f_就近音名)`，范围 `(-50, +50]`，显示保留 1 位小数 |
| 低频提示 | `f < 250 Hz` 时音名标注"仅供参考"（Δf 粗于半音间距，音名可能整体偏半音） |
| 无效值 | `f ≤ 0`、非有限、静音帧 → 显示 `—`，不产生音名 |
| 与 MIDI 一致性 | `note_frequency(midi)` 与 FR-4.3 的 `f = 440 × 2^((note-69)/12)` 互为逆运算，M5 直接复用 |

**取值口径**：音名一律取自"界面显示的那个主频"——中值滤波开启时取 3 点中值
（CSV 的 `freq_smoothed_hz`），关闭时取原始值（`freq_hz`）；CSV 两列都写以便复算。
音名的可信度必须与置信度、音分偏差一同展示，不得只给一个孤立的音名。

**主频时序数据流**：

```text
[分析线程] Analyzer.analyze → AnalysisSnapshot
                                ├─► PitchSeries.push（单写收集，无锁、不做 IO）
                                │      ├─ 最近 2000 点 ──► GUI 数据区（points_after 增量追加）
                                │      └─ 录制全量点 ────► 停止时写 records/*.csv
                                └─► 有界快照队列(maxsize=4) ──► 频谱/波形视图
```

| 规则 | 取值 |
| --- | --- |
| 收集位置 | 分析线程（`AudioController._publish`），**不依赖 GUI 定时器** |
| 显示容量 | 最近 2000 行（`gui/pitch_data_view.py`，含列头），超出丢弃最旧 |
| 导出范围 | 仅录制（`RECORDING`）保留全量点；监听/播放只保留最近点 |
| 静音帧 | 不产生数据行，只推进帧序号（供主频图断线，A6） |
| 上限保护 | 单次录制最多保留 200000 点（≈3.5 h @16 Hz），超出截断并告警 |
| 时间基准 | 流相对时间（录制时 `ring.clear()` 已复位 → 录制相对秒） |
| 文件名 | 与 wav 同 stem：`YYYYmmdd_HHMMSS_<设备名>.csv` |
| CSV 列头 | `t_s,freq_hz,freq_smoothed_hz,note,cents,confidence,rms_dbfs,method` |
| 编码 | UTF-8 with BOM（Excel 直接打开），`\n` 换行，父目录自动创建 |
| 失败处理 | 捕获异常 → 日志 + 中文提示；wav 已完成收尾，不受影响（A8） |

CSV 示例（1 kHz 合成正弦）：

```csv
t_s,freq_hz,freq_smoothed_hz,note,cents,confidence,rms_dbfs,method
0.0853,1000.18,1000.18,B5,21.3,0.92,-9.03,fft_parabolic
```

---

## 五、架构、线程与状态机

### 5.1 建议目录结构

```text
zpyaudio/
├─ docs/requirements.md
├─ main.py                     # 入口
├─ config.json                 # 运行期生成
├─ records/                    # 录制输出
├─ logs/                       # 日志文件
├─ src/zpyaudio/
│  ├─ core/
│  │  ├─ frames.py             # AudioFrame / AnalysisSnapshot / 时间戳工具
│  │  ├─ ringbuffer.py         # 单写多读、近似无锁
│  │  ├─ resample.py           # 采样率、声道规整
│  │  ├─ analyzer.py           # 加窗 + FFT + 谱峰（无 GUI 依赖）
│  │  ├─ pitch.py              # P1/P2/P3 主频算法
│  │  ├─ pitch_track.py        # 3 点中值滤波 + 跳变/谐波关系判定
│  │  ├─ notes.py              # [v0.5] 频率 → 音名（A4=440，科学音高记号）
│  │  └─ pitch_series.py       # [v0.5] 主频时序收集（最近点 + 录制全量）+ CSV 导出
│  ├─ sources/
│  │  ├─ base.py               # AudioSource 协议 + 块回调挂载
│  │  ├─ device_source.py      # sounddevice 输入
│  │  ├─ synthetic_source.py   # 合成测试信号（无麦克风环境验证 A1/A6）
│  │  └─ file_source.py        # wav/mp3 准实时源（预填 + 节拍 + seek + 暂停）
│  ├─ media/
│  │  ├─ decoder.py            # soundfile / ffmpeg 子进程 / ffprobe
│  │  ├─ recorder.py           # WAV 写盘线程
│  │  ├─ player.py             # OutputStream 播放线程
│  │  ├─ midi_loader.py        # [M5] mido → NoteEvent[]
│  │  └─ midi_render.py        # [M7] FluidSynth 渲染（预留）
│  ├─ app/
│  │  ├─ controller.py         # 状态机 + 线程编排（唯一持有状态的地方）
│  │  ├─ config.py             # 默认参数与读写
│  │  └─ logging_setup.py
│  └─ gui/
│     ├─ main_window.py
│     ├─ waveform_view.py
│     ├─ spectrum_view.py
│     ├─ pitch_view.py
│     ├─ pitch_data_view.py    # [v0.5] 主频时序数据输出区（日志右侧，约 1/3 宽）
│     ├─ pianoroll_view.py
│     ├─ controls.py
│     └─ log_view.py
└─ tests/
```

分层规则：`gui` 只依赖 `app` 暴露的信号/快照；`core` 与 `media` **禁止 import PySide6/pyqtgraph**，保证算法可单测。根目录 `main.py` 负责把 `src/` 加入 `sys.path` 后启动 GUI（未做可安装包时），打包入口同样是它。

### 5.2 线程模型

```text
[输入设备回调线程] --write--> [环形缓冲 RingBuffer] --read_latest--> [分析线程]
   (sounddevice, 不计算)              |                                ├─► [PitchSeries 收集]（v0.5）
                                      |                                │      ├─ 最近 2000 点 → GUI 数据区
                                      |                                │      └─ 录制全量点 → 停止时写 CSV
                                      |                                └─► [AnalysisSnapshot 队列(有界,1~4)]
                                      v                                              |
                              [录制写盘线程]                                         v
                                                              [GUI 主线程 QTimer 30~60 Hz 重绘]
[文件解码线程 ffmpeg] --> [文件源缓冲] --hop 节拍--> 分析线程 / [播放线程 OutputStream]
```

硬性约束：

1. **GUI 主线程只做绘图**：不 FFT、不做文件 IO、不阻塞等待。
2. 音频回调线程内**禁止**加锁分配、日志、绘图；仅写入环形缓冲或队列。
3. 所有跨线程队列**有界**，满时丢最旧 + 计数告警（禁止无界增长）。
4. 分析线程与写盘线程使用**线程优先级/独立队列**，写盘抖动不得反向影响分析。
5. 后台线程统一为 `threading.Thread(daemon=True)` 或用 `QThread`，退出时按"停止回调 → 排空队列 → join(超时) → 关闭设备/文件"顺序释放。
6. 约束 1 的**唯一例外（v0.5 明确）**：主频时序 CSV 在 `AudioController.stop()` 内同步写出
   （毫秒级，30 min ≈ 2.9 万行）。它与既有的 `recorder.stop()`（join 写盘线程 + 关闭 wav）
   同属"停止收尾"，此时分析线程已停；写失败只提示，不影响 wav（FR-1.8 / A8）。
   主频时序的**收集**必须留在分析线程（见 4.7），不得依赖 GUI 定时器。

### 5.3 状态机

状态：`IDLE`（空闲）、`MONITORING`（监听中，M1 新增）、`RECORDING`、`PLAYING`、`PAUSED`、`ERROR`。

> `MONITORING` 是 M1 的临时状态：只采集与分析、不写文件；M3 落地录制后由 `RECORDING` 承担。
> 新增该状态的理由：M1 的最小原型需要"能开始/停止实时分析"但尚无录制与播放能力，
> 若强行复用 `RECORDING` 会让日志与界面显示错误的语义。

| 当前 | 事件 | 迁移 | 副作用 |
| --- | --- | --- | --- |
| IDLE | 开始监听（M1，不写文件） | MONITORING | 打开输入源、启动分析线程 |
| MONITORING | 停止 | IDLE | 停止分析线程、关闭输入源、清空缓冲、日志收尾 |
| IDLE | 开始录制 | RECORDING | 打开设备、创建 wav、启动分析 |
| IDLE | 打开文件并播放 | PLAYING | 启动解码、播放、分析 |
| RECORDING | 暂停 | PAUSED | 停止写盘与播放，保留缓冲 |
| PLAYING | 暂停 | PAUSED | 停止消费输出，保留位置 |
| PAUSED | 继续 | RECORDING / PLAYING | 按暂停前模式恢复 |
| RECORDING / PLAYING | 停止 | IDLE | 关闭文件（写 wav 头）、关闭设备、清空缓冲、日志收尾 |
| PLAYING | 播放到结尾 | IDLE | 日志输出完成信息 |
| 任意 | 异常 | ERROR | 弹窗 + 日志，资源回滚到 IDLE |

非法迁移（如 IDLE 下点暂停）一律忽略并记录一条调试日志，不弹错。

---

## 六、非功能需求（NFR）

| 编号 | 类别 | 要求 | 验证方式 |
| --- | --- | --- | --- |
| NFR-1 | 性能 | 单次分析（N=4096 实 FFT）耗时 < 5 ms；16 Hz 节拍下分析线程 CPU 占用 < 15%（i5 级） | 计时打点 + 任务管理器 |
| NFR-2 | 实时性 | 采集到显示的端到端延迟 < 150 ms；连续 30 min 无累积延迟增长 | 敲击测试 + 长时间运行 |
| NFR-3 | 稳定性 | 连续运行 30 min 内存增长 < 50 MB；无未捕获异常退出 | 长期运行 + 日志检查 |
| NFR-4 | 健壮性 | 设备不存在/被占用、采样率不支持、文件损坏、ffmpeg 缺失、磁盘满 —— 均给出中文提示且程序不崩溃 | 手工故障注入 |
| NFR-5 | 可用性 | 全中文界面；日志带毫秒级时间戳；错误有对话框与日志双通道 | 走查 |
| NFR-6 | 可维护性 | `core`/`media` 单测覆盖关键路径，行覆盖 ≥ 70%；类型注解齐全 | `pytest --cov` |
| NFR-7 | 可打包 | `PyInstaller --onedir` 可在无 Python 环境的 Windows 上启动 | 干净虚拟机验证 |
| NFR-8 | 兼容性 | Windows 10/11 x64；高 DPI 下布局不裁切 | 缩放 100%/150% 走查 |
| NFR-9 | 可观测性 | 支持 `--log-level DEBUG`；运行日志落盘 `logs/zpyaudio_YYYYmmdd_HHMMSS.log` | 走查 |

---

## 七、GUI 设计

### 7.1 布局

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 菜单栏：文件(打开/保存/退出)  视图(时间窗/FFT/窗函数)  帮助           │
├──────────────────────────────────────────────────────────────────────┤
│ 工具条：[输入设备▾][采样率▾][FFT▾][窗函数▾] │ ● 开始 ‖ 暂停 ■ 停止   │
├──────────────────────────────────────────────────────────────────────┤
│ ① 波形时序图（pyqtgraph PlotWidget，滚动窗口）                        │
├──────────────────────────────┬───────────────────────────────────────┤
│ ② 频谱图（当前帧，dBFS）      │ ③ 主频时序图（对数频率轴 + 置信度）    │
├──────────────────────────────┴───────────────────────────────────────┤
│ ④ 钢琴卷帘（仅 MIDI 文件时显示，其余情况隐藏）                        │
├──────────────────────────────────────┬───────────────────────────────┤
│ ⑤ 日志/工作状态（只读，自动滚动）      │ ⑥ 主频时序数据（v0.5 新增）    │
│                                       │   时间/s 频率/Hz 音名 置信度  │
│                                       │   约占总宽度 1/3，最多 2000 帧 │
├──────────────────────────────────────┴───────────────────────────────┤
│ ⑦ 进度条 + 时间标签                                                   │
└──────────────────────────────────────────────────────────────────────┘
```

### 7.2 控件清单

| 区域 | 控件 | 说明 |
| --- | --- | --- |
| 工具条 | 输入设备下拉框 | 来自 `sounddevice.query_devices()`，默认系统默认输入 |
| 工具条 | 采样率下拉框 | 44100 / 48000 |
| 工具条 | FFT 长度下拉框 | 1024 / 2048 / 4096（默认 4096） |
| 工具条 | 窗函数下拉框 | Hann（默认）/ Hamming / Blackman |
| 工具条 | 主频算法下拉框 | P1（默认）/ P2 / P3 |
| 工具条 | 开始 / 暂停 / 停止 按钮 | 按状态机置灰（见 5.3） |
| 工具条 | 录制 / 打开文件 按钮 | 打开文件过滤器：`音频 (*.wav *.mp3 *.flac *.ogg *.m4a *.aac);;MIDI (*.mid *.midi);;全部文件 (*)` |
| 波形区 | 自动量程 / 时间窗选择 | 0.1–2 s；自动量程关闭时固定满量程 ±1.05 |
| 频谱区 | 线性/对数频率轴、峰值标注 | 峰值显示"频率 + dBFS" |
| 主频区 | 时间窗选择、平滑开关 | 默认 10 s，可选 5/10/30/60 s；标题显示"主频 + 音名（含音分）"（v0.5） |
| 主频区（M5） | 符号数据模式 | 打开 MIDI 时复用该区域画**音符频率曲线**（每个音符一段等频横线），横轴改为 0…曲长，标题标注"符号数据…不是声学主频"；有声学帧进来即自动退回声学模式（FR-4.4） |
| 数据区（v0.5） | 主频时序数据（只读等宽文本） | 逐帧显示 `时间/s 频率/Hz 音名+音分 置信度`；最多保留最近 2000 帧，超出丢弃最旧；低频段音名带 `*` |
| 数据区（v0.5） | 分区标签 | "主频时序数据（* 低频段 < 250 Hz 音名仅供参考）"；最小宽度 260 px（NFR-8） |
| 卷帘区（M5） | 钢琴卷帘（pyqtgraph，只读） | **仅打开 MIDI 时显示**（其余情况隐藏且不占高度）；横轴时间、纵轴音高，左轴按音名标注刻度；一根横条 = 一个音符，条长 = 音符时长（FR-4.2） |
| 底部 | 进度条 + `当前/总时长` 标签 | 播放可点击定位；录制显示不确定态 + 已录时长 |
| 状态栏 | 当前状态、采样率、电平/主频（含音名）、缓冲 overrun 与播放欠载 | overrun/欠载 > 0 时黄色告警 |
| 菜单栏 | 文件（打开/监听/录制/停止/退出）、视图（频谱对数轴、主频中值滤波、恢复默认设置）、帮助（关于） | 快捷键：`Ctrl+O` 打开、`F5` 开始、`F6` 录制、`F7` 暂停/继续、`F8` 停止 |

绘图约束：`pyqtgraph` 的 `setData` 增量更新；曲线对象复用不重建；`setClipToView(True)` + `setDownsampling(auto=True)`；禁止在定时器里 `clear()` 后重建图元。频谱/主频刷新固定 16 Hz，界面重绘 30–60 Hz。

### 7.3 日志区显示内容

| 场景 | 必显字段 |
| --- | --- |
| 启动 | 采样率、FFT、窗、主频算法、跳步；频率分辨率 Δf；**音名口径（A4 = 440 Hz）**（v0.5） |
| 录制开始 | 开始时间（`YYYY-MM-DD HH:MM:SS.mmm`）、设备名、采样率、声道 |
| 录制结束 | 结束时间、总时长、保存路径（绝对路径）、文件大小、**主频时序文件路径与行数**（v0.5；未生成时给出原因） |
| 播放文件 | 文件路径、总时长、采样率、声道、格式 |
| 播放过程 | 当前进度（`mm:ss.S / mm:ss.S`，百分比） |
| 异常 | 异常类型、简述、可操作建议（如"未找到 ffmpeg，请在设置中指定路径"） |

---

## 八、验收标准

| 编号 | 场景 | 通过判据 |
| --- | --- | --- |
| A1 | 1 kHz 正弦 wav，N=4096，fs=48 kHz，Hann | 频谱峰值 bin 与 1000 Hz 偏差 ≤ 1 bin（11.7 Hz）；抛物线插值后 ≤ 2 Hz |
| A2 | 同上，观察主频时序图 10 s | 读数 1000 ± 5 Hz，标准差 < 5 Hz，无明显倍频跳变 |
| A3 | 麦克风说话 | 波形随声音变化；频谱形态随元音改变；GUI 无卡顿（拖动窗口流畅） |
| A4 | 录制 10 s 再回放 | wav 可读、时长误差 < 50 ms、内容与录制时视图一致 ✅ 实测通过 |
| A5 | 播放 mp3 | 有声音输出，进度显示与实际偏差 < 200 ms，视图同步 ✅ 实测 43 ms（低延迟输出设备）；⚠️ 与输出设备强相关：MME / 蓝牙默认输出下实测 212–314 ms，见 2.1 环境坑与附录 C |
| A6 | 静音输入 | 主频视图不画点（或断开），无随机虚假频率 ✅ 实测通过 |
| A7 | 打开 MIDI | 钢琴卷帘音符起止正确，C4 显示 261.63 Hz ✅ **实测通过**：`--midi-check` 对 28842 个音符（four-seasons）与 471 个音符（canon）均与 `mido` 的 `note_on` 条数一致；canon 解析时长 **127.195 s**，与 `sync.json` 记录的 127.2 s 吻合；C4 = 261.63 Hz（v0.5 起 `core/notes.py` 已用同一公式） |
| A8 | 故障注入（拔设备 / 打开损坏文件 / 隐藏 ffmpeg） | 中文提示、程序不崩溃、可回到 IDLE 继续操作 ✅ 实测通过 |
| A9 | 连续运行 30 min | 内存增长 < 50 MB，无崩溃，日志完整 ⬜ M6 |
| A10 | 1 kHz 正弦（合成源/文件均可） | 状态栏、主频图标题、数据区同时给出音名 `B5`，音分 `+21.3 ± 0.5`；静音时音名为 `—`（v0.5） |
| A11 | 录制 10 s 后检查 `records/` | 与 wav 同名的 `.csv` 存在；列头 = 4.7 规定；数据行数 = 有效主频帧数（≈16×时长×有效率，偏差 ≤ 10%）；首行 `t_s < 1.0`；UTF-8 BOM 可被 Excel/pandas 直接读取（v0.5） |
| A12 | 导出失败注入（records 不可写 / 磁盘满） | 中文提示 + 日志，wav 仍完整可读，程序回到 IDLE（v0.5） |

测试信号生成（供 A1/A2 使用）：

```bat
ffmpeg -hide_banner -f lavfi -i "sine=frequency=1000:duration=10:sample_rate=48000" ^
       -ac 1 -c:a pcm_s16le tests/data/sine_1k_48k.wav
```

---

## 九、实施计划（里程碑）

| 里程碑 | 内容 | 交付物 | 验收 | 预估 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M0 | 环境与骨架 | 依赖安装、目录结构、`frames.py`、日志与配置 | `pytest` 空跑通过，程序能起空窗口 | 0.5 d | ✅ 完成 |
| M1 | 最小原型 | 麦克风采集 → 波形 + 频谱（`sounddevice + numpy + pyqtgraph`） | A1、A3（主频类验收在 M2 完成） | 1–2 d | ✅ 完成 |
| M2 | 主频 | `pitch.py`（P1/P2/P3）+ 主频时序图 + 轨迹平滑 | A1、A2、A6 | 1–2 d | ✅ 完成 |
| M3 | 文件与录制 | ffmpeg 解码、文件准实时源、播放线程、录制线程 | A4、A5 | 2 d | ✅ 完成 |
| M4 | GUI 完善 | 控件清单全量、状态机、日志区、进度条、配置持久化 | 走查 + A8 | 1–2 d | ✅ 完成 |
| M4.1 | **v0.5 增量**：主频音名、日志右侧数据区、录制导出主频时序 CSV | `core/notes.py`、`core/pitch_series.py`、`gui/pitch_data_view.py`、布局与日志字段 | A10、A11、A12 | 0.5–1 d | ✅ 完成 |
| M5 | MIDI 符号分析 | `media/midi_loader.py` + `media/midi_render.py`（接口）+ `gui/pianoroll_view.py` + 主频区符号曲线 | A7 | 1 d | ✅ 完成 |
| M6 | 测试与打包 | 单测覆盖、长时间运行、PyInstaller 打包 | NFR-6、NFR-7、A9 | 1–2 d | ⬜ 待开始 |
| M7（可选） | MIDI → 音频 | FluidSynth + SoundFont 渲染后进入统一管线 | 波形/频谱在主频上与声学分析一致 | 1–2 d | ⬜ 可选 |

原文档"第 2 步"的验收项"播放 1 kHz 正弦波，频谱峰值在 1000 Hz 附近"已量化为 A1 的容差，避免"附近"不可判定。

**实施进度（2026-10-05）**：**M0–M4 已完成并通过验收**（244 项单元测试全绿），实测数据见附录 C；
**M4.1（v0.5 变更）已完成**（音名 / 数据区 / 录制导出 CSV，见附录 C 的 v0.5 记录）；
**M5（MIDI 符号分析）已完成**（333 项单元测试全绿，见附录 C 的 M5 记录）；M6（覆盖率与打包）待开发。

**复测进度（2026-10-08）**：环境升级到 Python 3.14.7 / PySide6 6.12.0 后**代码未改动**，
333 项单测全绿，GUI / 采集 / 录制 + CSV / MIDI 自检全部通过；唯一失败项为 A5 进度偏差，
已定位为输出设备（MME / 蓝牙）属性，详见附录 C 末尾的复测记录。

---

## 十、风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| ffmpeg 为外部依赖，用户环境可能缺失 | mp3 无法播放 | 启动时探测 `C:\ffmpeg\bin\ffmpeg.exe` 与 PATH；缺失时给出中文指引并允许在设置中指定路径 |
| `librosa` 体积大、依赖冲突 | 安装失败、打包臃肿 | 降级为可选依赖，基础功能零依赖 |
| `pretty_midi` 在 Python 3.14 上兼容性未知 | MIDI 解析受阻 | 首选纯 `mido` 实现，`pretty_midi` 仅作增强 |
| Δf=11.7 Hz 粗于主频精度要求 | 主频不准 | 谱线抛物线插值 + HPS/YIN 兜底；必要时补零 |
| 复音/噪声下主频不可信 | 误导用户 | 置信度着色 + 静音门限 + 界面标注算法档位；范围外见 N3 |
| 音频设备独占、采样率不被支持 | 打开失败 | 打开前用 `check_input_settings` 校验，失败则回退设备默认采样率并提示 |
| GUI 卡顿 | 体验差 | 分层 + 有界队列 + pyqtgraph 增量更新；分析线程与 GUI 解耦 |
| PyInstaller 漏打包 PortAudio / Qt 插件 | 打包后无法运行 | 使用 `pyinstaller-hooks-contrib`，`--collect-all sounddevice`，干净虚拟机验证 |
| 长时间录制文件过大 | 磁盘写满 | 显示剩余空间；可选按大小/时长自动分段（后续版本） |

---

## 十一、待确认事项

> v0.5 已关闭原第 1 条（主频序列导出）：主频时序现在随录制导出 CSV（FR-1.8）。
> 频谱序列导出与视图截图导出仍未列入本期。

1. 立体声是否需要**分声道独立分析**（如比较左右声道主频）？当前设计为降混单声道。
2. 是否存在**复音**主频需求（和弦、多人说话）？若有，需单列"复音基频/多音高估计"需求（当前见 N3）。
3. 目标打包机的 Windows 版本与是否允许附带 ffmpeg 二进制？
4. 是否需要**长时间录制自动分段**（按时长或文件大小）？
5. 主频时序是否需要**边录边写**（避免超长录制时内存中保留全量点，当前上限 200000 点）？

---

## 附录 A：与 v0.1 需求草案的差异对照

| v0.1 内容 | v0.2 处理 |
| --- | --- |
| 范围只提 wav/mp3，实施路径却出现 mp4 | 范围明确为 wav/mp3 + MIDI 符号分析；mp4 归入 N2（尽力而为，不验收） |
| `AudioFrame` 仅 4 行字段 | 补充 dtype/形状/值域/声道/时间戳/块大小/末块约定（4.1） |
| "1/16 秒刷新"未定义窗长与分辨率 | 给出 hop/窗长默认值与 Δf 对照表（4.3） |
| "主频率"未定义算法 | 新增 4.4 专章，区分主频与基频，给出三档算法与默认值 |
| "问题：midi 文件怎么处理"无结论 | 决策：本期符号分析（3.4），渲染列为 M7 |
| 依赖清单混入 `librosa`、`pretty_midi`、`FluidSynth` | 分层为"核心/首选/可选"，并标注兼容性风险 |
| 无状态机、无异常处理、无性能指标 | 新增 5.3、第六章、第十章 |
| 验收标准口语化（"附近""不卡顿"） | 全部量化为第八章数值判据 |
| 未记录环境事实 | 新增第二章（含默认 `python` 命令为应用商店占位符的实测记录） |

## 附录 B：常用命令备忘

```powershell
# 用基线环境运行（注意：直接敲 python 会命中应用商店占位符）
C:\miniconda3\envs\ibase\python.exe main.py

# 安装本期依赖
C:\miniconda3\envs\ibase\python.exe -m pip install sounddevice soundfile mido pytest pytest-qt pyinstaller pyinstaller-hooks-contrib

# 探测文件信息
C:\ffmpeg\bin\ffprobe.exe -v error -show_entries format=duration:stream=sample_rate,channels,codec_name -of json "test.mp3"

# 解码为裸 PCM（分析/播放共用）
C:\ffmpeg\bin\ffmpeg.exe -hide_banner -v error -nostdin -i "test.mp3" -f f32le -acodec pcm_f32le -ac 1 -ar 48000 pipe:1

# 跑测试（GUI 用例需离屏）
$env:QT_QPA_PLATFORM = "offscreen"; C:\miniconda3\envs\ibase\python.exe -m pytest -q

# M0–M4 已实现的自检入口
C:\miniconda3\envs\ibase\python.exe main.py --list-devices          # 枚举输入设备
C:\miniconda3\envs\ibase\python.exe main.py --demo-tone 1000        # 合成正弦替代麦克风（可视验收 A1）
C:\miniconda3\envs\ibase\python.exe main.py --audio-check 3         # 无界面端到端自检（A1/A2）
C:\miniconda3\envs\ibase\python.exe main.py --file-check a.mp3      # 文件播放自检（A5：欠载/进度）
C:\miniconda3\envs\ibase\python.exe main.py --self-test             # 离屏起窗口（M0 验收）
C:\miniconda3\envs\ibase\python.exe main.py --self-test --demo-tone 1000 --self-test-shot images/shot.png
```

## 附录 C：M0–M5 实施记录（实测）

环境：`C:\miniconda3\envs\ibase\python.exe`（Python 3.13.16，2026-10-08 已升级为 3.14.7）、ffmpeg `N-122544-g8966101fa6-20260125`。
下表为 2026-10-05 在 Python 3.13.16 / PySide6 6.9.3 上的原始记录；新环境的复测见本节末尾。

| 验收项 | 命令 / 用例 | 实测结果 |
| --- | --- | --- |
| 单元测试 | `python -m pytest` | **244 passed** |
| M0 起窗口 | `python main.py --self-test` | exit 0，主窗口存活 800 ms |
| M1 合成端到端 | `python main.py --audio-check 2` | 30/31 帧；主频 **1000.18 Hz**（偏差 0.18 Hz）；overrun 0 |
| M1 真实麦克风 | `python main.py --audio-check 2 --source device` | 29/31 帧；环境噪声 RMS −52 dBFS；overrun 0 |
| A1 谱峰精度 | `tests/test_analyzer.py::test_a1_*` | 裸 bin 偏差 3.9 Hz ≤ 1 bin（11.72 Hz）；插值后 < 1 Hz |
| A1 幅度定标 | `test_bin_centered_tone_has_correct_dbfs` | 幅度 0.5 的正弦峰值 −6.02 dBFS |
| A2 主频稳定度 | `tests/test_controller.py::test_start_and_stop_cycle_produces_snapshots` | 中位数 1000.2 Hz、标准差 < 5 Hz |
| A6 静音门限 | `tests/test_analyzer.py::test_a6_silence_produces_no_pitch` | 静音时不输出主频，频谱仍输出 |
| M2 P2 谐波积谱 | `tests/test_pitch.py::test_hps_*` | 二次谐波比基频高 14 dB 时，P1 报 400 Hz、P2 正确还原 **200 Hz** |
| M2 P3 YIN | `tests/test_pitch.py::test_yin_*` | 220/200/80/60 Hz 误差 < 0.25 Hz；噪声置信度 0.05；单帧 0.27 ms |
| M3 文件播放（wav） | `python main.py --file-check tone.wav --file-check-seconds 3` | 44 帧；**0 次欠载**；进度偏差 **23–27 ms**；主频 1000.18 Hz |
| M3 文件播放（mp3） | `python main.py --file-check tone.mp3 --file-check-seconds 3` | 47 帧；**0 次欠载**；进度偏差 **43 ms**；主频 439.92 Hz（真实声卡出声） |
| A4 录制 | `tests/test_controller_media.py::test_recording_*` | 文件时长与录制区间偏差 < 50 ms；内容主频 1000 Hz 与视图一致 |
| A5 进度 | `test_playback_uses_file_source_and_player` | 进度与播放位置偏差 < 200 ms |
| A8 故障注入 | `test_gui_media.py::test_*_reported*`、`test_decoder.py::test_open_decoder_reports_missing_ffmpeg` | 文件缺失/损坏、MIDI、目录不可写、ffmpeg 缺失均给出中文提示且回到 IDLE |
| FR-6.3 恢复默认 | `test_gui_media.py::test_restore_defaults_*` | 配置/下拉框/视图/分析器全部复位，运行中会先停止 |
| 界面取证 | `--self-test --demo-tone 1000 --self-test-shot images/m4_gui_windows.png` | 见 `README.md` 截图 |

**v0.5（M4.1）实测记录**：

| 验收项 | 命令 / 用例 | 实测结果 |
| --- | --- | --- |
| 全量回归 | `python -m pytest` | **286 passed**（M0–M4 的 244 项 + v0.5 新增 42 项） |
| A10 音名换算 | `tests/test_notes.py` | A4→`A4 +0.0¢`；1 kHz→`B5 +21.3¢`；`note_frequency(60)`=261.63 Hz（FR-4.3 一致）；<250 Hz 标 `low_band` |
| A10 界面同步 | `tests/test_gui_smoke.py::test_pitch_data_view_shows_frequency_and_note` | 1 kHz 合成源下数据区、状态栏、主频图标题同时出现 `B5` |
| A11 序列收集 | `tests/test_pitch_series.py` | 静音帧不产生行；3 点中值口径正确；`record_full` 仅录制保留全量；超上限截断计数 |
| A11 CSV 导出 | `tests/test_controller_media.py::test_recording_exports_pitch_series_csv` | 与 wav 同 stem 的 CSV 生成；列头符合 4.7；全部有效帧音名 `B5`；首行 `t_s < 1 s`（录制相对时间） |
| A11 非录制不导出 | `test_monitoring_does_not_export_pitch_series` | 监听运行后有序列点、无 CSV、`consume_pitch_export()` 为空 |
| A11 静音录制 | `test_silent_recording_creates_no_pitch_csv` | wav 正常，无 CSV，导出结果 `path=None, error=None` |
| A12 导出失败注入 | `test_pitch_export_failure_keeps_wav_and_reports` | 抛出 `OSError` 时 wav 完整、导出结果带原因、状态回到 IDLE |
| 布局取证 | `--self-test --demo-tone 1000 --self-test-shot images/m4_gui_windows.png` | 日志右侧出现数据区，含 `B5 +21.3¢` 行与 `*` 低频说明 |

**v0.5（M5）实测记录**：

| 验收项 | 命令 / 用例 | 实测结果 |
| --- | --- | --- |
| 全量回归 | `python -m pytest` | **333 passed**（v0.5 的 286 项 + M5 新增 47 项） |
| A7 自检（演示文件） | `python main.py --midi-check build/demo_midi.mid` | 17 个音符；`mido note_on` 17 个；音域 60–74；C4 = **261.63 Hz**；PASS |
| A7 自检（真实文件） | `--midi-check .../the-four-seasons-complete.mid` | **28842 个音符**与 `mido note_on` 28842 一致；type 1 / 6 轨 / 110.0 BPM；PASS |
| A7 时长交叉验证 | `--midi-check .../canon-in-d-easy.mid` | 解析时长 **127.195 s**，与同套件 `sync.json` 的 127.2 s 一致（独立实现的 tempo 换算互相印证） |
| FR-4.1 解析口径 | `tests/test_midi_loader.py`（21 项） | tempo 变化后 tick→秒 斜率正确（0/0.5/0.75 s）；`velocity=0` 视为松键；同音高重叠按队列配对；轨末未闭合不丢；type 2 逐轨独立 |
| FR-4.2 卷帘几何 | `tests/test_gui_midi.py::test_piano_roll_bars_match_note_times` | `BarGraphItem` 的 `x0` = `[0,1,2,3]`、`width` = 0.5 ×4、`y` = 音符号，与 MIDI 一致 |
| FR-4.3 频率曲线 | `test_symbolic_curve_frequency_uses_fr43_formula` | 曲线纵轴为 log10(频率)，端点 = log10(261.63) / log10(523.25) |
| FR-4.4 口径标识 | `test_symbolic_curve_is_labelled_and_hides_acoustics` | 标题含"符号数据"与"不是声学主频"；声学曲线数据清空、符号曲线有数据；有声学帧进来自动退回声学模式 |
| 仅 MIDI 时显示 | `test_open_audio_file_hides_piano_roll` | 打开音频后卷帘隐藏、`splitter` 该格高度归 0、MIDI 文档清空 |
| FR-4.5 接口预留 | `test_midi_renderer_is_reserved_interface` | `MidiRenderer.available is False`；`render()` 抛 `NotImplementedError` 且带中文说明 |
| A8 同类（坏 MIDI） | `test_corrupt_midi_file_is_reported` | 中文提示 + 留在 IDLE + 可继续操作 |
| 界面取证 | `--self-test --demo-midi --self-test-shot images/m5_gui_midi.png` | 见 `README.md` 截图（区域④为卷帘，区域③为符号频率曲线） |

**M5 新增差异（均为实现期新增，需要评审确认）**：

| 差异 | 原因 |
| --- | --- |
| 新增 `media/midi_loader.py` | FR-4.1 的解析与"与 mido 消息数一致"的验收需要一个可单测的纯模块（放在 `media/`，与规格书 5.1 的目录规划一致） |
| 新增 `media/midi_render.py` | FR-4.5 要求接口先存在；放在 M7 的实现位置旁边，签名一次定好 |
| 新增 `gui/pianoroll_view.py` | FR-4.2；用 `BarGraphItem`（一根条 = 一个音符），左轴直接标音名 |
| `PitchView` 增加符号模式（非新视图） | FR-4.4 要求"复用主频时序图区域"；两种模式互斥并在标题上明确标识，避免与声学主频混淆 |
| 新增 `--midi-check` / `--demo-midi` / `--self-test-midi` | 把 A7 从"人工看一眼"变成可重复执行的命令；`--demo-midi` 与 `--demo-tone` 同思路（不依赖外部素材） |
| MIDI 不进状态机，只作"当前文档"挂在控制器上 | MIDI 没有音频、不可播放；任何一次真实采集/播放都会自动清掉它，保证"卷帘只在 MIDI 时出现" |

**实现相对本规格书的差异（均为实现期新增，需要评审确认）**：

| 差异 | 原因 |
| --- | --- |
| 新增 `MONITORING` 状态 | M1 无录制/播放能力，但需要可开始/停止的实时分析（见 5.3） |
| 新增 `sources/synthetic_source.py` | 无麦克风环境（CI/离线）验证 A1、A6 与端到端链路 |
| 新增 `core/pitch_track.py` | 4.4 的"3 点中值滤波 + 跳变标记"需要一个可单测的纯计算模块 |
| 对数坐标轴自绘（`gui/log_axis.py`） | pyqtgraph 0.14 的 `setLogMode` 只映射曲线、不映射散点/标线，混用会画错 |
| 播放预填与节拍回拨（4.5） | 不修正会导致起播欠载（实测 9 → 0 次） |
| `--audio-check` / `--file-check` / `--self-test` / `--demo-tone` / `--self-test-shot` | 把验收判据变成可重复执行的命令，避免"人工看一眼" |
| 控制器支持注入 `source_factory` / `player_factory` | 无声卡环境也能测试播放链路（`tests/fakes.py`） |
| `pytest.ini` 关闭 `tmpdir` / `cacheprovider` 插件 | 受限开发环境下系统临时目录不可写、`.pytest_cache` 重命名被拒；改用 `tests/conftest.py` 的 `work_dir` 夹具 |

**v0.5（M4.1）新增差异（均为实现期新增，需要评审确认）**：

| 差异 | 原因 |
| --- | --- |
| 新增 `core/notes.py` | 需求 1 要求输出音名；与 M5 的 MIDI 频率公式同源，避免两套换算 |
| 新增 `core/pitch_series.py` | 需求 2/3 要求界面显示与 CSV 导出同一份数据；用分析线程单写序列，避免 GUI 丢帧导致导出缺行 |
| CSV 在 `AudioController.stop()` 内同步写 | 需求 3 要求"录制结束后"保存，此时分析线程已停、写入为毫秒级（已在 5.2 记为约束 1 的例外） |
| 数据区上限 2000 行、序列上限 200000 点 | 防止界面与内存无界增长（NFR-3）；超出上限时截断并在日志/导出结果中说明 |
| 音名的低频"仅供参考"标注 | Δf 粗于低音区半音间距，孤立音名会误导；改为"音名 + 音分 + 置信度"组合展示 |
| 音名随"主频中值滤波"开关切换口径 | 界面上同一时刻只应有一个主频读数，避免图上与数据区音名不一致 |

**新运行环境复测记录（2026-10-08，Python 3.14.7 / PySide6 6.12.0）**：

环境：`C:\miniconda3\envs\ibase\python.exe` = Python 3.14.7（Anaconda 打包版）、PySide6 6.12.0、
pyqtgraph 0.14.0、numpy 2.5.3、scipy 1.18.1、sounddevice 0.5.6、soundfile 0.14.0、mido 1.3.3、
pytest 9.1.1、ffmpeg `N-122544-g8966101fa6-20260125`。**源代码未做任何修改**（`git status` 干净），
本节为纯复测。

| 验收项 | 命令 / 用例 | 实测结果 |
| --- | --- | --- |
| 全量回归 | `python -m pytest`（`QT_QPA_PLATFORM=offscreen`） | **333 passed in 21.7 s**，无警告转错误 |
| M0 起窗口（离屏） | `python main.py --self-test --self-test-ms 800` | exit 0；主窗口创建并存活 |
| M0 起窗口（真实桌面） | `python main.py --demo-tone 1000` | 窗口标题 `zpyaudio 音频分析`，`Responding=True`，工作集 248 MB，45 线程；运行 7 s 后正常终止 |
| M1 合成端到端 | `python main.py --audio-check 3` | 46/47 帧；主频 **1000.18 Hz**（偏差 0.18 Hz）；裸 bin 996.09 Hz；RMS −9.0 dBFS；overrun 0 → **PASS** |
| M1 真实麦克风 | `python main.py --audio-check 3 --source device` | 45/47 帧；RMS −54.0 dBFS；overrun 0；主频 119.4 Hz `A#2 +42.3¢`（带低频"仅供参考"标注）→ **PASS** |
| 设备枚举 | `python main.py --list-devices` | 16 个输入设备正常枚举 |
| A7 MIDI（演示） | `python main.py --midi-check build/demo_midi.mid` | 17 个音符 == `mido note_on` 17；C4 = 261.63 Hz；type 1 / 1 轨 / 120 BPM → **PASS** |
| A4 + A11 录制导出 | 控制器录制 3 s / 10 s（合成源 / 真实设备），校验 wav + CSV | 合成源 3 s：wav **3.008 s**（与墙钟偏差 8 ms）、CSV 47 行；设备 3 s：wav **2.923 s**、CSV 36 行；设备 10 s：wav **9.899 s**、CSV 135 行（设备录制的 ~0.08–0.10 s 差为输入流启动延时，非速率误差）。CSV 列头与 4.7 一致、UTF-8 BOM、首行 `t_s < 1 s` → **PASS** |
| A1/A2/A10 界面取证 | `--self-test --demo-tone 1000 --self-test-shot` | 波形（±0.5）、频谱单峰、对数轴主频 1 kHz 直线、右下数据区与进度条均按预期绘制；截图 1528×780，与既有 `images/m4_gui_windows.png` 肉眼一致（32994 vs 32903 字节） |
| FR-4.2/4.3 界面取证 | `--self-test --demo-midi --self-test-shot` | 钢琴卷帘（音阶阶梯 + 三层和弦）+ 红色符号频率曲线正常，与既有 `images/m5_gui_midi.png` 一致 |
| A5 文件播放 | `python main.py --file-check <wav/mp3> --file-check-seconds 4` | ❌ **FAIL**：进度偏差 212 / 215 / 314 ms（判据 < 200 ms）；`playback_underruns` 仍为 0，但每次起播记 1 次 PortAudio `output underflow` 状态告警。根因与输出设备有关，见下 |

> 截图说明：离屏（`offscreen`）平台下 Qt 取不到字体库，图上文字一律渲染成方块——这是在旧环境
> 与仓库中既有截图里都存在的现象，不是本次升级引入的问题；界面文字内容的正确性由
> `tests/test_gui_smoke.py` / `tests/test_gui_midi.py` 的断言保证。

**A5 偏差的根因定位（诊断脚本 `build/probe_playback.py`，未进仓库）**：
`--file-check` 用「已送出声卡的帧数 ÷ 采样率」（`Player.position_seconds`）对比墙钟，
两者之差 ≈ 输出队列预填（`capacity − 2` 块 = 0.13 s）− 队列实际积压 + 输出缓冲延迟。
在当前机器上把同一文件送不同输出设备实测（源位置与播放位置的稳态差值）：

| 输出设备（host API） | 流延迟 `stream.latency` | 播放位置 − 墙钟 | 源侧丢块 |
| --- | --- | --- | --- |
| 5 MME「耳机 (HUAWEI FreeBuds SE 2)」（**系统默认**） | 0.1067 s | **−0.21 ~ −0.35 s** | 10–16 块 |
| 16 WASAPI「麦克风阵列 (网易虚拟音频设备)」 | 0.0427 s | **+0.024 ~ +0.039 s** | 0 |
| 17 WASAPI「耳机 (HUAWEI FreeBuds SE 2)」 | 0.0427 s | **+0.012 ~ +0.029 s** | 0 |
| 23 WDM-KS「Speakers (Realtek)」 | 0.0100 s | **+0.012 ~ +0.037 s** | 0 |

结论：**A5 的偏差读数由输出设备/主机 API 决定，而不是由 Python 3.14 / PySide6 6.12 引起**
（同一份未改动的代码在 WASAPI / WDM-KS 上为 12–43 ms，与 2026-10-05 记录的 23–43 ms 吻合）。
当前机器的系统默认输出是 MME 上的蓝牙耳机：MME 缓冲大（0.09–0.18 s）、消费速率比实时约慢 5%
（表现为源侧持续丢块、积压增长），因此偏差随播放时长增大并越过 200 ms 判据。

**建议（不属本期代码变更，待评审）**：

1. `Player` 允许指定输出设备/主机 API，或默认优先 WASAPI（当前固定用 PortAudio 的默认设备，Windows 上即 MME）；
2. `--file-check` 把 A5 判据改为与「输出流延迟」比较（或允许按设备延迟放宽阈值），避免把设备属性判成回归；
3. 起播的 1 次 `output underflow` 状态告警可忽略（`_underruns` 计数为 0，未真正输出静音），
   或在日志里降级为 DEBUG 并注明"仅起播首帧"。

