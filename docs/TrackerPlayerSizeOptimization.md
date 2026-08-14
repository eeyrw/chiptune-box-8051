# TrackerPlayer Flash 体积优化记录

本文记录 Tracker10 固件在 **STC8H3K64S2（Code Flash 65024 字节）** 上，
为塞入大曲（尤其 Unreeeal Superhero 3）而对 `TrackerPlayer` 与曲谱访问路径
做的体积压缩。重点是：**在不大改 tracker 语义的前提下**，用测量驱动的方式
削减 SDCC large + `--stack-auto` 带来的膨胀，并用少量手写汇编补齐 C 编译器
做不好的热点。

相关约束见 [AGENTS.md](../AGENTS.md)、[T10Format.md](T10Format.md)、
[Tracker10Architecture.md](Tracker10Architecture.md)。

---

## 1. 问题背景

### 1.1 产品边界（不可破）

| 约束 | 含义 |
|------|------|
| 十声部固定分配 | 无动态 voice 分配 |
| 主循环做效果/包络 | ISR 只出样，不跑 tracker 效果机 |
| 曲谱在内部 Code Flash | `Score[]` + `MOVC`；禁止 SPI NOR 读音频路径 |
| 离线编译 | MCU **不**解析 XM/MOD；只跑 T10P/T10M v4 |
| 热状态布局固定 | `TrackerPlayer.inc` 与 C `offsetof` 必须一致 |

### 1.2 为何 Flash 吃紧

以 **Unreeeal Superhero 3** 全曲进 `scoreList.c` 为例（本轮优化前的量级）：

| 部分 | 约字节 | 占比 | 说明 |
|------|--------|------|------|
| **曲谱 `Score[]`** | **~32 KB** | **~53%** | 模式/乐器/波表/PCM |
| **TrackerPlayer.c** | **~18.5 KB** | **~32%** | 最大代码块 |
| AudioRender.s | ~3.5 KB | ~6% | 十声部内联合成（已是 ASM） |
| Protocol.c | ~2.3 KB | ~4% | 串口协议 |
| 其余 | ~2 KB | ~5% | Bsp / PitchTable / main 等 |

结论：

1. **曲谱本身占一半以上**——再抠固件也换不来“无限长歌”。
2. 固件本体里 **2/3 是 TrackerPlayer**——压缩代码应优先打这里。
3. 早期还背过 **SPI NOR + 双后端 + stream 抽象**，已删除；不要重新引入。

### 1.3 编译器与内存模型（体积的“税”）

当前 Makefile 关键路径：

```text
sdcc -mmcs51 --model-large --stack-auto --opt-code-size
     --max-allocs-per-node 100000
```

对体积影响最大的几点：

| 机制 | 现象 |
|------|------|
| **large model** | 指针默认 3 字节 generic；访问 XRAM 常变成 `movx` 序列 |
| **`--stack-auto`** | 可重入：形参/局部走栈，函数入口 `push _bp`、大量 `@r0` 间接访问 |
| **XDATA 静态局部** | `static MEM_XDATA(...)` 避免栈爆，但每次读写都是 `movx`，且占 XRAM |
| **库乘除** | `* 48`、`* 8` 等易链入 `__mulint` / `__mullong` |
| **校验树** | 主机已校验的 magic/bounds 若再在设备上展开，会生成巨大比较树 |

因此：**同一算法用 C 写一遍，在 8051 large reentrant 下可能比“逻辑复杂度”大一个数量级。**

---

## 2. 测量方法（先量再改）

### 2.1 整包 Flash / XRAM

```bash
make clean && make
# 看 music-box-8051.mem
#   ROM/EPROM/FLASH  ...  used / 65024
#   EXTERNAL RAM     ...  used / 3072
```

### 2.2 模块 CSEG（`.rel`）

```bash
python3 - <<'PY'
from pathlib import Path
import re
for p in [
    'TrackerPlayer/TrackerPlayer.rel',
    'ScoreFlash.rel',
    'scoreList.rel',
    'Protocol.rel',
    'WavetableSynth/AudioRender.rel',
]:
    t = Path(p).read_text(errors='ignore')
    m = re.findall(r'^A\s+(\w+)\s+size\s+([0-9A-Fa-f]+)', t, re.M)
    print(p, {k: int(v, 16) for k, v in m if int(v, 16)})
PY
```

### 2.3 单函数“指令行数”粗排

SDCC 生成 `TrackerPlayer/TrackerPlayer.asm`，用 `; function name` 分段，
统计以空白+助记符开头的行数（**不是**精确字节，但排序可靠）：

```bash
python3 - <<'PY'
from pathlib import Path
import re
asm = Path('TrackerPlayer/TrackerPlayer.asm').read_text()
parts = re.split(r';\t function (\S+)\n', asm)
rows = []
for i in range(1, len(parts), 2):
    name, body = parts[i], parts[i+1]
    code = [l for l in body.splitlines() if re.match(r'^\s+[a-z]', l)]
    rows.append((len(code),
                 sum('lcall' in l for l in code),
                 sum('movx' in l for l in code),
                 name))
for n, lc, mx, name in sorted(rows, reverse=True)[:15]:
    print(f'{n:5d} insn  lcall={lc:2d}  movx={mx:3d}  {name}')
PY
```

### 2.4 板级验收（体积优化后必做）

```bash
make flash   # 需 stcgal + 上电复位
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
```

期望：`State: playing`，`Parser error: 0x00`，`underruns` 启动后保持 0，
缓冲区接近满。

### 2.5 本轮前后对照（Unreeeal 全曲内置）

| 阶段 | ROM used | 说明 |
|------|----------|------|
| 本轮 Tracker 压缩前 | **60719** | 已无 SPI/stream；状态偏移曾为 `0x30a` |
| 本轮较好结果 | **~59039** | 校验裁剪 + 批量 MOVC + 乘改移位等 |
| 文档落盘时实测 | **59170** | 含 `score_copy48` 与部分 read 改动；仍 **playing** |
| 上限 | 65024 | STC 选项 `program_eeprom_split` |

粗算：**整包约省 1.5–1.7 KB**；其中 TrackerPlayer CSEG 从约 **18586 → ~16900** 量级。

曲谱 `Score[]` CONST 仍约 **32411** 字节——固件再压也改变不了这一块。

---

## 3. 优化策略总览

按 **收益 / 风险 / 工作量** 排序的实战策略：

```text
1. 删整条子系统（SPI、双后端、ScoreStream）     → 一次砍数 KB
2. 主机信任：砍设备侧重校验树                   → 数百 B～1 KB+
3. 数据布局对齐磁盘 → 批量拷贝代替字段拼装       → 数百 B + 更快
4. 手写 ASM：MOVC 读写、固定长度 copy、双 DPTR  → 小函数极致瘦身
5. 避免库乘：*8/*16/*48 改移位加法               → 少链 mul + 局部变短
6. 减少 XDATA 静态局部与双间接指针               → 减 movx（需小心栈）
7. 大函数 ASM 化（decode_row / vm_next_event）  → 潜力最大，成本最高
```

**不要**指望“再开一级 `-Os`”解决 large reentrant 的结构性膨胀。

---

## 4. 前置压缩（本轮之前已完成）

这些是 TrackerPlayer 细抠的前提，写进文档以免回潮。

### 4.1 删除 SPI NOR 与双后端

- 曲谱 **只** 走内部 Code Flash：`scoreList.c` → `Score[]`。
- 移除 `SpiFlash.*`、Storage 抽象、运行时 `storage_auto_detect`。
- 音频路径禁止 SPI 读。

效果量级（历史）：含 SPI 的厚校验固件（极小曲）曾 ~35 KB，删 SPI 后 ~31 KB。

### 4.2 删除 ScoreStream

- 旧模型：抽象字节流，VM 维护流状态。
- 新模型：`trackBase` / `patternPos` / `patternEnd` 为 **绝对 Code 地址**；
  `ScoreFlash.s` 提供 `score_u8/u16/u32`。
- 效果量级：极小曲固件再降到 ~29 KB。

### 4.3 热布局与 AudioRender 同步

- `TRACKER_PLAYER_VM_STATUS` 必须与  
  `offsetof(TrackerPlayer, vm) + offsetof(TrackerVm, status)` 一致。
- 本轮因乐器结构 +2 字节 × 10 声部，状态偏移：**`0x30a` → `0x31e`**  
  （C 编译期 assert + `TrackerPlayer.inc` + `AudioRender.s`）。

---

## 5. 手写汇编：`ScoreFlash` 与 STC 双 DPTR

### 5.1 为何 C 读 Flash 贵

每次 `score_u8(addr)` 在 large reentrant 下大致是：

1. 准备 16 位地址进 DPTR；
2. `lcall`；
3. 函数内 `MOVC`；
4. 返回值再写回调用方 XDATA/栈。

若 `load_instrument` 对 48 字节乐器 **逐字节** `track_u8`，就是 **几十次调用 + 上百次 movx**。

### 5.2 API（当前）

头文件 `ScoreFlash.h`：

| 函数 | 作用 |
|------|------|
| `score_u8/u16/u32` | 绝对 Code 地址读 1/2/4 字节 |
| `score_copy_xdata(addr, dst, len)` | 通用 MOVC→XRAM 拷贝 |
| `score_copy48_xdata(addr, dst)` | 固定 48 字节（乐器/头）专用入口 |

实现：`ScoreFlash.s`。调用约定遵循 **SDCC large MCS51**：

- 第一个 16 位参数：`DPL/DPH`；
- 其余参数：栈上，**后写的参数更靠近 SP**（注意与 push 顺序一致）；
- `u8` 返回 `DPL`；`u16` 返回 `DPL:DPH`；`u32` 为 `DPL:DPH` + `B:A`。

### 5.3 STC8 硬件双 DPTR

**是 STC 硬件特性**，不是软件模拟两套指针。

| 寄存器 | 地址 | 作用 |
|--------|------|------|
| `DPS` | `0xE3` | 选择当前 DPTR（0 / 1） |
| `DPL`/`DPH` | 标准 | DPTR0 |
| `DPL1`/`DPH1` | `0xE4`/`0xE5` | DPTR1 |

`score_copy_*` 典型用法：

```text
DPTR0 = Code 源地址   → MOVC A, @A+DPTR
DPTR1 = XRAM 目的地址 → MOVX @DPTR, A
inc DPS / dec DPS     → 在两套 DPTR 间切换，避免单 DPTR 反复 push/pop
```

### 5.4 ISR 与双 DPTR 的正确性

32 kHz Timer0 ISR（`PeriodTimer.s`）原先只保存 `A, DPL, DPH, PSW, R0`。

若主线程在 `score_copy` 中途 **`DPS=1`** 时被打断，ISR 里的 `mov dptr,#...`
会改到 **DPTR1**，返回后主线程拷贝错乱。

对策：

1. ISR 入口：`push DPS`，并 **`mov DPS, #0`** 强制 DPTR0；
2. ISR 出口：`pop DPS` 恢复；
3. 文档/注释写明：ISR 触碰集合含 **DPS**。

这是“用双 DPTR 换体积/周期”的 **硬前提**，不能省。

### 5.5 通用 copy vs 固定 48 字节

- **通用** `score_copy_xdata`：栈上多一个 `len`，循环 `djnz`。
- **固定** `score_copy48_xdata`：少一个参数、常数 48，调用点更短。

实测：固定入口对 **单次** load 有收益，但若同时把 `read8` 改回栈局部等，
整包可能出现 **几十字节级回摆**——以 `music-box-8051.mem` 为准，不要只看函数 insn 数。

---

## 6. 乐器布局对齐：48 字节整块拷贝

### 6.1 磁盘格式（T10M v4）

每条乐器 **固定 48 字节**（见 T10Format）：

| 偏移 | 内容 |
|------|------|
| 0 | mode |
| 1 | gain |
| 2–3 | relativePitch |
| 4–11 | volume/pitch 宏控制字段 |
| 12–13 | fadeout |
| 14–15 | reserved |
| 16–31 | volumeMacro[16] |
| 32–47 | pitchMacro[16] |

### 6.2 旧 RAM 布局的问题

早期 `TrackerInstrumentState` 为 **46 字节**（跳过 disk 上 2 字节 reserved），
导致：

- 不能 `memcpy`/`score_copy` 整块；
- C 侧逐字段赋值 → SDCC 生成巨长 `load_instrument`（曾见 **400+ 指令行**）。

### 6.3 改法

```c
typedef struct {
    /* ... 与磁盘相同顺序 ... */
    uint16_t fadeout;
    uint16_t reserved;   /* 对齐磁盘 +14..15 */
    uint8_t volumeMacro[16];
    int8_t  pitchMacro[16];
} TrackerInstrumentState;  /* sizeof == 48 */
```

运行时：

```c
score_copy48_xdata(codeAddr, (uint8_t __xdata *)instrument);
```

代价：

- 每声部 +2 字节 × 10 = **+20 XRAM**；
- `TRACKER_PLAYER_VM_STATUS`：**0x30a → 0x31e**（C assert + `.inc`）。

收益：

- `load_instrument` 从“字段拼装巨函数”变成“算地址 + 一次 copy”；
- 去掉临时 `raw[48]` 后 XRAM 可净减（曾观测到 net 下降）。

主机已校验乐器记录时，设备侧可只保留 **编号上界**（见下节），进一步删比较树。

---

## 7. 信任主机：裁剪设备侧校验

### 7.1 原则

| 层 | 职责 |
|----|------|
| **host**（`tools/tracker10`） | 格式、效果集合、乐器/模式合法性、长度 |
| **device** | 播放语义；仅保留 **防跑飞** 的最小检查 |

设备上每多一个 `if (x > 255 && y < z && magic != ...)`，large 模型下可能展开成
大段 `movx` + 多字节比较。

### 7.2 已做的裁剪示例

| 位置 | 原状 | 现策略 |
|------|------|--------|
| `load_instrument` | mode/gain/宏长度等完整校验 | 仅 `number` 范围；非法编号返回 0 |
| `open_track` | magic、采样率、size 镜像等多重大树 | 版本/声部数/flags/目录字段等 **必要子集** |
| `load_pattern` | offset/size 相对 track 的多重边界 | 保留 pattern 索引与 rows/size 非零 |
| `decode_row` / `seek` | mask 高半字节、note>97、volume>64 等 | 流耗尽失败仍报错；**内容合法性交给 host** |

错误码（`trackerLastError`）仍保留若干（如 `0x81` 流读失败、`0x88` 乐器加载失败），
便于串口诊断，但 **不要为“理论上不可能的 host 输出”堆完整验证器**。

### 7.3 风险与回滚

- 若用 **手工改坏的 T10** 刷机，设备可能静默异常而非干净报错。
- 量产/发布路径应保证：只刷 `tracker10_tool.py compile` 产出。
- 需要强校验时：优先加 **host 测试**，而不是把树搬回 MCU。

---

## 8. 乘法与常数尺度

SDCC 对 `uint16_t * 48` 等常调用 `__mulint`。

| 表达式 | 改写 |
|--------|------|
| `n * 48`（乐器） | `(n << 5) + (n << 4)`（32+16） |
| `pattern * 8` | `pattern << 3` |
| `pcmIndex * 8` | `pcmIndex << 3` |
| `waveCount * 16` | `waveCount << 4` |

注意：

- 若模块 **别处仍链接** `__mulint`（音量、tremolo 等），去掉一处乘法 **不一定**
  减少最终 ROM（库仍在）；但能缩短该函数并减少调用开销。
- 移位展开有时比 `lcall __mulint` **更长**——以整包 `mem` 为准做 A/B。

音量路径上的 `(volume * gain * macro)` 等仍可能保留 `__mulint`；
那是 **正确性/质量** 优先区，不建议为几十字节改语义。

---

## 9. 调用约定与包装层

### 9.1 `track_u*` 宏

```c
#define track_u8(vm, off)  score_u8((uint16_t)((vm)->trackBase + (uint16_t)(off)))
#define track_u16(vm, off) score_u16((uint16_t)((vm)->trackBase + (uint16_t)(off)))
#define track_u32(vm, off) score_u32((uint16_t)((vm)->trackBase + (uint16_t)(off)))
```

用宏代替 `static` 包装函数，避免 **二次 `lcall` + bp 帧**。
地址仍是 16 位加法；瓶颈在 large 指针与 XDATA，不在这一行加法本身。

### 9.2 `read8` / `read16`

模式流读取是 `decode_row` / `seek_pattern_row` 热路径。

可选形态：

| 形态 | 优点 | 缺点 |
|------|------|------|
| 出参 `MEM_XDATA *` + 静态临时 | 不占栈 | 每次 `movx` |
| 出参栈上 `uint8_t *` | 少 XRAM | reentrant 下更胖 |
| 手写 ASM 读流 | 最短 | 维护成本高 |

实践中：**整包体积** 对这里很敏感，改完必须 `make` 看 `mem`，不能只看 C 是否“更干净”。

---

## 10. 热点函数现状（优化后量级）

对 `TrackerPlayer.asm` 指令行粗排（数量随版本波动，用于抓大头）：

| 函数 | 角色 | 量级（insn 行） | 备注 |
|------|------|-----------------|------|
| `decode_row` | 每行解码 + 效果记忆 + 触发 | ~1900 | **最大**；十声部展开 |
| `vm_next_event` | 混音控制打包 + PCM 启动 | ~1750 | 含音量/fade/memcmp |
| `open_track` | 开轨、读头、默认乐器 | ~1400 | 已裁校验，仍偏大 |
| `apply_tick_effects` | tick 效果 | ~800 | switch 多分支 |
| `service_note_delays` | EDx | ~750 | |
| `output_pitch` | 音高+宏+vibrato | ~600 | 可能含 mul |
| `load_instrument` | 乐器装入 | ~120–140 | 已从 400+ 压下 |
| `ScoreFlash` 模块 | MOVC 助手 | ~60–90 B CSEG | 双 DPTR copy |

**下一步若继续抠 TrackerPlayer**，性价比最高的是：

1. **`decode_row` 手写 ASM 或拆表驱动**（工作量大）；
2. **`vm_next_event` 音量路径 ASM** / 减少 `memcmp`；
3. **`open_track` 批量读 48 字节头** 后用 IRAM 解析（注意先前 bulk 头缓冲曾让函数变胖，需再 A/B）；
4. Protocol 桩精简（非 Tracker，但抢同一 Flash）。

---

## 11. 反模式：哪些“优化”会回摆

| 做法 | 为何翻车 |
|------|----------|
| 为 bulk 头解析加 **大块 static XDATA + 大量下标** | SDCC 下标成地址运算，函数变 **更长** |
| 处处 `(x << n) + (x << m)` 替代已共享的 `__mulint` | 调用点变长，库又删不掉 |
| `static Type __xdata * __xdata p` 双间接 | 指针本身也在 XRAM，访问 **两次 movx** |
| 在 ISR 用双 DPTR 却不保存 `DPS` | 偶发错音/错谱，极难查 |
| 改乐器尺寸却忘改 `TRACKER_PLAYER_VM_STATUS` | AudioRender 读错 status，表现像“播着播着停” |
| 改 `.inc` 不 `make clean` | SDCC 汇编依赖不完整，链到旧目标文件 |

---

## 12. 推荐工作流（以后再压）

```text
1. make clean && make → 记录 ROM / TrackerPlayer CSEG
2. 对 TrackerPlayer.asm 做函数 insn 排序，只动 top 3～5
3. 一次只改一类手段（校验 / 布局 / ASM / 乘法）
4. 再 make，对比 mem；变差就回滚该类
5. make flash + musicbox_proto status/audio
6. 大曲 + 极小曲各验一次（防“只对 Unreeeal 碰巧对”）
7. 改 .inc / 结构体后：强制 make clean
```

板级串口：

```text
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
115200；flash 常需断电复位配合 stcgal
```

---

## 13. 关键文件清单

| 路径 | 角色 |
|------|------|
| `TrackerPlayer/TrackerPlayer.c` | VM / 效果 / 开轨 |
| `TrackerPlayer/TrackerPlayer.h` | 结构体；乐器 48 B；status 偏移 assert |
| `TrackerPlayer/TrackerPlayer.inc` | `TRACKER_PLAYER_VM_STATUS` 等给 ASM |
| `ScoreFlash.s` / `ScoreFlash.h` | MOVC 读与 copy；双 DPTR |
| `WavetableSynth/PeriodTimer.s` | ISR；保存/恢复 DPS |
| `WavetableSynth/AudioRender.s` | 使用 VM status 偏移 |
| `Makefile` | large + stack-auto + opt-code-size |
| `scoreList.c` | 内置曲谱（当前常为 Unreeeal 全曲） |
| `tools/tracker10_tool.py` | 主机编译与校验权威 |

---

## 14. 结论（给后来者）

1. **Flash 一半是歌，三分之一是 TrackerPlayer**——优化代码是为了 **多留几 KB 给更大的歌**，不是为了跑分。
2. **SDCC large + stack-auto** 下，算法复杂度与代码体积严重不成正比；布局对齐、批量 MOVC、裁校验、少包装层，往往比“微优化 C 表达式”更有效。
3. **手写汇编应打在边界清晰的助手上**（Flash 读、固定长度 copy、ISR），而不是一上来重写整台效果机。
4. **STC 双 DPTR 可用且值得用**，但必须与 ISR 的 `DPS` 保存绑定，否则是定时炸弹。
5. **主机是语义与格式的权威**；设备校验只防跑飞。把 FT2 兼容性做在 `tests/tracker10` 和编译器里。
6. 一切以 **`music-box-8051.mem` + 板级 playing** 为准；insn 行数只用于找热点。

若继续压缩，优先评估：`decode_row` / `vm_next_event` 的 ASM 化成本，以及 Protocol 是否还能砍；
**不要**在未改 PCB 的前提下重新引入 SPI 曲谱后端。
