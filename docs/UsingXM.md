# 使用其他 XM 曲目

本文描述从一个 FastTracker II XM 文件到板上试听的完整流程。XM 只在主机端
解析；8051 播放的是编译后的 `T10P/T10M v4` 语义曲谱、16 字节波表和 16 kHz
PCM one-shot。

## 1. 环境与输入要求

主机需要 Python 3、`pytest`、SDCC、GNU Make、`stcgal` 和 `pyserial`。当前硬件
和格式边界如下：

- 最多 10 个 XM 声道；少于 10 路会补静音声道，多于 10 路直接拒绝；
- 最多 16 张去重后的歌曲波表；
- 音调采样被抽取为 16 点单周期波表；长且不循环的采样被编译为 16 kHz、
  signed 8-bit PCM one-shot；
- 当前程序区由芯片选项固定为 65024 字节，不是完整 65536 字节；
- 输出是单声道，XM 的声像命令会被丢弃；
- SPI 后端仍保留，但 T10M v4 音频资源当前只能从内部 Code Flash 通过 `MOVC`
  读取。没有安装 SPI Flash 不影响本流程。

先在 MilkyTracker 中确认 XM 自身可以正常播放。不要把原曲里已有的节奏跳动、
采样截断或失真误判为转换器问题。

文件名含空格时始终用引号，例如：

```bash
XM='/home/yuan/下载/example song.xm'
```

## 2. 只读预检

先运行完整编译预检，但不写输出：

```bash
python3 tools/tracker10_tool.py inspect-xm "$XM"
```

该命令会解析 XM，在内存中完整执行 XM 到 T10M lowering，并校验生成的 T10M。
主要字段含义如下：

| 字段 | 含义 |
|---|---|
| `channels` | XM 源声道数，必须不超过 10 |
| `orders` / `patterns` | 播放序列长度和实际 pattern 数 |
| `instruments` / `samples` | 源乐器和采样数量 |
| `frequency_mode` | `linear` 或 `amiga` 音高模型 |
| `effects` | 播放 order 中出现的 effect 统计 |
| `warnings` | 能编译但存在有意降级或当前近似的语义 |
| `compiled.waves` | 去重后波表数，最多 16 |
| `compiled.pcm_samples` | PCM one-shot 数量 |
| `compiled.pcm_bytes` | PCM 占用的内部 Flash 字节数 |
| `compiled.track_bytes` | 单首 T10M 的总大小 |

预检失败不会修改 `scoreList.c`。常见错误包括超过 10 路、未知 effect、未知
volume-column 命令、超过 16 张波表、XM 损坏或截断。错误位置使用
`pattern:row:channel`，三个编号都从 0 开始。

### 当前 effect 支持

| XM 命令 | 处理方式 |
|---|---|
| `0xy` | arpeggio |
| `1xx` / `2xx` | pitch slide up/down |
| `3xx` | tone portamento，含参数记忆且不重触发相位 |
| `4xy` | vibrato |
| `8xx` | 接受但丢弃声像，因为输出为单声道 |
| `Axy` | volume slide |
| `E9x` | note retrigger |
| `Fxx` | speed/BPM |

volume column 当前接受 `10..50` 音量设定和 `C0..CF` panning；后者被丢弃。
其他命令明确拒绝，避免无提示地播放成错误音乐。

### 编译成功仍需注意的限制

- panning command 和 sample default panning 不参与单声道 fold；
- panning envelope 尚未渲染；
- instrument automatic vibrato 尚未渲染；
- 多采样乐器目前只选择该乐器最常用音符映射的一个 sample，尚未拆分 key zone；
- instrument-only cell 只有简化语义；
- 非循环短采样可能被压成波表，长 one-shot 才成为 PCM；
- 包络最多编译为 16 个定时点，因此极复杂包络是有界近似。

这些限制的设计和审计细节见 [XMCompatibilityAudit.md](XMCompatibilityAudit.md)。

## 3. 生成固件内置曲谱

推荐用一条命令同时生成便于检查的 `.t10p` 和固件真正使用的 `scoreList.c`：

```bash
python3 tools/tracker10_tool.py compile "$XM" build-song.t10p \
  --c-output scoreList.c
```

也可以使用 Makefile：

```bash
make compile-tracker \
  TRACKER_INPUT="$XM" \
  TRACKER_OUTPUT=build-song.t10p \
  TRACKER_C_OUTPUT=scoreList.c
```

`.t10p` 不是当前无 SPI Flash 板子的运行时输入。板子实际读取链接进 Code Flash
的 `scoreList.c`；如果只生成 `.t10p` 而没有更新 `scoreList.c`，重新下载后仍会
播放旧曲。

可以独立校验生成物的 playlist 目录、每首 T10M 范围、CRC、资源表和 pattern：

```bash
python3 tools/tracker10_tool.py inspect-t10p build-song.t10p
```

编译器会先完整生成和校验数据，再以原子文件替换写输出，避免中断后留下半个
`.t10p` 或 `scoreList.c`。如果命令失败，不应继续构建固件。

### 只编译前 N 个 orders

定位某个转换问题或临时缩小 Flash 时可使用：

```bash
python3 tools/tracker10_tool.py compile "$XM" preview.t10p \
  --max-orders 4 --c-output scoreList.c
```

该模式会截断歌曲，并关闭曲目循环；它是诊断功能，不是长期解决空间不足的方法。
最终版本应恢复完整 order 列表。

## 4. 测试、构建与空间检查

更新曲谱后执行：

```bash
make host-test
make clean
make
```

`make clean` 可以避免手写汇编依赖和旧目标文件干扰结果。成功链接后检查
`music-box-8051.mem`：

```bash
tail -n 12 music-box-8051.mem
```

必须满足：

- `ROM/EPROM/FLASH` 不超过 65024 字节；
- `EXTERNAL RAM` 不超过 3072 字节；
- absolute DATA 热状态仍为 90 字节并从 `0x21` 开始；
- stack 仍从 `0x7B` 开始，剩余空间不能异常下降。

曲谱变大主要增加 Code Flash，不应增加 XRAM 或 DATA。若链接器报告 Code Flash
溢出，优先检查 `compiled.pcm_bytes`。可行的曲目级处理是缩短无用采样、降低
sample 数量、删除未使用 pattern/order，或重新安排长 PCM；不要把 SPI 读取放进
音频热路径。

## 5. 下载与启动

默认串口和波特率是：

```text
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
115200
```

确认设备：

```bash
python3 tools/musicbox_proto.py ping
```

下载：

```bash
make flash
```

`make flash` 会先通过协议发送 RESET，使 MCU 自动进入 STC bootloader，再由
`stcgal` 下载。通常不需要手动断电。如果自动 RESET 没收到响应，`stcgal` 会停在
`Waiting for MCU, please cycle power`，此时再按提示断电/上电。

若 by-id 路径不存在，先查看实际设备：

```bash
ls -l /dev/serial/by-id /dev/ttyUSB*
```

临时覆盖端口示例：

```bash
make flash STCGAL_PORT=/dev/ttyUSB0
python3 tools/musicbox_proto.py --port /dev/ttyUSB0 ping
```

## 6. 板上验收

从第一首歌开头重新播放：

```bash
python3 tools/musicbox_proto.py stop
python3 tools/musicbox_proto.py mute 0
python3 tools/musicbox_proto.py track 0
python3 tools/musicbox_proto.py play
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
```

验收时与 MilkyTracker 从相同 order/row 开始对照。`status` 应显示：

- `State: playing`；
- `Parser error: 0x00`；
- order、row、tick 持续前进。

`audio` 应显示：

- 播放稳定后 `Buffer` 接近 `255/255`；
- `clip=False`；
- `isr_overrun=False`。

`underruns` 是 8-bit 累积计数，STOP 期间 Timer0 仍输出静音，因此非零绝对值不
代表播放中发生 underrun。应在同一试听窗口前后各读一次，确认计数没有增长。

### 隔离单个声道

mute mask 的 bit 0..9 对应 XM channel 1..10。设置某 bit 会静音该声道，但相位和
PCM cursor 继续运行。只听第 N 路时：

```text
mask = 0x3ff ^ (1 << (N - 1))
```

例如只听第 6 路：

```bash
python3 tools/musicbox_proto.py mute 0x3df
```

每次 A/B 都应执行 stop、设置 mute、选择 track、play，从歌曲开头开始。中途切换
mute 会比较到不同乐段，不能作为严格对照。完成后恢复：

```bash
python3 tools/musicbox_proto.py mute 0
```

## 7. 问题定位顺序

1. 先用 MilkyTracker 确认异常是否已经存在于原曲。
2. 查看 `inspect-xm` 的 warnings 和 effect 统计。
3. 确认编译命令确实更新了 `scoreList.c`。
4. 确认 clean build 的 Flash 没有超限。
5. 用 `status` 排除 parser error 和播放位置错误。
6. 在同一时间窗口前后读取 `audio`，排除 buffer、clip、ISR overrun。
7. 从头播放并逐路隔离，定位到具体 XM channel 和 instrument。
8. 对照 MilkyTracker 的 pattern、volume column、effect 和 sample waveform。

不要通过修改 Timer0 时钟来掩盖曲谱语义或采样问题。时钟以
`Bsp.c` 的 `MAIN_Fosc=33177600UL` 为准；Makefile 的 `F_CPU` 默认值不决定 UART
或 Timer0 实际时序。
