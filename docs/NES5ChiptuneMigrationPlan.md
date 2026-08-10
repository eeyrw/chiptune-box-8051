# Music Box 8051 改造为 NES 五声道 Chiptune 播放器的详细计划

## 1. 文档状态

- 文档类型：架构设计、格式设计与分阶段实施计划
- 目标工程：`music-box-8051`
- 目标 MCU：STC8H3K64S2-45I-TSSOP20
- 目标声音模型：NES/Famicom 基础五声道，即两个 Pulse、一个 Triangle、一个 Noise、一个 DPCM
- 用户输入格式：FamiTracker Module `.ftm`
- 设备播放格式：离线编译得到的紧凑二进制格式，本文暂命名为 `N5M`
- Fami32 参考版本：提交 `306a4d9200019abb5ef3b13fc3ddb95f9fa0e5fa`，提交日期 2026-08-04
- 当前结论：可行，但属于音频引擎、播放器、工具链和协议层的大规模重构

本文不是“把当前波表换成方波”的方案。当前固件的核心抽象是 MIDI 音符驱动的八路通用波表合成器；目标系统则是由 tracker 逐 tick 控制的五个固定功能声道。两者在乐谱语义、包络模型、声部分配、合成算法和样本存储方面均不相同。

---

## 2. 执行摘要

推荐采用以下总体方案：

```text
用户创作/下载 .ftm
        |
        v
PC 端 FTM 适配器
        |
        | 解析 tracker 的 pattern、instrument、sequence、effect、tempo、loop
        v
规范化 APU 时间线
        |
        | timestamp + 5 声道渲染状态 + DPCM 触发
        v
N5M 编译器
        |
        | 去重、变长等待、状态位图、DPCM 重排、CRC
        v
N5PL 多曲容器
        |
        +--> 内置 Flash: 生成 scoreList.c
        |
        +--> 外置 SPI Flash: 生成 musicbox.n5p
                         |
                         v
8051 N5M Player + 预取队列
                         |
                         v
Timer0 ISR: 五声道定点合成 + NES LUT 混音 + 8-bit PWM
```

核心决策如下：

1. 用户工作流接受 `.ftm`，但 MCU 不直接解析 `.ftm`。
2. tracker 的乐器序列和效果在 PC 端展开；8051 播放已渲染的控制状态。
3. 第一版目标是“NES 五声道听感和 FamiTracker 播放行为兼容”，不是逐 CPU cycle 的 2A03 APU 仿真器。
4. Pulse、Triangle、Noise、DPCM 的逐采样生成仍在 8051 上实时完成，不存 PCM 音频。
5. DPCM 原始比特流保存在 Flash，主循环预取到 XRAM 环形缓冲区，ISR 不直接访问 SPI。
6. 使用 NES 非线性混音 LUT，移除当前通用动态压缩器和 ADSR。
7. 保留现有 PWM、功放、UART、SPI Flash、Storage 后端和多歌曲控制框架。
8. 先以 32kHz 输出完成稳定版本；测量 ISR 余量后再决定是否提高到 48kHz。

这一方案的主要价值是把复杂度放在 PC 上。FTM 解析、tempo、effect、instrument sequence 和循环检测都不是硬实时任务，不应消耗 8051 的 64KB Code、3KB XRAM 和 ISR 周期。

---

## 3. 需求边界

### 3.1 第一版必须支持

- FamiTracker `.ftm` 作为用户输入。
- NTSC 2A03 基础五声道：
  - Pulse 1
  - Pulse 2
  - Triangle
  - Noise
  - DPCM
- FTM 中常用的 2A03 instrument sequences：
  - Volume
  - Arpeggio
  - Pitch
  - Hi-pitch
  - Duty
- 常用 tracker 效果：
  - Speed/tempo
  - Jump frame
  - Skip row
  - Halt
  - Arpeggio
  - Pitch slide up/down
  - Portamento
  - Vibrato
  - Tremolo
  - Volume slide
  - Note delay
  - Note cut
  - Duty/noise mode changes
- DPCM sample、pitch、loop、start offset 和初始 DAC level。
- 多歌曲容器、顺序播放、单曲、上一首、下一首、指定歌曲。
- 内置 Flash 和外置 SPI Flash 两种存储后端。
- 8-bit PWM 单声道输出。
- 串口诊断、五声道 mute、状态查询和 Flash 管理。

### 3.2 第一版明确不支持

- VRC6、VRC7、FDS、MMC5、N163、S5B 等扩展音源。
- 在设备上编辑 `.ftm`。
- 在设备上直接浏览 FAT 文件系统。
- 直接播放任意 NSF/NSFe 中的 6502 程序。
- PAL 与 Dendy 的完整时序兼容。
- 逐 CPU cycle、逐 APU frame sequencer edge 的硬件级仿真。
- Stereo、声像、混响或现代软合成效果。
- 播放过程中执行 SPI chip erase 或长时间 page program。

### 3.3 可选的后续能力

- PAL 曲目。
- 48kHz 输出。
- 更严格的 APU envelope、length counter、sweep 和 frame counter 仿真。
- 直接导入 NSF/NSFe/VGM。
- SFX 与背景音乐共享声道。
- 实时串口演奏五个 APU 声道。
- FamiStudio `.fms` 输入适配器。

---

## 4. 当前工程基线

### 4.1 可以保留的部分

| 当前模块 | 处理方式 | 原因 |
|---|---|---|
| `Bsp.c` PWM 初始化 | 保留并小幅调整 | 已经提供 8-bit PWM 载波和 Timer0 音频中断 |
| `Bsp.c` UART/ADC/时钟 | 保留 | 与音乐格式无关 |
| `Storage.c` 运行时后端选择 | 保留 | 内置与 SPI 两个后端仍然需要 |
| `Storage_Internal.c` | 泛化后保留 | 可承载 N5PL 常量数据 |
| `Storage_SPI.c` | 泛化后保留 | 可承载外部 N5PL 镜像 |
| `SpiFlash.c` GPIO SPI | 保留底层，重做读取策略 | PCB 数据线交叉决定仍需软件 SPI |
| Player 的歌曲切换状态机 | 保留思想，重写解码器 | Prev/Next/SetSong 仍有效 |
| Protocol 的 framing、RX ring、TX 状态机 | 保留 | 串口可靠性修复仍然适用 |
| `UpdateTick.inc` / `GetSysMs()` | 32kHz 阶段保留 | 系统时间和超时仍需要 |
| RUN_TEST 构建框架 | 保留并扩展 | 适合加入五声道确定性测试 |

### 4.2 必须替换的部分

| 当前模块 | 替换原因 |
|---|---|
| `Synthesizer/WaveTable.*` | NES Pulse/Triangle/Noise 不使用长 PCM 波表 |
| `Synthesizer/NonlinearMapTable.*` | 当前表服务于通用 ADSR，不是 NES 混音 |
| `Synthesizer/CompressorGenerated.*` | NES 需要固定非线性混音，不应再套动态压缩器 |
| `Synthesizer/SynthCore.*` | 当前模型是 8 个可分配 MIDI voice |
| `Synthesizer/Synth.inc` | 热路径需要改为五个固定功能声道 |
| `Player/Player.c` 的 SSCR 解码 | 当前事件是 NoteOn/NoteOff，不包含 duty/noise/DPCM 状态 |
| `scoreList.c` 的 SSPL/SSCR 数据 | 改为 N5PL/N5M 数据 |
| Protocol 中 NoteOn/Off、VoiceDump、ADSR | 与新模型不匹配 |
| `tools/adsr_*` | 新引擎没有通用 ADSR 参数 |

### 4.3 当前资源基线

当前链接结果大致为：

| 资源 | 当前使用 | 上限 | 备注 |
|---|---:|---:|---|
| Code/Flash | 37219 bytes | 65536 bytes | 波表和压缩器表占有可回收空间 |
| XRAM | 1953 bytes | 3072 bytes | 含 1024-byte SPI cache 与约 780-byte 协议区 |
| DATA/IDATA | 固定 Synth 0x21-0x72 | 256 bytes | 0x73 起为 141-byte C stack |
| 音频 ISR | 32000 Hz | 约 1037 个系统时钟/次 | `MAIN_Fosc=33177600UL` |

目标重构必须继续遵守：

- `EXTRAM` 保持 0。
- Timer0 ISR 使用 register bank 1。
- ISR 内不得调用可能使用不可控 C stack 的通用函数。
- DATA 绝对布局变化必须同步 C struct、`.inc` 偏移和测试。
- 修改 `.inc` 后必须 clean build。

---

## 5. Fami32 对比研究结论

### 5.1 已确认的事实

Fami32 使用 FamiTracker Module `.ftm`，不是 `.fmt`。其 README 明确列出基础五声道和 tracker 编辑功能。

参考提交中的重要行为：

- `FTM_FILE::open_ftm()` 检查文件头 `FamiTracker Module`。
- 仅接受 FTM header version `0x0440`。
- 直接解析 PARAMS、INFO、HEADER、INSTRUMENTS、SEQUENCES、FRAMES、PATTERNS 和 DPCM SAMPLES blocks。
- tracker player 以 tick 为单位更新 instrument sequence 和 effects。
- 默认音频参数为 72kHz、60Hz engine tick、4 倍过采样。
- 实时声道实现大量使用 `float`、`std::vector`、整 tick PCM buffer、FIR、HPF 和 LPF。
- 混音使用 31 项 Pulse table 和 203 项 TND table。
- 提供 NES VGM exporter：输出 2A03 register writes、44.1kHz wait commands 和 DPCM RAM block。
- VGM exporter 将 tracker 的包络/效果结果转成 constant-volume APU register 状态；它不是把完整 FTM instrument engine搬进 APU。

### 5.2 值得借鉴的部分

| Fami32 设计 | 在本项目中的用途 |
|---|---|
| FTM block 划分 | 确认 host converter 需要覆盖的对象范围 |
| 5 类 instrument sequence | 定义 FTM 前端的兼容性测试矩阵 |
| tick/row/frame 状态机 | 用作行为对照，不直接移植 |
| NES mixer table 结构 | 作为 LUT 规模和索引方式参考 |
| VGM exporter | 作为规范化 APU 日志的参考输出和 golden oracle |
| DPCM 64-byte 对齐与 16KB window | 用于设计 N5M DPCM bank |
| loop position detection | 用于 host compiler 的循环分析 |

### 5.3 不应移植的部分

| Fami32 设计 | 不适合 8051 的原因 |
|---|---|
| 设备端直接解析 FTM | 需要大量动态对象、随机访问和复杂版本分支 |
| `std::vector` 数据模型 | 8051 无堆或不应依赖堆 |
| 浮点频率与相位 | SDCC 8051 浮点成本过高 |
| 每声道整 tick buffer | 3KB XRAM 无法容纳 |
| 72kHz + 4x noise oversampling | Timer0 周期和 CPU 预算不允许 |
| I2S 16-bit PCM block output | 当前硬件是 8-bit PWM 寄存器逐样本更新 |
| UI、文件系统、USB MSC/MIDI | 当前 PCB 没有对应资源和交互设备 |
| 扩展音源 | 超出五声道目标与资源预算 |

### 5.4 许可证约束

研究时未在 Fami32 仓库根目录发现明确的 LICENSE/COPYING 文件。第三方 component 自带许可证不能自动覆盖 Fami32 自有源码。

因此实施时必须采用以下原则：

- 不复制 Fami32 的 FTM parser、player、mixer 或 exporter 源码。
- 不把其源文件加入本项目构建。
- 可以把编译后的外部工具作为人工对照工具使用。
- 可以依据公开的 FTM、VGM 和 NES APU 格式规范独立实现。
- 测试中可以比较两个程序对同一输入的可观察输出。
- 若未来希望复用源码，必须先获得作者明确授权或补充的开源许可证。

---

## 6. 精度目标：不是完整 APU 模拟器

### 6.1 推荐的第一版精度定义

第一版定义为 `TRACKER_RENDERED_NES5`：

- PC 端执行 FTM 的 tracker 语义。
- 每个 engine tick 计算五个声道最终可听状态。
- 设备接收的是 period、duty、volume、noise mode/rate、DPCM trigger 等状态。
- 设备实时生成波形并使用 NES 非线性混音。
- 不要求设备重现 APU 硬件 envelope、sweep 和 length counter 的内部时钟边沿。

这与 Fami32 VGM exporter 的思路一致：tracker software sequence 已经生成每 tick 的音量、音高和 duty，导出的 Pulse control 使用 constant volume，sweep 被关闭。

### 6.2 为什么不从完整 APU 仿真起步

完整 APU 仿真需要同时正确处理：

- CPU/APU 时钟比。
- Pulse timer、sweep unit、envelope divider、length counter。
- Triangle linear counter、length counter 和序列保持状态。
- Noise timer 和 15-bit LFSR。
- DMC DMA、sample buffer、output shifter、IRQ/loop。
- 4-step/5-step frame sequencer。
- `$4015/$4017` 的精确副作用。

这些能力对于执行任意 NSF 或游戏代码很重要，但对于已经由 tracker renderer 每 tick 输出最终状态的音乐流，大部分是重复工作。先做完整仿真会显著增加汇编复杂度和测试面，却不一定改善 FTM 曲目的听感。

### 6.3 后续升级条件

只有满足以下条件之一，才进入完整 APU profile：

- 需要直接播放标准 VGM 而不预渲染状态。
- 需要接受使用硬件 envelope/sweep 的第三方日志。
- 与参考播放器的差异被定位到 APU frame sequencer，而不是采样率或滤波。
- 32kHz 五声道 ISR 的最坏周期仍保留至少 40% 裕量。

---

## 7. Host 工具链设计

### 7.1 分层结构

建议建立以下工具：

```text
tools/chiptune/
  ftm_frontend.py       # 用户入口与外部工具编排
  vgm_reader.py         # VGM 2A03 命令和 DPCM data block 读取
  nsf_trace.py          # 可选：NSF 离线执行与 APU write 捕获
  apu_log.py            # 规范化时间线数据结构
  n5m_compiler.py       # 状态归并、时间换算、压缩和校验
  n5m_format.py         # N5M/N5PL 读写定义
  n5m_dump.py           # 容器检查与反汇编
  n5m_reference.py      # PC 定点参考播放器
  pack_playlist.py      # 多曲容器、scoreList.c 与 SPI 镜像生成
```

### 7.2 输入适配器策略

为了避免把 FTM parser 与硬件开发绑定，输入端分三步交付。

#### 阶段 A：VGM 输入

- 接受只包含 NES APU 的 `.vgm`。
- 支持 `0xB4 aa dd` APU write。
- 支持 `0x61/0x62/0x63` wait。
- 支持 `0x67 0x66 0xC2` NES RAM data block。
- 支持 loop offset 和 end command。
- 拒绝 expansion chip commands。

Fami32 可导出这种 VGM，因此它可以立即生成对照输入。此阶段最快打通 DPCM、时间线和五声道固件。

#### 阶段 B：FTM 外部转换

`ftm_frontend.py` 接受 `.ftm`，通过可配置外部后端导出 NSF 或 VGM：

- FamiStudio CLI 后端。
- Dn-FamiTracker/FamiTracker 命令行或人工导出后端。
- Fami32 desktop 导出文件作为手工参考后端。

外部工具失败时必须打印：工具版本、输入 FTM header version、命令行、stderr 和建议的手工导出路径。

#### 阶段 C：独立 FTM parser

如果外部工具部署体验不可接受，再依据公开格式规范独立实现 FTM `0x0440` parser。第一版只支持：

- 单 track。
- 2A03、5 channels。
- PARAMS block version 6。
- HEADER block version 3。
- INSTRUMENTS block version 6。
- SEQUENCES block version 6。
- FRAMES block version 3。
- PATTERNS block version 5。
- DPCM SAMPLES block version 1。

其他版本必须显式拒绝，不能静默猜测结构。

### 7.3 规范化 APU 日志

所有输入后端先转换为统一数据模型：

```text
ApuLog
  region              NTSC/PAL
  source_rate         例如 44100
  total_source_ticks
  loop_source_tick
  metadata
  dpcm_memory_image
  events[]

ApuEvent
  absolute_source_tick
  writes[]

ApuWrite
  register_index      0x00..0x17，对应 $4000..$4017
  value               uint8
```

规范化阶段执行：

- 同一 timestamp 的 writes 合并。
- 同一寄存器多次写入保留最后一次，但 DPCM start/stop 等有边沿副作用的写入不得错误消除。
- 不可识别命令报错并包含文件偏移。
- 验证 DPCM virtual address 位于 `$C000..$FFFF`。
- 验证 DPCM data block 不超过 16KB。
- 保留 loop timestamp，而不是仅保留文件字节 offset。

### 7.4 从 APU 日志生成渲染状态

Host renderer 维护 24-byte APU register shadow，并在事件处输出以下五声道状态：

```text
PulseState:
  enabled
  timer_period_11bit
  duty_2bit
  volume_4bit
  phase_reset

TriangleState:
  enabled
  timer_period_11bit
  gate

NoiseState:
  enabled
  period_index_4bit
  short_mode
  volume_4bit

DpcmCommand:
  action: none/start/stop
  rate_index_4bit
  loop
  initial_level_7bit
  virtual_start_14bit
  byte_length_12bit
```

Host renderer 的职责是把 FTM/VGM 中与 tracker 相关的状态化约，而不是生成 PCM。频率相位仍由 MCU 连续推进，因此输出数据量保持很小。

### 7.5 时间换算

设备第一版采样率为 32000Hz。所有 source timestamp 用累计法转换：

```text
target_sample = round(source_tick_total * 32000 / source_rate)
delta_sample  = target_sample - previous_target_sample
```

禁止逐事件独立四舍五入再累加，因为长曲目会产生系统性 tempo 漂移。

对于 44.1kHz VGM：

```text
target = floor((source_total * 32000 + 22050) / 44100)
```

循环点也使用同一累计转换公式。转换器验证循环前后相位和事件边界，不允许 loop target 落在一个事件 payload 中间。

---

## 8. N5PL/N5M 设备格式

### 8.1 设计目标

- 顺序读取友好。
- 绝大多数控制事件只需数个字节。
- DPCM 数据可独立预取。
- 支持内置 Flash 和 SPI Flash。
- 所有多字节整数固定 little-endian。
- 有明确版本和 CRC。
- MCU 不依赖字符串、动态内存或递归 parser。
- 损坏文件必须停止播放，不能越界访问 Flash。

### 8.2 N5PL 多曲容器头

建议容器 magic 为 `N5PL`，固定头 24 bytes：

| Offset | Size | Field | 说明 |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `N5PL` |
| 4 | 1 | version | 第一版为 1 |
| 5 | 1 | flags | 保留，必须为 0 |
| 6 | 2 | song_count | 曲目数量 |
| 8 | 4 | table_offset | 一般为 24 |
| 12 | 4 | total_size | 整个容器字节数 |
| 16 | 4 | payload_crc32 | entry table 和 payload CRC32 |
| 20 | 4 | reserved | 必须为 0 |

每个 entry 固定 16 bytes：

| Offset | Size | Field |
|---:|---:|---|
| 0 | 4 | track_offset |
| 4 | 4 | track_size |
| 8 | 4 | name_hash 或 metadata offset |
| 12 | 4 | flags/reserved |

第一版 MCU 不需要显示歌曲名，因此可以只使用 hash 或置零；PC dump 工具仍可从可选 metadata block 显示名称。

### 8.3 N5M track header

建议每曲固定头 64 bytes：

| Offset | Size | Field | 说明 |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `N5M1` |
| 4 | 1 | header_size | 64 |
| 5 | 1 | region | 0=NTSC，1=PAL |
| 6 | 1 | channel_mask | bit0..4 对应五声道 |
| 7 | 1 | flags | loop、DPCM、metadata |
| 8 | 4 | sample_rate | 第一版必须为 32000 |
| 12 | 4 | total_samples | 非循环段结束 sample |
| 16 | 4 | loop_sample | 无循环为 `0xFFFFFFFF` |
| 20 | 4 | event_offset | 相对 track 起点 |
| 24 | 4 | event_size | 字节数 |
| 28 | 4 | dpcm_offset | 无 DPCM 为 0 |
| 32 | 4 | dpcm_size | 最大 16384 |
| 36 | 4 | loop_event_offset | 相对 event stream |
| 40 | 4 | metadata_offset | 可为 0 |
| 44 | 4 | metadata_size | 可为 0 |
| 48 | 4 | event_crc32 | event stream CRC |
| 52 | 4 | dpcm_crc32 | DPCM CRC |
| 56 | 8 | reserved | 必须为 0 |

### 8.4 Event stream 编码

事件流采用 opcode 编码：

| Opcode | Payload | 含义 |
|---:|---|---|
| `0x00` | 无 | END |
| `0x01` | `u8` | WAIT 1..255 samples |
| `0x02` | `u16le` | WAIT 1..65535 samples |
| `0x03` | `u24le` | WAIT 1..16777215 samples |
| `0x10` | `channel u8 + mask u8 + values` | 更新一个 Pulse 字段 |
| `0x11` | `mask u8 + values` | 更新 Triangle 字段 |
| `0x12` | `mask u8 + values` | 更新 Noise 字段 |
| `0x13` | 6 bytes | DPCM START |
| `0x14` | 无 | DPCM STOP |
| `0x15` | `u8` | channel enable/mute mask |
| `0x7F` | `len u8 + bytes` | 可跳过扩展命令 |

状态更新采用字段 mask，values 按 bit 从低到高排列。这样只改变 volume 时不必重复 period 和 duty。

`PULSE_STATE (0x10)`：

| 字段 | 编码 |
|---|---|
| channel | `0`=Pulse 1，`1`=Pulse 2，其他值非法 |
| mask bit0 | enabled，payload `u8`，只能为 0/1 |
| mask bit1 | timer period，payload `u16le`，仅低 11 bit 有效 |
| mask bit2 | duty，payload `u8`，范围 0..3 |
| mask bit3 | volume，payload `u8`，范围 0..15 |
| mask bit4 | phase reset，无 payload；仅当输入日志明确要求重置 Pulse sequencer 时置位 |
| mask bit5..7 | 保留，第一版必须为 0 |

`TRIANGLE_STATE (0x11)`：

| mask | 含义 |
|---|---|
| bit0 | enabled，payload `u8` 0/1 |
| bit1 | timer period，payload `u16le`，低 11 bit 有效 |
| bit2 | gate，payload `u8` 0/1 |
| bit3..7 | 保留 |

`NOISE_STATE (0x12)`：

| mask | 含义 |
|---|---|
| bit0 | enabled，payload `u8` 0/1 |
| bit1 | period index，payload `u8` 0..15，使用标准 `$400E` 顺序 |
| bit2 | short mode，payload `u8` 0/1 |
| bit3 | volume，payload `u8` 0..15 |
| bit4..7 | 保留 |

`DPCM_START (0x13)` 固定 payload：

| Offset | Size | Field | 说明 |
|---:|---:|---|---|
| 0 | 1 | rate_flags | bit0..3=rate 0..15，bit6=loop，其余必须为 0 |
| 1 | 1 | initial_level | 0..127 |
| 2 | 2 | virtual_offset | 相对 `$C000` 的 offset，必须 64-byte 对齐 |
| 4 | 2 | byte_length | 1..4081，且不得越过 DPCM bank |

`CHANNEL_MASK (0x15)` 的 bit0..4 分别对应 Pulse 1、Pulse 2、Triangle、Noise、DPCM。该命令表示内容层的 enable mask；串口调试 mute mask 独立存在，最终有效状态为两者组合。bit5..7 必须为 0。

事件分组语义固定如下：

1. stream 开始处的状态命令属于 sample 0。
2. 一组状态命令之后的 WAIT 表示“从当前组到下一组”的时间。
3. parser 读到 WAIT 时，将已经聚合的当前组和当前 deadline 放入 control queue，然后把 deadline 增加 WAIT 值。
4. 多个连续 WAIT 合法，等价于没有状态变化的时间推进。
5. END 前如仍有未提交状态组，先提交该组，再排入 END marker。
6. `loop_event_offset` 必须指向一个 opcode 边界和事件组开头，不能指向 mask payload 或 WAIT 参数。
7. loop 默认保留各振荡器相位、Noise LFSR 和 DPCM DAC hold level；只有 loop 入口事件带 Pulse phase-reset 或 DPCM start 时才重置相应状态。

真实 APU 写 `$400F` 不会重置 Noise LFSR，Triangle note start 也不应默认清零 32-step sequencer。因此 N5M v1 不为 Triangle/Noise 提供通用 retrigger 位。Pulse phase reset 同样不是“每个 tracker note 自动置位”；adapter 必须根据输入日志中实际可观察的 timer-high write 或明确的兼容策略决定。

编译器选择最短 WAIT：

- 1..255 使用 `0x01`。
- 256..65535 使用 `0x02`。
- 更长使用 `0x03`，或拆成多个 WAIT。

连续同 timestamp 的状态变化不得插入 WAIT。END 后：

- 若有 loop，播放器将 event pointer 跳至 `loop_event_offset`，并将逻辑 sample clock 重定位到 loop timeline。
- 若无 loop，关闭五个声道并通知 scheduler。

### 8.5 DPCM bank

DPCM bank 表示虚拟 `$C000..$FFFF` window 的有效前缀：

- `dpcm_offset` 必须 64-byte 对齐。
- 每个 sample 起点必须 64-byte 对齐。
- sample 实际长度必须满足 NES `length = reg * 16 + 1` 的可表示规则。
- 不足的尾部由 converter 填充安静或保持趋势的 DPCM byte，而不是读取未初始化数据。
- 总 bank 最大 16384 bytes。
- converter 可以合并重复 sample，以内容 hash 去重。

MCU 计算：

```text
bank_offset = (virtual_address - 0xC000)
```

它不需要真正构造 6502 address space。

### 8.6 格式防御

播放前验证：

- magic/version/header_size。
- track 和所有 sub-block 位于容器范围内。
- 所有 offset 加 size 不产生 32-bit overflow。
- sample_rate 是固件支持值。
- loop offsets 位于 event stream 内。
- DPCM start/length 位于 DPCM bank 内。
- event parser 未读取超过 event_size。
- 未知不可跳过 opcode 立即终止曲目。

SPI 后端启动时可选择只验证 header 和局部 CRC；PC 烧录工具必须在写入后 read-back 校验整个镜像。

---

## 9. 8051 五声道合成器

### 9.1 模块布局

```text
NesSynth/
  NesSynth.h
  NesSynth.c
  NesSynth.inc
  NesSynthAsm.s
  NesSynthStep.inc
  NesMixerTable.h
  NesMixerTable.c
  NesRateTable.h
  NesRateTable.c
  NesSynth_testbench.s
```

职责划分：

- `NesSynth.c`：初始化、控制状态提交、非 ISR 诊断。
- `NesSynthStep.inc`：逐采样热路径。
- `NesSynthAsm.s`：DATA 绝对布局。
- `NesMixerTable.*`：离线生成的 8-bit mixer LUT。
- `NesRateTable.*`：Noise/DPCM rate increments。
- Player 主循环：解析 N5M 并提前排队控制事件。
- Timer0 ISR：按 sample deadline 应用事件并生成一个 PWM sample。

### 9.2 热状态与控制状态分离

为了减少 MOVX，逐样本访问的数据放 DATA；低频控制数据放 XRAM。

建议 DATA hot state：

```text
PulseHot x2:
  phase24
  increment24
  duty_threshold
  volume
  flags

TriangleHot:
  phase24
  increment24
  flags

NoiseHot:
  step_frac16
  step_frac_increment16
  step_base
  lfsr16
  volume
  mode
  flags

DpcmHot:
  step_frac16
  step_frac_increment16
  step_base
  output_level
  shift_reg
  bits_remaining
  current_byte
  bytes_remaining16
  flags

GlobalHot:
  raw_mix16
  pwm_sample
  sample_clock32 或低位 deadline clock
  underrun counters/flags
```

目标是整个 hot state 不超过当前 82-byte absolute DATA 区域。详细字节布局在实现阶段由 linker map 和 `.inc` 共同锁定。

XRAM control state 包含：

- event queue。
- DPCM ring buffer。
- queue producer/consumer counters。
- 当前 track offsets 和 parser state。
- 诊断 snapshot。
- 非实时 register shadow。

### 9.3 Pulse 1/2

使用 24-bit DDS phase：

```text
phase += increment
step = phase >> 21       # 0..7
output = duty_pattern[duty][step] ? volume : 0
```

四种 duty pattern：

```text
12.5%: 00000001
25.0%: 00000011
50.0%: 00001111
75.0%: 11111100 或等价相位版本
```

相位方向和 duty pattern 必须以 reference renderer 的逐样本测试为准。频率为：

```text
f = NES_CPU_NTSC / (16 * (timer + 1))
increment = round(f * 2^24 / output_sample_rate)
```

increment 只在 period 改变时由主循环计算或查表，不在 ISR 内除法。

标准 APU 会使过小的 Pulse timer 静音；第一版明确对 `timer < 8` 输出 0。这样 Pulse DDS 的有效频率低于 output sample rate，24-bit 单周期 phase increment 可表示。该静音规则必须由 host reference 和 MCU 同时实现，不能只在 converter 中丢弃状态。

可选实现：生成 2048 项 24-bit increment 表，约 6144 bytes Code。由于当前 WaveTable 可释放大量 Code，这个表可以换取更简单且确定的控制提交。

建议先用表，完成后再根据 Code 使用量决定是否改为主循环 32-bit division。

### 9.4 Triangle

Triangle 使用 32-step 序列：

```text
15,14,...,1,0,0,1,...,14,15
```

频率：

```text
f = NES_CPU_NTSC / (32 * (timer + 1))
```

DDS phase 的高 5 bit 选择序列值。停止时的策略需要可配置：

- `HOLD_LAST`：保持当前 DAC level，更接近真实 APU。
- `CENTER`：固定 7/8，减少设备直流跳变。
- `ZERO`：便于 mixer silence 定义，但可能产生 click。

第一版建议 `HOLD_LAST`，再由 DC blocker 处理直流。

对 `timer < 2` 的 Triangle ultrasonic 情况，第一版按 `HOLD_LAST` 处理且不推进 DDS。这样既避免输出不可表示的超采样率 phase increment，也避免把超声状态错误折叠成强烈可听 alias。该策略列入 compatibility report；如果 golden 曲目依赖另一种行为，再增加编译 profile，而不是在固件中隐式改变。

### 9.5 Noise

Noise 使用 15-bit LFSR：

```text
feedback = bit0 XOR (short_mode ? bit6 : bit1)
lfsr = (lfsr >> 1) | (feedback << 14)
output = (lfsr & 1) ? 0 : volume
```

16 档 period 使用标准 NTSC `$400E` table：

```text
4, 8, 16, 32, 64, 96, 128, 160,
202, 254, 380, 508, 762, 1016, 2034, 4068
```

这里 index 0 是最短周期，index 15 是最长周期。Fami32 的内部 `noise_rate` 顺序与最终 APU register 顺序存在一次反转，N5M 必须统一使用标准 `$400E` 顺序，避免 converter 和固件各反转一次或完全不反转。

Noise shift frequency：

```text
rate_num = NES_CPU_NTSC
rate_den = period_table[index]
step_base = floor(rate_num / (rate_den * output_sample_rate))
remainder = rate_num - step_base * rate_den * output_sample_rate
step_frac_increment = round(
    remainder * 65536 / (rate_den * output_sample_rate))
```

Noise 不能使用普通的 24-bit “小于一周期” DDS increment，因为最快 rate 大约是 output sample rate 的 14 倍。每个声道 sample 使用整数步数加 16-bit 余数：

每个 output sample：

```text
steps = step_base
step_frac += step_frac_increment
if step_frac carry:
    steps++
repeat steps times:
    step_lfsr()
```

最短 period 为 4 CPU clocks，在 32kHz 下一个 output sample 最坏约推进 14 次 LFSR。必须实测最坏循环次数和周期。优化顺序：

1. 普通循环推进。
2. 对 `steps=0..14` 使用小型循环或分段展开。
3. 如果仍超时，为长/短模式生成多步 jump helper。

不建议照搬 Fami32 的 4x oversampling FIR，因为这会显著超过 8051 预算。

### 9.6 DPCM

DPCM 输出 level 为 0..127。每个 bit：

```text
if bit == 1 and level <= 125: level += 2
if bit == 0 and level >= 2:   level -= 2
shift_reg >>= 1
bits_remaining--
```

当 8 bits 用完：

- 从 XRAM ring 取下一个 byte。
- `bytes_remaining--`。
- 结束时根据 loop flag 重启或保持当前 DAC level。
- ring 空时不读 SPI，保持当前 level，增加 underrun counter，并设置 refill urgent flag。

DPCM rate 使用 16 项 NTSC 表转为与 Noise 相同的 `step_base + Q0.16 fraction`。最高 rate 约 33144 bit/s，在 32kHz 下 `step_base=1`，并偶尔由 fraction carry 再消费一个 bit。逻辑不能假定每个 sample 最多一个 bit，也不能把大于 1.0 的 rate 截断进 24-bit increment。

### 9.7 NES 非线性混音

标准近似结构：

```text
pulse_index = pulse1 + pulse2                 # 0..30
tnd_index   = 3*triangle + 2*noise + dpcm     # 0..202
mix = pulse_table[pulse_index] + tnd_table[tnd_index]
```

PC 生成器将浮点公式预量化为适合 PWM 的整数 LUT：

```text
pulse = 95.52 / (8128 / index + 100)
tnd   = 163.67 / (24329 / index + 100)
```

固件 ISR 不做除法和浮点。可以选择：

- 两张 unsigned 8-bit table 后相加并限幅。
- 两张 signed 16-bit table 后统一缩放。

第一版建议 8-bit table：31 + 203 = 234 bytes Code，ISR 只需两个 MOVC、一次加法和限幅。

当前 compressor 必须移除。若输出偏小，调整 LUT 的 master scale，而不是引入动态增益。

### 9.8 DC blocker

NES 混音包含随声道状态变化的 DC 分量；真实设备通常由模拟耦合处理。为了避免 Triangle/DPCM hold level 造成 PWM 中点漂移，可加入低成本整数 DC estimator：

```text
dc += (sample - dc) >> N
output = sample - dc
```

实现要求：

- `dc` 至少 16-bit signed。
- N 由离线脚本计算并通过录音比较选择。
- 可以编译期开关。
- 必须先测量 PCB 音频输入耦合；若硬件已有足够高通，可关闭数字 DC blocker 节省周期。

### 9.9 8-bit PWM 映射

最终 signed mix 映射为：

```text
pwm = saturate(mix + 128, 0, 255)
PWMA_CCR2L = pwm
```

静音必须稳定输出 128，而不是 0 或 256。`PWMA_CCR2H` 保持与 ARR 配置一致。

---

## 10. 精确事件调度

### 10.1 不能继续使用毫秒轮询调音乐事件

当前 SSCR Player 使用 `GetSysMs()` 和 `delta * 8`。NES tracker 的控制 tick 通常约 60Hz，并且 vibrato、arpeggio、delay 等效果对 tick 抖动更敏感。

如果继续由 main loop 用毫秒判断：

- SPI cache fill 会带来毫秒级延迟。
- UART 大响应会改变事件落点。
- 16.666ms 不能由整数 ms 精确表示。

因此 N5M 使用 output sample 作为时间单位。

### 10.2 事件预取队列

主循环始终提前解析事件到 XRAM queue：

```text
ControlEventQueue[8..16]
  target_sample_low_or_delta
  changed_channel_mask
  compact state payload
```

ISR 每个 sample：

1. 增加 sample clock。
2. 比较 queue head deadline。
3. 到期则应用全部同 timestamp 事件。
4. 生成五声道 sample。
5. 写 PWM。

队列规则：

- producer 仅 main loop 写 tail。
- consumer 仅 Timer0 ISR 写 head。
- head/tail 使用 8-bit 原子字段。
- producer 完整写 payload 后最后提交 tail。
- ISR 读取 tail 后不得访问未提交槽。
- queue 空且未到 END 计为 event underrun。

### 10.3 Deadline 表示

不建议每个 event 保存完整 32-bit absolute time。可选方式：

- queue 中保存 `uint16_t delta_samples`，长 wait 由 parser 分段。
- hot state 保存 16-bit countdown，每 sample 递减。
- countdown 为 0 时应用下一个事件。

这种方式比每 sample 做 32-bit compare 更便宜。歌曲总 sample clock 仍可由低频诊断状态维护。

### 10.4 同 timestamp 原子性

FTM 一个 tick 可能同时修改五个声道。听感上必须在同一 output sample 生效。

推荐 queue slot 包含一个聚合后的 state delta；ISR 先应用完整 delta，再生成该 sample。不能让 Pulse 1 在前一个 sample 改变、Pulse 2 在后一个 sample 才改变。

---

## 11. SPI 与 DPCM 预取

### 11.1 当前问题

当前 `SpiFlash.c` 的 1024-byte cache miss 会同步 bit-bang 读取整个 1KB。虽然高优先级 Timer0 可以打断读取，但 main loop 在 refill 期间无法继续准备事件；DPCM 与 event stream 共用一个 cache 还可能互相抖动。

### 11.2 推荐重构

将通用 1KB cache 替换为面向播放器的两个顺序 ring：

| Buffer | 建议大小 | 用途 |
|---|---:|---|
| Event byte ring | 192 或 256 bytes | N5M event stream |
| DPCM byte ring | 256 bytes | DPCM bitstream |
| SPI burst scratch | 32 或 64 bytes | 每次短 burst |

主循环优先级：

1. DPCM ring 低于 high watermark 时 refill。
2. Event queue 少于安全水位时解析和 refill。
3. UART protocol。
4. 可视化和低优先级统计。

### 11.3 水位建议

DPCM 最大约 4143 bytes/s：

- 256-byte ring 理论可覆盖约 61ms。
- low watermark 可设 96 bytes。
- refill burst 可设 32 或 64 bytes。

Event stream 通常远低于 DPCM 带宽。至少预取 8 个 engine ticks，约覆盖 133ms。

### 11.4 Flash 写入互斥

播放时：

- `FLASH_READ` 可允许，但可能降低预取余量，应限长。
- `FLASH_WRITE/ERASE/ERASE_ALL` 默认返回 BUSY。
- 或 Protocol 在明确 stop playback 后才执行写擦。

不能一边播放 DPCM 一边 chip erase。

---

## 12. 内存预算

### 12.1 DATA/IDATA 目标

| 项目 | 预算 |
|---|---:|
| Register bank 0/1 | 16 bytes，固定 |
| sysMs 与调度小变量 | 约 8 bytes |
| bit-addressable | 1..4 bytes |
| NesSynth hot state | 不超过 82 bytes |
| C stack | 保持至少 128 bytes，目标仍为 141 bytes |

不扩大 absolute DATA 区域到 0x73 以上。若 hot state 超过 82 bytes，优先把诊断、register shadow、完整 sample clock 和低频 control state 移到 XRAM，而不是压缩 C stack。

### 12.2 XRAM 目标

| 项目 | 预算上限 |
|---|---:|
| Protocol RX/pkt/TX | 约 780 bytes |
| Event byte ring | 256 bytes |
| DPCM ring | 256 bytes |
| Control event queue | 192 bytes |
| SPI scratch | 64 bytes |
| Player/Storage streams | 128 bytes |
| APU control/diagnostics | 128 bytes |
| 其他全局与初始化 | 256 bytes |
| 安全余量 | 至少 512 bytes |

总目标不超过 2560/3072 bytes。链接后必须检查 `.mem`，不能只依据 C struct 手算，因为 SDCC large model、初始化段和临时全局会改变占用。

### 12.3 Code 目标

删除长 WaveTable 和旧控制代码后预计会释放大量 Code。新预算：

| 模块 | 预算 |
|---|---:|
| 五声道汇编热路径 | 2..5KB |
| Player/parser | 3..5KB |
| Tables | 0.5..7KB，取决于 period table 策略 |
| Protocol 更新 | 1..2KB |
| 测试固件额外代码 | 只在 RUN_TEST |

生产固件目标低于 52KB，为后续格式升级保留至少 12KB。

---

## 13. ISR 周期预算

### 13.1 32kHz 阶段

`33177600 / 32000 ≈ 1036.8`，当前 reload 使用整数除法，实际频率需用示波器测量确认。

目标最坏预算：

| 部分 | 目标周期 |
|---|---:|
| ISR 保存/恢复与 UpdateTick | <= 60 |
| Event countdown，无事件 | <= 10 |
| Pulse x2 | <= 50 |
| Triangle | <= 20 |
| Noise 普通 | <= 80 |
| Noise 最短周期 | <= 220 |
| DPCM 普通 | <= 50 |
| DPCM 双 bit | <= 80 |
| Mixer LUT + DC + PWM | <= 80 |
| 到期 control event apply | <= 180 |
| 总 worst case | <= 700 |

留下至少约 30% 余量给中断边界、分支波动和后续修正。

### 13.2 测量方法

- 保留 P5.5，在 ISR 入口置 1，写 PWM 后清 0。
- 示波器同时观察 P5.5 和 PWM。
- 制作 stress track：
  - 两 Pulse 最高频持续发声。
  - Triangle 最高频。
  - Noise register period index 0/最短 period。
  - DPCM rate 15。
  - 每个 60Hz tick 修改全部声道状态。
- 测平均、高分位和绝对最大脉宽。
- 若 P5.5 脉宽接近 Timer0 周期 70%，停止增加功能并优化。

### 13.3 48kHz 升级门槛

只有 32kHz worst case 小于约 450 system clocks，才进入 48kHz 实验。48kHz 还需要：

- 重新设计 Timer0 fractional reload 或接受并量化频率误差。
- 将 sysMs prescaler 从固定 32 改为通用相位累加器。
- 重新生成所有 increment tables。
- 重新换算 N5M sample_rate。
- 升级格式版本或明确 sample_rate 字段兼容性。

---

## 14. Player 重构

### 14.1 新 Player 数据结构

```text
N5Player
  status
  track_stream
  event_begin/event_end/event_pos
  loop_event_pos
  wait_remaining
  event_ring state
  control_queue state
  dpcm bank base/size
  parser error

N5Scheduler
  playlist_stream
  song_count
  current_song
  target_song
  mode
  switch state
```

### 14.2 可保留的 scheduler 语义

- `MODE_ORDER_PLAY`
- `MODE_LIST_ONCE`
- `MODE_SINGLE_SONG`
- Prev/Next/Direct/Stop

切歌时顺序：

1. 禁止提交新事件。
2. ISR 执行 global silence。
3. 清 control queue。
4. 清 DPCM ring 和 DPCM hot state。
5. 初始化新 track streams。
6. 预取至安全水位。
7. 从 sample 0 开始播放。

### 14.3 错误行为

发生以下错误时必须静音并停止当前曲目：

- bad magic/version。
- event overrun。
- unsupported opcode。
- DPCM range invalid。
- CRC mismatch。
- storage read failure。
- event queue 长时间 underrun。

scheduler 不应自动把损坏曲目当作正常结束循环播放。它可跳到下一曲，但必须保存 last error 供协议读取。

---

## 15. Protocol 迁移

### 15.1 保留命令

- PING
- GET_INFO
- RESET
- UPTIME
- MEM_INFO
- ADC_READ
- SYS_INFO
- PANIC
- PLAY/STOP/PREV/NEXT
- SET_SONG/GET_STATUS
- FLASH_INFO/ID/READ/WRITE/ERASE

### 15.2 删除或标记不支持

- NOTE_ON
- NOTE_OFF
- FAST_NOTE_ON
- FAST_NOTE_OFF
- ADSR_GET
- ADSR_SET
- 旧 VOICE_DUMP

为了兼容现有 host tool，旧命令可以先返回 `STATUS_NOT_SUPPORTED`，下一次 protocol major version 再移除定义。

### 15.3 新命令建议

| 命令 | 建议码 | 作用 |
|---|---:|---|
| APU_STATE | `0x07` 复用 | 返回五声道 hot/control 状态 |
| APU_WRITE | `0x09` 复用 | 调试用直接设置一个渲染字段或寄存器 |
| CHANNEL_MUTE | `0x0A` 复用 | 五声道 mute mask |
| PLAYER_DEBUG | `0x16` | queue 水位、event pos、underrun、last error |
| FORMAT_INFO | `0x17` | N5PL/N5M version 和能力位 |

`APU_STATE` snapshot 必须在短临界区内复制 ISR 共享的多字节字段，不能逐字段无保护读取。

### 15.4 SYS_INFO 调整

新增或替换字段：

- active channel mask。
- current song。
- player state。
- event queue level。
- DPCM ring level。
- event underrun count。
- DPCM underrun count。
- last parser/storage error。
- current PWM sample/raw mix。

---

## 16. 构建系统与文件迁移

### 16.1 建议新增源文件

```text
NesSynth/NesSynth.c
NesSynth/NesSynth.h
NesSynth/NesSynth.inc
NesSynth/NesSynthAsm.s
NesSynth/NesSynthStep.inc
NesSynth/NesMixerTable.c
NesSynth/NesMixerTable.h
NesSynth/NesRateTable.c
NesSynth/NesRateTable.h
NesSynth/NesSynth_testbench.s
Player/N5Player.c
Player/N5Player.h
Player/N5Format.h
tools/chiptune/*.py
docs/N5MFormat.md
docs/NES5Testing.md
```

### 16.2 建议移除生产构建的源文件

```text
Synthesizer/WaveTable.c
Synthesizer/NonlinearMapTable.c
Synthesizer/CompressorGenerated.c
旧 SynthCore.c
旧 SynthCoreAsm.s
旧 Synth.inc
```

可以在迁移分支中暂时并存，通过 `SYNTH_ENGINE=wavetable|nes5` Makefile 变量切换；完成验收后删除旧引擎。长期保留双引擎会扩大维护面，不建议作为最终状态。

### 16.3 Makefile 要求

- 新增 `-INesSynth`。
- 为所有 `.inc` 建立手工依赖。
- 新增 `make generate-nes-tables`。
- 新增 `make pack-n5m INPUT=...` 或独立工具命令。
- RUN_TEST 自动选择 `NesSynth_testbench.s`，不编译真实 Timer0 ISR。
- production 与 RUN_TEST 仍禁止并行共用一个 worktree。

---

## 17. 测试策略

### 17.1 Host 格式测试

每个 parser 使用损坏输入测试：

- 截断 header。
- 非法 version。
- offset overflow。
- 超长 data block。
- 未知 opcode。
- loop 指向 payload 中间。
- DPCM virtual range 越界。
- CRC 错误。

Round-trip 测试：

```text
FTM/VGM -> ApuLog -> N5M -> n5m_dump -> equivalent timeline
```

### 17.2 定点合成器测试

为每声道建立独立 C reference：

- Pulse 四种 duty 的前 64 个 step。
- Pulse timer 边界：0、7、8、0x7FF。
- Triangle 32-step sequence 和 gate hold。
- Noise long mode 前 32767 states 周期。
- Noise short mode的已知序列片段。
- 每个 Noise rate 的 phase overflow 次数。
- DPCM 全 0、全 1、`0x55`、`0xAA`、边界饱和。
- DPCM rate 15 双 bit/sample 情况。
- DPCM loop、stop、empty buffer。
- Mixer 所有 31 Pulse index 和 203 TND index。
- PWM saturation 和 silence=128。

### 17.3 汇编对照测试

沿用当前 `Synth_testbench.s` 的思想：

- 初始化固定 hot state。
- 调用一次 `NesSynthAsmStep()`。
- 与独立 C reference 比较所有 phase、LFSR、DPCM、mix 和 PWM 字段。
- 使用伪随机状态跑数千组。
- 特别覆盖 carry、24-bit wrap 和负 DC filter state。

### 17.4 Player 测试

- WAIT_U8/U16/U24。
- 同 timestamp 多事件合并。
- queue wrap。
- loop 无 sample drift。
- end 静音。
- prev/next 清 DPCM。
- storage short read。
- event underrun 后恢复或停止策略。
- `uint16_t` countdown wrap。

### 17.5 Golden 曲目

至少准备以下 FTM：

1. 两 Pulse 四 duty 扫描。
2. Triangle 音阶和低音持续音。
3. Noise 16 rates、long/short mode。
4. DPCM 16 rates、loop/start offset/delta。
5. Volume/arp/pitch/hi-pitch/duty sequences。
6. Vibrato、tremolo、slides、portamento。
7. Note delay、note cut、jump、skip、halt。
8. 五声道满载 stress track。
9. 长循环曲目，用于检查 30 分钟累计漂移。

每首保存：

- 原 `.ftm`。
- 参考 `.vgm`。
- 编译 `.n5m`。
- PC reference `.wav`。
- 硬件录音或逻辑分析记录。

### 17.6 板级测试

- 示波器测 Timer0 sample rate。
- P5.5 测 ISR 最坏脉宽。
- PWM 输出静音中点。
- 五声道独立 mute。
- DPCM 连续播放 10 分钟无 underrun。
- UART 连续查询时无音频断裂。
- 切歌无随机残留 DPCM。
- 内置与 SPI 后端输出事件序列一致。
- 低电压和冷启动下 SPI 自动检测不误选。

### 17.7 听感 A/B

不能只比较最终 WAV hash，因为采样率、滤波和相位初始值可能不同。分层比较：

1. tracker control timeline 是否一致。
2. 每声道 period/duty/volume/DPCM trigger 是否一致。
3. PC fixed-point renderer 与 MCU UART trace 是否一致。
4. 数字 PWM sample 与 reference 是否在定义容差内。
5. 最后比较模拟录音频谱和主观听感。

---

## 18. 分阶段交付计划

### Phase 0：规格冻结与基线

交付物：

- 本文档评审完成。
- 确认第一版仅 NTSC 2A03 五声道。
- 收集 8..12 个代表性 `.ftm`。
- 记录当前固件 Code/XRAM/stack/ISR 波形。
- 确认 Fami32 只作对照，不复制源码。

退出条件：

- 输入格式、听感目标、是否要求硬件级 APU 仿真没有歧义。

### Phase 1：VGM/APU Log/N5M 工具链

交付物：

- VGM reader。
- ApuLog normalization。
- N5M compiler/dump。
- N5PL packer。
- DPCM bank 重排和 CRC。
- PC reference player 的事件层。

退出条件：

- Fami32 导出的基础五声道 VGM 能稳定编译和 dump。
- loop、DPCM 和 timestamp 测试通过。

### Phase 2：四个合成声道

先不做 DPCM：

- Pulse 1/2。
- Triangle。
- Noise。
- NES LUT mixer。
- 8-bit PWM。
- APU_STATE/CHANNEL_MUTE。

退出条件：

- 四声道 stress track 连续播放。
- ASM 与 C reference 一致。
- ISR worst case 小于预算。

### Phase 3：Sample-accurate Player

交付物：

- N5M Player。
- event ring/control queue。
- sample countdown 调度。
- N5PL scheduler。
- internal/SPI backend。

退出条件：

- 30 分钟循环无累计节拍漂移。
- UART 压力下 event underrun 为 0。

### Phase 4：DPCM

交付物：

- DPCM hot engine。
- 16 rates。
- start/stop/loop/offset/initial level。
- DPCM ring 与优先 refill。
- underrun diagnostics。

退出条件：

- rate 15 连续播放无 underrun。
- 256-byte ring 水位满足安全目标。
- 切歌、stop、panic 不残留 sample。

### Phase 5：FTM 用户入口

交付物：

- `ftm_frontend.py`。
- 外部转换后端探测。
- `.ftm -> .n5p -> flash` 一条命令。
- 兼容性报告。
- 对不支持 expansion/multitrack/version 的清晰错误。

退出条件：

- 代表性 FTM 集合可一键生成并烧录。
- 工具输出可复现，不依赖 GUI 手工步骤作为唯一流程。

### Phase 6：协议、文档和清理

交付物：

- 更新 Protocol 文档和 Python CLI。
- 更新 Testing 文档。
- 删除或隔离旧 ADSR UI/tools。
- 更新 AGENTS.md 架构和内存布局。
- 清理旧 wave/ADSR/compressor production sources。

退出条件：

- production build、RUN_TEST build、host tests 全部通过。
- 工作树无生成物混淆。
- README 中用户流程与实际命令一致。

### Phase 7：可选精度升级

按测量结果选择：

- 48kHz。
- PAL。
- 完整 APU envelope/sweep/frame counter。
- NSF/VGM 直接输入。

此阶段不得阻塞第一版五声道播放器交付。

---

## 19. 风险清单与应对

### 19.1 FTM 兼容性

风险：FTM 版本、fork 和 expansion 差异大。

应对：

- 初始只承诺 header `0x0440` 和基础 2A03。
- host 工具显式生成 compatibility report。
- 以标准 VGM/NSF 作为中间层隔离 parser。
- 不在 MCU 中实现 FTM parser。

### 19.2 Fami32 行为并非标准

风险：Fami32 是参考项目，不一定与官方 FamiTracker 完全一致。

应对：

- 同时使用官方 FamiTracker/Dn-FamiTracker/FamiStudio 输出做三方比较。
- Fami32 只作为一个 oracle，不作为规范本身。
- 分别比较 tracker timeline 和音频输出。

### 19.3 Noise 高频混叠

风险：32kHz 且无过采样时，Noise 高频听感与 72kHz reference 不同。

应对：

- 正确推进多次 LFSR，避免单纯降低 noise clock。
- 评估低成本 box average 或 2x 子采样，仅用于 Noise。
- 以 ISR 余量决定是否升 48kHz。

### 19.4 DPCM underrun

风险：软件 SPI、协议操作或长临界区阻止 refill。

应对：

- 独立 256-byte ring。
- 高低水位。
- 小 burst。
- 播放时禁止 erase/write。
- 暴露 underrun counter。

### 19.5 ISR 超时

风险：最短 Noise period、DPCM 双 bit 和控制事件同 sample 叠加。

应对：

- 汇编热路径。
- P5.5 实测。
- control delta 聚合。
- 避免 ISR 中通用除法、乘法和 MOVX bulk copy。
- 设定硬退出门槛，不以“听起来还行”代替时序验证。

### 19.6 8-bit PWM 动态范围

风险：NES 非线性混音量化后低电平细节不足或削波。

应对：

- 离线搜索 LUT master scale。
- golden 曲目统计峰值分布。
- 可选 TPDF/1-bit dither，但只在周期有余量时开启。
- 不使用会泵动的动态 compressor。

### 19.7 授权风险

风险：Fami32 当前未声明根许可证。

应对：

- clean-room 实现。
- 记录参考 commit 和观察结论。
- 不复制源码片段、表或注释。
- 若需复用，先取得书面授权。

---

## 20. 验收标准

第一版只有同时满足以下条件才算完成：

### 功能

- 用户可从 `.ftm` 生成可烧录 N5PL 镜像。
- 两 Pulse、Triangle、Noise、DPCM 均能独立与同时播放。
- instrument sequence 和列出的常用 effects 行为正确。
- 多歌曲 Prev/Next/SetSong/loop/stop 正确。
- internal 与 SPI backend 均可播放。

### 正确性

- C reference 与汇编逐样本测试全部通过。
- DPCM known vectors 全部通过。
- Noise known sequence 全部通过。
- mixer table 全索引测试通过。
- 30 分钟循环的控制事件时间误差不累计超过 1 output sample。

### 实时性

- Timer0 ISR worst case 小于周期的 70%。
- 正常播放 event underrun=0。
- 正常播放 DPCM underrun=0。
- UART 状态轮询不产生可听断裂。

### 资源

- Code < 52KB。
- XRAM < 2560 bytes。
- C stack 可用空间 >= 128 bytes。
- 不改变 `EXTRAM=0` 前提。

### 稳定性

- 连续播放 8 小时不崩溃、不越界、不失去串口响应。
- 损坏 N5M 文件安全停止并报告错误。
- Stop/Panic/切歌后 PWM 回到稳定静音中点。

---

## 21. 开工前需要最终确认的产品决策

以下决策不会阻止前两阶段研究，但在格式冻结前必须确认：

1. 第一版是否只要求 NTSC 2A03 五声道。
2. `.ftm` 是否允许通过 PC 工具编译，还是强制要求设备直接存原始 FTM。本文强烈推荐前者。
3. 是否接受“FamiTracker 听感兼容”而非完整 cycle-accurate APU。
4. DPCM 是否为第一版必须项。本文按必须项规划。
5. 是否必须保留现有 MIDI/NoteOn 串口命令。本文建议废弃。
6. 外置 SPI Flash 是否为主要歌曲存储。若是，应优先围绕 SPI 流式播放优化。
7. 是否需要保留当前可视化 PWM4 输出。本文默认保留，并改为显示最终 NES mix envelope。

---

## 22. 推荐的第一批实际任务

按依赖关系，第一批代码任务应严格限制在 host 和 reference 层，不立即删除当前固件：

1. 收集三首无 DPCM FTM 和三首有 DPCM FTM。
2. 用 Fami32 导出 VGM，记录版本和导出参数。
3. 实现只读 VGM parser 和 `vgm_dump`。
4. 定义 `ApuLog` 和测试 fixture。
5. 实现 N5M v1 writer/reader/dump。
6. 实现 PC 上的五声道 fixed-point reference renderer。
7. 生成 mixer/noise/DPCM rate tables。
8. 用 reference renderer 导出 WAV，与 Fami32/FamiTracker 做分层 A/B。
9. 确认控制状态流足以重现目标曲目后，再冻结 N5M v1。
10. 创建固件 `nes5` 分支或编译开关，开始替换 Synth ISR。

先完成这十项，可以在不破坏当前可工作的音乐盒固件的前提下，验证最重要的格式和听感假设。

---

## 23. 参考资料

- Fami32：<https://github.com/jsjsjsjsjsjsjson/Fami32>
- FamiStudio 导入说明：<https://famistudio.org/doc/import/>
- FamiStudio 导出说明：<https://famistudio.org/doc/export/>
- FamiStudio CLI：<https://famistudio.org/doc/cmdline/>
- NESdev APU：<https://www.nesdev.org/wiki/APU>
- NESdev APU Mixer：<https://www.nesdev.org/wiki/APU_Mixer>
- NESdev DMC：<https://www.nesdev.org/wiki/APU_DMC>
- VGM specification：<https://vgmrips.net/wiki/VGM_Specification>

外部资料用于确认格式和硬件行为；最终固件的定点算法、表生成器、parser 和测试必须保存在本仓库中，并能够独立复现。
