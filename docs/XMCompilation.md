# XM 如何编译成 T10P：教学导读

本文解释主机端把 FastTracker II XM 变成设备可播放的 `T10P` 时，每一步在做什么、
为什么要这样做，以及哪些语义被保留、哪些被有意降级。目标读者是想理解转换器、
调试曲目或扩展前端的人，而不是只想下载一首歌到板子上的使用者。

若你只需要操作步骤，请先看 [UsingXM.md](UsingXM.md)。若你需要二进制字段表，请看
[T10Format.md](T10Format.md)。若你关心“哪些 XM 语义尚未实现”，请看
[XMCompatibilityAudit.md](XMCompatibilityAudit.md)。

实现入口：

- 命令行：`tools/tracker10_tool.py`
- XM 解析与 lowering：`tools/tracker10/xm.py`
- IR 与编码：`tools/tracker10/format.py`

## 1. 先建立产品边界

Tracker10 不是“在 8051 上跑一个迷你 MilkyTracker”。它把 XM 当作**源语言**，把
T10 当作**目标机器码**。主机编译器负责：

1. 读懂 XM 的文件结构；
2. 把 tracker 语义压到设备能实时执行的子集；
3. 把采样离线变成 16 点波表或 16 kHz PCM；
4. 写出可校验的 `T10P` 容器。

MCU 端只做：

- 顺序读 order / pattern；
- 按 tracker tick 跑 effect、包络和 release；
- 把结果变成十个固定声部的控制字；
- 在 32 kHz 上从 XRAM 音频环取一个字节写 PWM。

因此：

- MCU **从不**解析 `Extended Module:` 头、delta 编码采样或 volume column；
- MCU **从不**做重采样、插值、归一化或采样尾部 fade；
- 不支持的 effect 在主机端**明确失败**，而不是静默变成错误音乐。

可以把整条链路想成编译器：

```text
源文件 (XM)
  -> 词法/语法 (parse_xm)
  -> 语义分析 (analyze_xm)
  -> 中间表示 lowering (compile_xm -> Song IR)
  -> 目标码生成 (encode_track + pack_playlist)
  -> 链接进固件 (scoreList.c) 或保存为 .t10p
```

## 2. 一张图看完整流水线

```text
song.xm 字节流
        |
        v
  parse_xm()
    校验 magic / version 1.04
    解 order、pattern 打包单元、乐器头
    解 8/16-bit delta 采样为 signed 8-bit PCM
        |
        v
  XmModule
    title, orders, restart, channels
    speed, bpm, linear_frequency
    patterns[pattern][row][channel] = Cell
    instruments[] = XmInstrument + XmSample
        |
        +--> analyze_xm()  -- 统计 effect、生成 warnings（不写文件）
        |
        v
  compile_xm()
    1. 拒绝 >10 声道
    2. 统计每个乐器最常触发的 note
    3. 只保留 order 真正用到的 pattern 并重编号
    4. 规范化每个 cell（volume column / effect lowering）
    5. 把 sample default volume 写回 instrument 事件
    6. 循环采样 -> 16 点波表；长 one-shot -> 16 kHz PCM
    7. volume envelope -> 最多 16 点定时宏 + fadeout
    8. 组装 Song IR
        |
        v
  encode_track(Song) -> 单首 T10M v4（含 T10R 资源与 body CRC）
        |
        v
  pack_playlist([track]) -> T10P（playlist 容器）
        |
        +--> song.t10p
        +--> emit_c() -> scoreList.c（链接进 Code Flash）
```

命令行对应关系：

```bash
# 只做解析 + 完整 compile 校验，不写盘
python3 tools/tracker10_tool.py inspect-xm song.xm

# 写出单曲 T10P，可选同时生成 C 数组
python3 tools/tracker10_tool.py compile song.xm song.t10p \
  --c-output scoreList.c

# 只读校验已有 T10P
python3 tools/tracker10_tool.py inspect-t10p song.t10p
```

`inspect-xm` 并不是“随便看看头信息”。它会真正调用 `compile_xm()` 和
`inspect_track()`；能过预检，才说明这首曲在当前编译器语义下可装入设备格式。

## 3. XM 源文件里有什么

先用 tracker 作者的视角看 XM，再看编译器如何切分它。

### 3.1 歌曲骨架

| XM 概念 | 含义 | 进入 T10 后 |
|---|---|---|
| Order list | 播放顺序，每项是 pattern 编号 | 仍是 order 表，可压缩未引用 pattern |
| Pattern | 二维网格：行 × 声道 | 仍是 pattern，但固定 10 声道 |
| Restart | 循环回到的 order 下标 | 同义；空白尾 pattern 可被识别为 one-shot 结束 |
| Speed | 每行多少 tracker tick | T10M `initial speed` |
| BPM | tick 时间基准 | T10M `initial BPM`；设备用整数余数精确调度 |
| Frequency table flag | linear / Amiga period | 变成 T10M flag bit 1（effect 模型） |

关键点：**pattern 重用仍然存在**。编译器不会把整首歌展开成“按采样时间戳写振荡器”
的事件流。那是旧 T10M v1 的做法，已被 v4 语义曲谱取代。

### 3.2 一个 pattern cell

XM 的一个 cell 最多携带五类信息：

```text
note | instrument | volume column | effect | parameter
```

文件里常见两种编码：

1. **未压缩**：先写 note，再固定跟 4 字节；
2. **压缩**：最高位为 1 的 marker，后跟按 bit 出现的字段。

`parse_xm()` 把两者都还原成同一个 Python 对象：

```python
Cell(note, instrument, volume, effect, parameter)
```

这里的 `volume` 仍是 XM volume-column 原值（例如 `0x20` 表示设定音量 16），
还没有变成 T10 的归一化音量字节。

### 3.3 乐器与采样

一个 XM instrument 可以包含：

- 96 项 note keymap（哪个音映射到哪个 sample）；
- 若干 sample（长度、loop、default volume、finetune、relative note）；
- volume envelope 点列、sustain/loop、fadeout；
- panning envelope、automatic vibrato（当前大多不渲染，见 warnings）。

采样数据在文件中是 **delta 编码**：

- 8-bit：每个字节是相对前一个采样的差值；
- 16-bit：每个 little-endian 有符号差值先累加，再右移 8 位变成 8-bit。

解析后的 `XmSample.values` 已是 signed 8-bit 幅度序列，后续所有波表/PCM 分析
都基于它。

## 4. 解析阶段：从字节到 XmModule

`parse_xm(data: bytes) -> XmModule` 是严格的边界检查解析器，不是“尽量容错的
播放器”。

### 4.1 头部硬条件

- 前缀 `Extended Module: `
- 偏移 37 为 `0x1A`
- 版本必须是 `0x0104`（即 1.04）
- song length、channel count、speed、bpm 落在合理范围
- order 中每个 pattern 索引必须存在

任一失败都会抛 `XmError`。这保证后续 lowering 看到的永远是结构完整的模块。

### 4.2 Pattern 解包

对每个 pattern：

1. 读 header length、packing type、row count、packed size；
2. 按 `rows × channels` 逐 cell 解包；
3. 要求解包游标刚好消耗完 packed size。

当前拒绝 packing type 非 0 的非标准变体。得到的是完整网格，空 cell 用全 0 表示。

### 4.3 乐器与采样

对每个 instrument：

1. 读 instrument header 与 sample count；
2. 若有采样，读 keymap、envelope、fadeout、vibrato 字段；
3. 校验 envelope 点单调、level ≤ 64、sustain/loop 下标合法；
4. 读每个 sample header，再按 length 取 delta 数据并解码；
5. 校验 loop 范围、16-bit 对齐、keymap 不越界。

解析结束时要求 `cursor == len(data)`，不允许尾部垃圾数据。

### 4.4 教学示例：delta 采样

假设 8-bit 原始字节是：

```text
0x00, 0x10, 0xF0
```

累加过程：

```text
acc = 0
+0x00 -> 0
+0x10 -> 16
+0xF0 作为 -16 -> 0
```

得到幅度序列 `(0, 16, 0)`。16-bit 采样同理，只是先按 16-bit 累加再 `>> 8`。
编译器后续只看见这个还原后的波形，不再关心 delta。

## 5. 中间表示：为什么需要 Song IR

编译器并不直接从 XM 字节写 T10M。它先构造 `format.Song`：

```python
Song(
  orders, restart, speed, bpm,
  patterns,          # 固定 10 声道 Cell 网格
  instruments,       # 固定 48 字节语义的 Instrument
  amiga_effects,     # True = 源是 Amiga period 模型
  waves,             # 最多 16 张 16-point 波表
  pcm_samples,       # 16 kHz signed 8-bit one-shot
)
```

这样做有三个好处：

1. **前端可替换**：将来可以有 IT/S3M/手写 IR 前端，只要 lowering 到同一 `Song`；
2. **编码单一**：`encode_track()` 只懂 T10 语义，不懂 XM；
3. **可测试**：主机测试可以构造 IR，不依赖真实 XM 文件。

注意 IR 里 volume 的特殊约定：

- Python IR：`1..65` 表示 tracker 音量 `0..64`，`0` 表示“该字段缺省”；
- 落盘时减 1，变成设备上的 `0..64`；
- 解码时再加 1 回到 IR。

这是为了让“缺省字段”和“音量 0”能区分开。

## 6. Pattern 与 effect 的 lowering

这是“源语言语义”变成“设备语义”的核心。

### 6.1 声道数

- XM `channels > 10`：直接失败；
- XM `channels < 10`：每行右侧补空 `Cell()`，凑满 10 路。

设备永远是十个固定物理声道，没有动态 voice allocation。

### 6.2 只用到的 pattern

编译器遍历选定 order，收集首次出现的 pattern，建立映射：

```text
源 pattern 编号 3, 7, 3, 1
  -> used = [3, 7, 1]
  -> 新 order = [0, 1, 0, 2]
```

未进入 order 的 pattern 不会进入 T10M。这能显著减小曲库中“库内有大量未用
pattern”的模块体积。

### 6.3 `_normalize_cell()`：每个格子的规则

对每个源 cell，编译器做一次确定性变换。

**Volume column**

| XM volume column | 结果 |
|---|---|
| `10..50` | 变成绝对音量字段（IR 中 `1..65`） |
| `6x` | 若无主 effect，降为 `A0x` volume slide down |
| `7x` | 若无主 effect，降为 `Ax0` volume slide up |
| `8x/9x` | fine slide：丢弃，并在 analyze 阶段 warning |
| `Cx` | panning：丢弃（单声道输出） |
| 其他 | 编译失败，报 `pattern:row:channel` |

**Main effect**

| XM | 设备侧 |
|---|---|
| `0xy` arpeggio | 保留 |
| `1xx/2xx` pitch slide | 保留 |
| `3xx` tone portamento | 保留（不重触发相位） |
| `4xy` vibrato | 保留 |
| `6xy` vib+volslide | 只保留 volume slide 为 `Axy`，vibrato 丢弃并 warning |
| `8xx` panning | 清空，warning |
| `9xx` sample offset | 清空，warning（总是从采样开头） |
| `Axy` volume slide | 保留 |
| `Bxx` position jump | 保留；目标 order 必须在选定范围内 |
| `Cxx` set volume | 转成 volume 字段，effect 清空 |
| `Dxx` pattern break | 保留；十进制行号，当前上限 row 63 |
| `E9x` retrigger | 保留 |
| `ECx` note cut | 保留 |
| `EDx` note delay | 近似为 tick 0 触发，warning |
| `E00` | 视为无操作 |
| `Fxx` speed/BPM | 保留 |
| 其他 | 编译失败 |

设计原则可以记成一句话：

> **能无损映射的保留；有意近似的必须 warning；未知语义直接失败。**

### 6.4 Sample default volume 的特殊处理

在 FT2 中，sample default volume 的真实语义是：

> 当选中该 sample 时，**初始化 channel volume**。

它不是“永远乘在乐器上的固定增益”。

因此编译器：

1. 先根据该乐器最常用 note 选出 sample，读取其 default volume；
2. 若某个 cell 写了 instrument，且没有显式 volume，就把 default volume 写进
   该 cell 的 volume 字段（满量程 64 且同时有 note 时可省略）；
3. T10 instrument 的 `gain` 固定为 unity `31`。

这样设备上的 channel volume slide / `Cxx` 与 XM 一致，不会把 default volume
错误地变成二次增益。

### 6.5 空白 restart 结尾

部分曲目把 restart 指到最后一个、且只被引用一次、内部几乎没有音乐事件的
pattern，用来表示“播完结束”。编译器识别该模式后：

1. 在该 pattern 第一行强制写入全通道 key-off（note 97）；
2. 关闭 T10M loop 标志；
3. analyze 阶段给出 one-shot ending warning。

普通循环曲不受影响。

## 7. 乐器编译：从采样到发声资源

这是理解“为什么听起来像芯片波表，而不是完整采样器”的关键。

### 7.1 先为每个乐器选一个代表 note

```text
遍历选定 order 中的全部 note 事件
  按“当前通道活跃 instrument”统计 note 直方图
  取 most_common 作为该乐器的代表 note
  若从未被触发，默认 note = 49（中部八度）
```

然后用 keymap[note-1] 选出 sample。当前版本**不会**把多 key zone 拆成多个
T10 instrument；多采样乐器会 warning，并只保留最常用 note 映射到的那一个
sample。

### 7.2 三条资源路径

```text
有 loop 且 loop_length >= 8
  -> 16-point 歌曲波表 + volume envelope/fadeout

无有效 loop，且采样长度 > 256
  -> 16 kHz PCM one-shot

其他短采样
  -> 仍抽成 16-point 波表（可能音色损失）
```

无采样的空乐器会得到 `gain=0` 的静音 instrument。

### 7.3 波表抽取 `_wave_table()`

目标：用 16 个 signed 字节描述“一个振荡周期”。

步骤：

1. 取 loop 段；无 loop 则取整个 sample；
2. 若源很长（>256），做有界自相关，在 lag 8..min(256, N/2) 中找误差最小周期；
3. 在代表周期上等间隔取 16 点；
4. **原样保留幅度**，不做 peak normalize，也不做 DC centering。

为什么不做归一化？因为 XM 的响度平衡大量依赖采样本身的相对幅度。若编译器
把每张波表都拉满，鼓和主音的相对关系会乱。

去重：完全相同的 16 点序列只存一份，instrument 的 `mode` 指向波表 ID
`0..15`。超过 16 张不同波表则失败。

### 7.4 PCM one-shot `_pcm_sample()`

长而无 loop 的采样被当作打击乐/一次性事件：

1. 用代表 note + sample.relative_note + finetune/128 计算源播放速率；
2. 以 16 kHz 为目标率做 16-tap windowed-sinc 低通重采样；
3. 去掉 DC，但保留动态范围；
4. 末尾最多 4 ms（64 点）线性 fade 到 0，避免固定声部静音时的末样 click。

这里 **会**使用 relative note / finetune，因为 PCM 仍是“按某个音高录好的录音”。

对比波表路径：**故意丢弃** relative note / finetune。原因是波表已经不再是原
录音，而是一个周期振荡器；若再叠加采样校准，会把谱面上的 note 再移调一次。

设备端 PCM 触发时，该 tracker 通道就切换到 PCM 模式；没有额外的动态 PCM 分配
器。最多十路可同时 one-shot。

### 7.5 Volume envelope `_xm_envelope()`

XM envelope 是“tick → level(0..64)”的折线。设备侧 cap 为 16 点宏：

```text
step = max(1, (last_tick + 14) // 15)
在 0..last_tick 上按 step 均匀采样，最多 16 点
level 映射为 0..32（除以 2 四舍五入）
sustain/loop 点按最近宏下标映射
```

同时保留：

- enable / sustain / loop 标志；
- fadeout 原值（设备用 32768 起始量程，与 FT2 实际内部范围一致）。

若 envelope 未启用，宏退化为单点 `32`，key-off 时按 tracker 规则直接静音。

### 7.6 最终 Instrument 记录

每个 T10 instrument 固定 48 字节，便于整份拷进 XRAM：

```text
mode/gain/relative_pitch
volume macro 元数据 + 16 字节数值
pitch macro 元数据 + 16 字节数值
fadeout
```

`mode` 选择发声源：

| 值 | 含义 |
|---:|---|
| `0x00..0x0F` | 歌曲波表 ID |
| `0x80..0xFC` | PCM ID = mode - 0x80 |
| `0xFE/0xFF` | 长/短周期噪声（当前 XM 前端通常不用） |

## 8. 编码阶段：Song → T10M → T10P

### 8.1 T10M：一首语义曲

`encode_track(song)` 生成单首 T10M v4，布局概念如下：

```text
[48 字节 T10M header]
[order 表：每字节一个 pattern 索引]
[pattern 目录：每项 8 字节]
[instrument 表：每项 48 字节]
[T10R 资源区：波表 + PCM 目录 + PCM 数据]
[pattern 正文：按目录顺序紧挨存放]
```

header 中的 body CRC32 覆盖从 offset 48 到文件末尾。主机工具会算 CRC；8051
启动时不算整曲 CRC，而靠范围与表项校验控制成本。

### 8.2 Pattern 正文如何更省空间

每一行：

```text
uint16 changed_mask   # bit0..9 = 十个声道是否有非空 cell
随后按声道升序写 cell token
```

每个 cell token：

```text
field_mask
  bit0 NOTE       -> 1 字节 note (1..96, 97=key-off)
  bit1 INSTRUMENT -> 1 字节 1-based instrument
  bit2 VOLUME     -> 1 字节 0..64
  bit3 EFFECT     -> effect + parameter 各 1 字节
```

空行只占 2 字节 mask。这是 tracker 曲谱在 Flash 里仍然紧凑的主要原因之一。

### 8.3 T10P：播放列表容器

`pack_playlist([track])` 在单曲场景下生成：

```text
T10P header (16 字节)
  magic "T10P", version 4, track_count, total_size
directory
  每项: T10M offset + size
track 0 的完整 T10M 字节
```

当前工具链通常一次只打包一首。格式本身允许多 track；设备按目录打开其中一首。

### 8.4 为什么还要 `scoreList.c`

当前板子的 T10M v4 音频资源必须放在内部 Code Flash，ISR 用 `MOVC` 直接读。
没有 SPI NOR 时，`.t10p` 文件本身不会被 MCU 运行时读取。

`emit_c()` 把整个 T10P 图像变成：

```c
__code const unsigned char Score[N] = { ... };
__code const uint32_t ScoreSize = <N>UL;
```

链接进固件后，播放器从 `Score` 起始地址打开 playlist。因此：

- `.t10p`：给人检查、做曲库、做回归哈希；
- `scoreList.c`：给当前无外置 Flash 的板子真正播放。

## 9. 用“心智实验”走完一小段

假设一首极简 XM：

```text
1 声道, speed=6, bpm=125, linear frequency
order: [0]
pattern 0, row 0, ch0: note=C-4, ins=1, vol=--, effect=---
instrument 1: 一个 loop 方波采样, default volume=64
  volume envelope: 0->64, 16->32, sustain at point1
```

编译器大致产出：

1. `parse_xm` 得到 1 个 pattern、1 个乐器、解码后的 loop 采样；
2. 代表 note 为 C-4；keymap 选出 sample 0；
3. loop 有效 -> `_wave_table` 得到 16 点方波，`waves=[wave0]`；
4. envelope 被采样成若干 `0..32` 点，step 由总 tick 跨度决定；
5. cell 规范化后仍是 note+instrument（volume 可因 64 满量程省略）；
6. 右侧补 9 个空声道；
7. `Song` 编码为 T10M，再包进单曲 T10P。

设备播放时：

- tick 0 解码该行，触发 instrument 1，重置包络；
- 之后每个 tick 推进 envelope / effect；
- 把当前音高换成 24-bit phase increment，音量写成控制队列；
- 主线程预渲染到音频环，ISR 只取字节。

你在示波器或耳朵里听到的“方波音色”，来自那 16 点表，而不是 XM 原始采样缓冲。

## 10. 保留什么、丢掉什么

### 10.1 有意保留

- order / pattern 重用结构；
- tracker tick 与 speed/BPM 调度；
- 受支持的 effect 与参数记忆模型；
- linear / Amiga 两种 slide/vibrato 强度模型（设备侧用 note 域实现）；
- volume envelope 的 sustain/loop/fadeout 大意；
- 线性幅度音量语义（不是 dB 域）；
- 采样相对响度（不做强制 peak normalize）。

### 10.2 有意降级（会 warning）

- 单声道：panning effect / volume-column panning / panning envelope；
- `6xx` 只留 volume slide；
- `9xx` 总是从开头；
- `EDx` 在 tick 0 触发；
- volume-column fine slide 丢弃；
- 多采样只取最常用 key zone；
- instrument auto-vibrato 不渲染；
- 复杂 envelope 压到 ≤16 点。

### 10.3 直接拒绝

- 超过 10 声道；
- 未知 effect / volume-column；
- 超过 16 张去重波表；
- 损坏或非 1.04 XM；
- 非法 envelope / loop / keymap。

“失败比错音更好”是这条工具链的产品决策。

## 11. 如何自己验证理解

### 11.1 看预检 JSON

```bash
python3 tools/tracker10_tool.py inspect-xm song.xm | less
```

重点字段：

| 字段 | 读法 |
|---|---|
| `frequency_mode` | linear 还是 amiga effect 模型 |
| `effects` / `volume_commands` | 源曲实际用了哪些命令 |
| `warnings` | 会响但有近似的地方 |
| `compiled.waves` | 去重后波表数，上限 16 |
| `compiled.pcm_samples` / `pcm_bytes` | one-shot 数量与 Flash 占用 |
| `compiled.track_bytes` | 单首 T10M 大小 |
| `compiled.loop` | 是否循环；one-shot ending 时为 false |

### 11.2 对比编译前后容器

```bash
python3 tools/tracker10_tool.py compile song.xm /tmp/song.t10p
python3 tools/tracker10_tool.py inspect-t10p /tmp/song.t10p
```

`inspect-t10p` 会验证 header、目录范围、T10M、CRC、instrument/resource/pattern。
同一源文件、同一工具版本应得到**逐字节相同**的 T10P（确定性编译）。

### 11.3 从代码顺着读

建议阅读顺序：

1. `tools/tracker10_tool.py` — 命令如何串起来；
2. `xm.parse_xm` — 文件结构；
3. `xm._normalize_cell` — effect 边界；
4. `xm._compile_instrument` / `_wave_table` / `_pcm_sample` — 音色从哪来；
5. `xm.compile_xm` — 总装配；
6. `format.encode_track` / `pack_playlist` — 字节如何落下；
7. `docs/T10Format.md` — 与设备阅读器字段一一对应。

### 11.4 主机参考播放器

`tools/tracker10/reference.py` 是按 T10 语义推进的主机参考实现，用于测试
“编码后的曲谱在 tick 级应如何运动”。它验证的是 **T10 VM**，不是完整 FT2。

## 12. 常见误解

**“T10P 就是压缩后的 XM。”**  
不是。结构相似（order/pattern/instrument），但采样、effect、声道模型和资源
布局都已重写。

**“relative note 丢了所以音高一定错。”**  
对波表路径，丢弃是为了避免二次移调；对 PCM 路径，校准被烤进 16 kHz 重采样。

**“default volume 应该写在 instrument gain 里。”**  
FT2 里它是 channel volume 初始化。写进 gain 会让后续 slide 语义错误。

**“只要生成了 .t10p，板上就会播新歌。”**  
当前无 SPI 板子播的是链接进去的 `scoreList.c`。要用 `compile --c-output` 或
`make flash-xm` 工作流。

**“warning 等于失败。”**  
失败才不能生成；warning 表示可播放但存在文档化近似。曲库里大量模块属于后者。

## 13. 和仓库其他文档的分工

| 文档 | 职责 |
|---|---|
| 本文 `XMCompilation.md` | XM→T10P 编译过程教学 |
| [UsingXM.md](UsingXM.md) | 从选曲到烧录的操作手册 |
| [T10Format.md](T10Format.md) | T10P/T10M/T10R 二进制规范 |
| [Tracker10Architecture.md](Tracker10Architecture.md) | 设备运行时与编译管线总览 |
| [PythonTools.md](PythonTools.md) | 工具参数与副作用 |
| [XMCompatibilityAudit.md](XMCompatibilityAudit.md) | 兼容缺口与审计依据 |
| [XMCollection.md](XMCollection.md) | 曲库来源与批量筛选 |

## 14. 小结

XM 到 T10P 的本质不是“换一个容器存同样的字节”，而是一次面向 8051 实时约束
的 **tracker 语义编译**：

1. 解析并校验完整 XM；
2. 把 pattern/effect 降到设备 VM 能执行的集合；
3. 把采样离线变成波表或 PCM 资源；
4. 保留 order/pattern/instrument 的高层结构；
5. 编码为可校验的 T10M，再装进 T10P。

读懂这条链路后，你就可以预测：一首新 XM 会在哪一步失败、会发出哪类 warning、
Flash 主要被 pattern 还是 PCM 吃掉，以及为什么设备上的声音既像原曲、又明显
是 8-bit 波表 tracker 而不是全功能采样器。
