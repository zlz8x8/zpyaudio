# zpyaudio

音频分析软件：对麦克风或音频文件做**实时**分析，显示波形时序图、1/16 秒刷新的频谱图与主频率时序图，
并在主频读数旁给出**音名**；录制结束后把本次主频时序导出为 CSV。
打开 **MIDI** 时进入**符号分析**：显示钢琴卷帘，并在主频区域画出音符频率曲线。

Audio analysis software: performs **real-time** analysis of a microphone or audio file, displaying a waveform timing diagram, a spectrum diagram refreshed every 1/16 second, and a dominant-frequency timing diagram, and showing the **note name** next to the dominant-frequency readout; when recording ends, it exports this session's dominant-frequency time series as CSV.
When **MIDI** is enabled, it switches to **symbolic analysis**: it displays a piano roll and draws the note-frequency curves in the dominant-frequency area.

需求规格书：`docs/requirements.md`（v0.5.1）。

## 当前进度

| 里程碑 | 内容 | 状态 | 验收 |
| --- | --- | --- | --- |
| M0 | 环境与骨架：目录结构、配置、日志、CLI、测试骨架 | ✅ 完成 | `pytest` 全绿、`--self-test` 能起窗口 |
| M1 | 最小原型：麦克风采集 → 波形 + 频谱 + 主频 P1 | ✅ 完成 | A1、A2、A3 |
| M2 | 主频 P2（谐波积谱）/ P3（YIN）+ 主频时序图 | ✅ 完成 | A1、A2、A6 |
| M3 | wav/mp3 解码与播放、录制写盘、暂停/继续/拖动 | ✅ 完成 | A4、A5 |
| M4 | GUI 控件全量、菜单、进度条、恢复默认设置、异常提示 | ✅ 完成 | A8、FR-6.3 |
| M4.1 | **v0.5 变更**：主频音名、日志右侧数据区、录制导出主频时序 CSV | ✅ 完成 | A10、A11、A12 |
| M5 | **MIDI 符号分析**：解析 → 钢琴卷帘 + 音符频率曲线 | ✅ 完成 | A7 |
| M5.1 | **ffmpeg 路径可配置 + 启动自动检测**（可在设置中覆盖） | ✅ 完成 | A13 |
| M6 | 覆盖率补强、PyInstaller 打包 | 🟡 PyInstaller 便携版打包已完成（见「打包」节），覆盖率与 A9 待做 | NFR-6/7、A9 |

![界面](images/m4_gui_windows.png)

由 `python main.py --self-test --demo-tone 1000 --self-test-shot images/m4_gui_windows.png` 生成（1 kHz 合成正弦）：
波形幅度 ±0.5、频谱峰值 996.1 Hz（裸 bin）/ 主频 1000.2 Hz（插值后）、主频时序图为对数频率轴上的 1 kHz 直线；
底部左侧为日志区、**右侧为 v0.5 新增的主频时序数据区（约 1/3 宽，逐帧显示 时间/频率/音名/置信度）**，
菜单栏、视图选项、进度条与状态栏读数（含音名 `B5`）一致。

![MIDI 符号分析](images/m5_gui_midi.png)

M5：由 `python main.py --self-test --demo-midi --self-test-shot images/m5_gui_midi.png` 生成（演示 MIDI：
C 大调音阶 + C/F/G 三个和弦）。**区域④为钢琴卷帘**（横轴时间、纵轴音高、左轴直接标音名：音阶是一串阶梯，
和弦是三层叠起来的横条），**区域③由声学主频切换为「符号数据」**——红色阶梯是按
`f = 440×2^((n-69)/12)` 画出的音符频率曲线，标题明确写着"不是声学主频"；
右下数据区为空（它是声学口径的 时间/频率/音名/置信度，MIDI 没有声学帧）。

## 环境

| 项 | 值 |
| --- | --- |
| 解释器 | `C:\miniconda3\envs\ibase\python.exe`（Python 3.14.7，Anaconda 打包版） |
| 依赖 | 见 `requirements.txt`（numpy 2.5.3 / scipy 1.18.1 / PySide6 6.12.0 / pyqtgraph 0.14.0 / sounddevice 0.5.6 / soundfile 0.14.0 / mido 1.3.3） |
| 外部程序 | ffmpeg（mp3 等有损格式解码；缺失时给出中文指引，wav 不受影响）。**路径启动时自动检测**：环境变量 `ZPYAUDIO_FFMPEG_DIR`/`FFMPEG_DIR`/`FFMPEG_HOME` → `C:\ffmpeg\bin` → 常见安装位置（winget/chocolatey/scoop/Program Files）→ `PATH`；检测到即写回 `config.json`，也可在「文件 → 设置 ffmpeg 路径…」中指定并覆盖 |

> ⚠️ 直接执行 `python` 会命中 Windows 应用商店占位符，请使用上面的绝对路径，或先 `conda activate ibase`。

> ⚠️ **播放进度判据与输出设备相关**：`--file-check` 的 A5 判据（进度偏差 < 200 ms）用的是"已送出声卡 vs 墙钟"的
> 差值，其大小约等于输出队列预填 + 输出设备缓冲延迟。用低延迟主机 API（WASAPI / WDM-KS，缓冲 3–43 ms）时实测偏差
> **12–43 ms**；若系统默认输出落在 **MME**（缓冲约 0.09–0.18 s，且消费速率偏慢）或蓝牙耳机上，实测偏差会到
> **0.21–0.36 s** 从而判定 FAIL。这是设备/主机 API 的属性，不是程序回归——换默认输出设备或指定低延迟设备即可。
> 详见 `docs/requirements.md` 附录 C 的「新运行环境复测记录」。

## 运行

```powershell
$py = "C:\miniconda3\envs\ibase\python.exe"

# 正常启动（选择输入设备后点「开始」监听；「录制」边录边分析；「打开文件」播放并分析）
& $py main.py

# 无麦克风时：用 1 kHz 合成正弦验证波形/频谱/主频（验收 A1）
& $py main.py --demo-tone 1000

# 列出可用输入设备
& $py main.py --list-devices

# 无界面端到端自检（合成 1 kHz，判定主频偏差 ≤ 2 Hz）
& $py main.py --audio-check 3
& $py main.py --audio-check 3 --source device     # 用真实麦克风

# 文件播放自检（A5：真实声卡输出 + 进度偏差 + 播放欠载）
& $py main.py --file-check path\to\music.mp3 --file-check-seconds 5

# MIDI 符号分析自检（A7：钢琴卷帘数据 + C4 频率 + 与 mido 的消息数一致性）
& $py main.py --midi-check path\to\song.mid

# 打开一段内置演示 MIDI（C 大调音阶 + 三个和弦），无需外部素材
& $py main.py --demo-midi

# 离屏起窗口自检（CI/无桌面环境），可顺带保存布局截图
& $py main.py --self-test --self-test-ms 800
& $py main.py --self-test --demo-tone 1000 --self-test-shot images/shot.png
& $py main.py --self-test --demo-midi --self-test-shot images/m5_gui_midi.png

# 详细日志
& $py main.py --log-level DEBUG
```

日志落盘于 `logs/zpyaudio_YYYYmmdd_HHMMSS.log`；配置保存在 `config.json`
（缺失或损坏时自动回退默认值，非法取值自动纠正，界面「视图 → 恢复默认设置」可一键复位）。

**ffmpeg 路径（M5.1）**：启动时自动检测 ffmpeg——命中即写回 `config.json` 的 `ffmpeg_dir`，
日志里会写明来源（`用户指定` / `已自动检测到`）；检测不到只是在播放 mp3/m4a/aac 时给中文提示，
wav/flac/ogg 与 MIDI 不受影响。想改用另一个 ffmpeg（或换机器后路径变了）：
「文件 → 设置 ffmpeg 路径…」→ 填/选 `ffmpeg.exe` 或它所在目录 → 「确定」**立即生效**，无需重启。
对话框里的「自动检测」按钮可随时查看当前环境命中的路径与版本；**用户指定的路径优先于自动检测**，
取消勾选「启动时自动检测」可固定该路径（即使它当前不可用也不被程序改写）。

录制（F6）结束后，除 wav 外还会在同目录生成**与 wav 同名的 CSV**（主频时序，UTF-8 BOM）：
列头 `t_s,freq_hz,freq_smoothed_hz,note,cents,confidence,rms_dbfs,method`，只写有有效主频的帧，
Excel / pandas 可直接打开。写在失败时会在日志与对话框中给出中文提示，wav 不受影响。

界面快捷键：`Ctrl+O` 打开文件、`F5` 开始、`F6` 录制、`F7` 暂停/继续、`F8` 停止。

## 打包（Windows x64 便携版，M6）

一键构建（产物是**文件夹**，非安装包、**不含 ffmpeg**）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_portable.ps1   # 构建
powershell -ExecutionPolicy Bypass -File packaging\verify_portable.ps1  # 构建后验证
```

本机实测：**235.2 MB / 472 个文件**，构建约 80 s。

产物结构：

```text
dist/zpyaudio-0.1.0-win.amd64-portable/
├── zpyaudio.exe            可执行程序（GUI 子系统，双击启动，不弹控制台，版本资源 0.1.0）
├── PORTABLE-README.txt     给最终用户的使用说明（含 ffmpeg 处理方式）
└── _internal/              Python 3.14 运行时 + Qt6 + numpy/scipy/pyqtgraph/… 依赖（87 个顶层条目）
```

打包要点：

- **免安装、可整folder拷贝**：把文件夹拷到任意位置/U 盘即可运行，不写注册表、不写用户 AppData；
  `config.json`、`logs/`、`records/` 都生成在 **exe 同级目录**（`app/config.py::resolve_project_root()`
  在 `sys.frozen` 时以 exe 所在目录为根）。
- **不打包 ffmpeg**：ffmpeg 是外部程序，便携版启动后仍按 FR-6.4 自动检测用户环境
  （环境变量 → `C:\ffmpeg\bin` → 常见安装位置 → `PATH`），也可在「文件 → 设置 ffmpeg 路径…」指定；
  没有 ffmpeg 时只影响 mp3/m4a/aac，wav/flac/ogg 与 MIDI 分析照常。构建脚本会扫描产物，
  发现 ffmpeg/ffprobe 就报警告。
- **配置文件**：`packaging/zpyaudio.spec`（PyInstaller onedir）、`packaging/version_info.txt`
  （exe 版本资源）、`packaging/build_portable.ps1`（构建）、`packaging/verify_portable.ps1`
  （自检：版本资源 / `--self-test` / `--audio-check` / `--list-devices` / `--midi-check` /
  真实桌面启动 6 s 仍响应 / 未打包 ffmpeg）。
- **体积控制**：spec 排除了用不到的重量级 Qt 模块（WebEngine / Quick / Qml / 3D / Multimedia /
  Charts / Sql / Designer / Test 等）**以及一整条第三方可选依赖链**——`pyqtgraph` 顶层会尝试
  `import MatplotlibWidget` → matplotlib → pandas → numba/llvmlite、cupy、IPython/jedi/tornado、
  lxml、Pillow，conda 环境里这些包真实存在，不排除会把产物从 235 MB 撑到 **1.7 GB**。
  sounddevice 的 PortAudio DLL 与 soundfile 的 libsndfile 由 `pyinstaller-hooks-contrib` 钩子收集。
- 打包依赖：`pyinstaller==6.22.3`、`pyinstaller-hooks-contrib==2026.8`（见 `requirements.txt`）。
- 命令行自检在便携版同样可用（GUI 子系统程序不打印到控制台，结论看 exe 同级 `logs/*.log` 与退出码）：
  `zpyaudio.exe --self-test`、`--audio-check 3`、`--midi-check <file.mid>`、`--list-devices`。

打包时踩过的两个坑（已修，留档备查）：

1. **conda 的 C 运行时 DLL 必须显式随包**。conda 把 `ffi-8.dll`、`libexpat.dll`、`libcrypto-3-x64.dll`
   等放在 `<env>\Library\bin`（不在 Windows 默认 DLL 搜索路径里），PyInstaller 解析 `.pyd` 依赖时
   看不到它们：产物里有 `_ctypes.pyd` 却没有 `ffi-8.dll`，冻结后 `import ctypes` 直接
   `DLL load failed`，scipy 跟着报 "install seems to be broken"，程序根本起不来。spec 里按
   "产物里有哪个 `.pyd` 就补对应 DLL" 的方式修（`CONDA_DLL_REQUIREMENTS`）。
2. **excludes 不能排除过头**。排除 `numpy.f2py` 会让 `scipy.signal` → `numpy.__getattr__` 的惰性
   导入崩掉；排除 `distutils` 会与 `setuptools._distutils` 的别名冲突，PyInstaller 直接报
   `ValueError: Target module "distutils" already imported as ExcludedModule`。
   改 excludes 前先跑预检（几秒，不必等 80 s 的打包 + 崩溃弹窗）：

   ```powershell
   & $py packaging\check_excludes.py     # 用 import hook 屏蔽同样的模块，跑一遍全部命令行入口
   ```

   窗口版崩溃时只弹 "Unhandled exception in script" 对话框、看不到 traceback：临时把 spec 里的
   `console=False` 改成 `console=True`，`python -m PyInstaller --noconfirm --distpath dist-diag packaging\zpyaudio.spec`
   重打一份带控制台的，即可在控制台看到完整栈。

## 架构

```text
音频输入源 ──► 环形缓冲 RingBuffer ──► 分析线程(每 62.5 ms) ──► 有界快照队列 ──► GUI 定时器重绘
（设备回调 / 合成源 / 文件源）        （core.analyzer）        (maxsize=4)      （只画图）
      └──► 块回调 ──► 录制写盘线程（wav） / 播放输出（sounddevice.OutputStream）
```

| 目录 | 职责 |
| --- | --- |
| `core/` | 数据结构、环形缓冲、重采样、频谱与主频算法（P1/P2/P3）、主频轨迹平滑、**音名换算（`notes.py`）**、**主频时序收集与 CSV 导出（`pitch_series.py`）**（**不依赖 GUI**） |
| `sources/` | 输入源：`device_source`、`synthetic_source`、`file_source`（准实时推进 + seek + 暂停） |
| `media/` | `decoder`（soundfile / ffmpeg 子进程 / **ffmpeg 定位与版本探测**）、`player`（播放线程）、`recorder`（写盘线程）、**`midi_loader`（mido → 音符事件）、`midi_render`（M7 预留接口）** |
| `app/` | 配置（**含 `resolve_ffmpeg()`：用户配置优先 + 自动检测写回**）、日志、状态机与线程编排（`controller`）、命令行（`cli`） |
| `gui/` | 主窗口、波形/频谱/主频/日志视图、**主频时序数据区（`pitch_data_view`）、钢琴卷帘（`pianoroll_view`）、ffmpeg 路径设置（`settings_dialog`）**、视图选项、进度条、控制条（**不做 FFT、不做文件 IO**） |

关键设计取舍：

- **分析跳步固定 1/16 s（62.5 ms）**，窗长默认 4096（48 kHz 下 85.3 ms，约 27% 重叠）；
  裸 FFT 分辨率 11.7 Hz 无法满足 ±5 Hz 精度，故主频默认用**抛物线插值**。
- **主频三档**：P1 谱峰+插值（默认）、P2 谐波积谱（二次谐波比基频强 14 dB 时仍能还原基频）、
  P3 YIN（80 Hz 等低频下误差 < 1 Hz，远优于 Δf）。
- **音名不是音高**：主频按十二平均律（A4 = 440 Hz）就近命名，同时给**音分偏差**与置信度；
  因 Δf 粗于低音区半音间距，`< 250 Hz` 的读数标注"仅供参考"（界面用 `*`）。
- **主频时序只有一份来源**：分析线程把每帧推入 `PitchSeries`（最近 2000 点供界面增量显示、
  录制时保留全量点供导出），主频图、数据区、状态栏、CSV 都取自它——GUI 定时器丢帧只影响显示，
  不会让导出的 CSV 缺行。
- **录制结束导出**：与 wav 同名的 `records/*.csv`（UTF-8 BOM，ASCII 列头
  `t_s,freq_hz,freq_smoothed_hz,note,cents,confidence,rms_dbfs,method`），只写有效主频帧；
  写失败只提示，不影响 wav 收尾。
- **回调线程不加锁、不分配**：环形缓冲用「写序号校验 + 有限重试」的近似无锁方案。
- **对数坐标轴自绘**（`gui/log_axis.py`）：pyqtgraph 的 `setLogMode` 只对曲线做 log10 映射，
  散点/标线不映射，混用会画错，因此统一由调用方传 log10 + 自定义刻度标签。
- **播放预填**：起播前先把播放队列填到接近满，并把节拍基准回拨预填时长，
  否则源会干等一个预填时长导致欠载（实测 9 次 → 0 次）。
- **静音门限 −60 dBFS**：低于门限不输出主频，避免静音时的虚假读数。
- **ffmpeg 只用于有损格式与重采样**：wav/flac/ogg 走 soundfile；需要重采样时交给 ffmpeg
  （逐块 `resample_poly` 会在块边界产生周期性失真）。

M5 追加：

- **MIDI 是符号数据，不进状态机**：它没有音频、不能播放，所以只作为"当前打开的一份文档"
  挂在控制器上；任何一次真实采集/播放都会自动清掉它，从而天然保证
  "钢琴卷帘只在 MIDI 时显示"（规格书 7.1 区域④）。
- **tick → 秒必须自己按 tempo map 换算**。`mido` 逐轨迭代给出的 `Message.time` 单位是
  **tick**；迭代整个 `MidiFile` 才是秒，但那样拿不到轨道号，而 FR-4.1 要求事件带 `track`。
  照抄 `msg.time` 的后果是"卷帘整体被拉长几百倍"，肉眼很难当场发现。
- **音符配对用队列，不用字典覆盖**：同一 `(channel, note)` 上重叠的多次 `note_on`
  必须逐个配对，否则踏板/长音段落会直接丢音符，`note_on` 计数也就对不上
  （而"解析结果与 mido 消息数一致"正是 FR-4.1 的验收方式）。
- **`note_on velocity=0` 是松键**（MIDI 惯例），轨末未闭合的音符按轨末闭合、不丢弃。
- **符号模式复用主频区域但语义完全不同**：横轴从"最近 N 秒"变成"0…曲长"，标题必须带
  "符号数据"并写明"不是声学主频"；一旦有声学帧进来就自动退回声学模式，避免两套数据混画。
- **符号曲线与音名共用一份公式**：`core/notes.note_frequency()` 既供音名换算，也供
  FR-4.3 的音符频率曲线，避免出现两套 `f = 440×2^((n-69)/12)`。

## 测试

```powershell
& $py -m pytest                      # 359 项，全绿（Python 3.14.7 / PySide6 6.12.0 复测通过）
& $py -m pytest tests/test_analyzer.py -v           # A1 / A6 判据
& $py -m pytest tests/test_notes.py -v              # A10 音名换算
& $py -m pytest tests/test_pitch_series.py -v       # A11 序列收集与 CSV 格式
& $py -m pytest tests/test_controller_media.py -v   # A4 / A5 / A11 / A12 判据
& $py -m pytest tests/test_midi_loader.py -v        # M5：FR-4.1 / FR-4.3 / FR-4.5
& $py -m pytest tests/test_gui_midi.py -v           # M5：FR-4.2 / FR-4.4 与 A7 的界面路径
& $py -m pytest tests/test_gui_settings.py -v       # M5.1：A13 ffmpeg 路径设置与启动自动检测
& $py -m pytest tests/test_config.py tests/test_decoder.py -v   # M5.1：FR-6.4 定位规则
```

播放相关用例使用 `tests/fakes.py` 的 `FakePlayer`，无需声卡即可验证「解码 → 分析 → 播放队列」链路。

`pytest.ini` 关闭了 `tmpdir` 与 `cacheprovider` 插件（受限环境下系统临时目录不可写、
`.pytest_cache` 重命名被拒），需要临时目录的用例改用 `tests/conftest.py` 的 `work_dir` 夹具。

## 状态机

`IDLE → MONITORING / RECORDING / PLAYING → PAUSED → … → IDLE`；非法迁移被忽略并记入 DEBUG 日志。
`ERROR` 仅作为异常回滚的中间态（弹窗/日志提示后立即回到 `IDLE`）。

## 后续（M6）

- M6：覆盖率补强、长时间运行（A9）、PyInstaller 打包（NFR-7）。
- M7（可选）：MIDI → 音频——`media/midi_render.py` 的 `MidiRenderer` 接口已经就位
  （当前 `render()` 抛"未实现"），接入 FluidSynth + SoundFont 后渲染出 PCM，
  再作为普通 `AudioFrame` 进入现有管线即可复用波形/频谱/主频三张图。

## 许可证

本项目采用 [MIT License](LICENSE)（Copyright © 2026 zlz8x8）。

