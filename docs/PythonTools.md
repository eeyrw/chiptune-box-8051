# Python 工具参考

本文覆盖仓库中 `tools/` 下的所有 Python 工具和 `tools/tracker10/` 主机库。
命令默认从仓库根目录执行。主机需要 Python 3；串口工具额外需要 `pyserial`，
测试需要 `pytest`。

## 工具总览

| 文件 | 用途 | 主要副作用 |
|---|---|---|
| `tools/tracker10_tool.py` | 单首 XM/MOD 预检、编译和 T10P 校验 | `compile` 写 T10P，可覆盖 `scoreList.c` |
| `tools/xm_batch.py` | 批量扫描 XM/MOD、容量分类、收集和 manifest | 可递归写 T10P、复制源文件、覆盖报告/校验表 |
| `tools/musicbox_proto.py` | 串口状态、播放控制和 SPI Flash 维护 | 部分命令改变播放状态或擦写 SPI Flash |
| `tools/boot.py` | 发送固件 RESET 帧进入 ISP | 立即复位 MCU |
| `tools/gen_builtin_demo.py` | 生成内置合成演示曲 | 覆盖仓库根目录 `scoreList.c` |
| `tools/gen_pitch_table.py` | 生成 32 kHz 音高增量表 | 覆盖 `WavetableSynth/PitchTable.c` |

所有 XM/MOD 编译命令都是确定性的；相同源码和工具版本应生成逐字节相同的 T10P。
`tracker10_tool.py` 和 `xm_batch.py` 的生成物使用临时文件加 `os.replace` 原子替换。

## tracker10_tool.py

单首歌曲的主入口，提供三个子命令：

```bash
python3 tools/tracker10_tool.py inspect song.xm
python3 tools/tracker10_tool.py inspect song.mod
python3 tools/tracker10_tool.py inspect-xm song.xm
python3 tools/tracker10_tool.py inspect-mod song.mod
python3 tools/tracker10_tool.py compile song.mod song.t10p --c-output scoreList.c
python3 tools/tracker10_tool.py inspect-t10p song.t10p
```

输入格式按文件内容自动识别：XM 魔数 `Extended Module: `，或 offset 1080 的
31-sample MOD 签名（`M.K.`、`M!K!`、`?CHN`、`??CH` 等）。扩展名只用于错误提示
和别名校验，不是唯一依据。

### inspect / inspect-xm / inspect-mod

读取并完整解析 XM 或 MOD，在内存中执行 lowering、T10M 编码和结构校验，但不写文件。
输出 JSON，包括 `format`、源通道/order/pattern/instrument/sample 数、effect 统计、
warnings、T10M 大小、波表数和 PCM 字节数。XM 额外包含 volume-column 统计；MOD
额外包含 `signature`。失败时退出非零，并报告第一个不兼容位置
`pattern:row:channel`，编号从零开始。

`inspect-xm` / `inspect-mod` 是格式约束别名：输入不是对应格式时失败。
`--max-orders N` 只分析前 N 个 orders，用于定位问题。它会改变曲目语义，不能把
截断结果标成完整兼容。

### compile

位置参数依次是输入 XM/MOD 和输出 T10P。`--c-output PATH` 同时生成可链接进内部
Code Flash 的 C 数组；当前无 SPI Flash 板要更换曲目必须更新该 C 文件。
`--max-orders` 与预检含义相同，截断版关闭完整曲目的正常循环语义，仅适合诊断。

资源 lowering 选项（不是 T10 文件字段，只影响 host 编译）：

| 选项 | 默认 | 含义 |
|---|---|---|
| `--resource-policy wave` | 是 | 优先 16 点波表（体积小；短非循环可能一直响到 cut） |
| `--resource-policy pcm` | 否 | 偏 PCM one-shot（非循环/长 loop 更像采样；体积更大） |
| `--multi-note-pcm` | 关 | 对多 note 的 PCM 乐器按 note 拆多份流（更大；默认只烤最常见 note） |

`inspect` / `inspect-xm` / `inspect-mod` 接受相同选项，便于对比容量。

输出 JSON 与预检相同，并增加 `playlist_bytes`、`resource_policy`、
`multi_note_pcm`。编译器先完整验证，再替换目标文件；失败时不会留下半个输出。

### inspect-t10p

只读校验 T10P header、目录范围、T10M v4、CRC、instrument/resource/pattern 表，
输出 playlist 大小和每首 track 的统计。它不证明音乐听感正确，但能排除截断、
越界、CRC 和结构错误。

## xm_batch.py

用于大量候选的可复现筛选。递归匹配 `.xm` 与 `.mod`。TSV 列固定为：`status`、`grade`、源模块/T10P/固件大小、
两个 SHA-256、warnings、source URL、相对路径和 error。TSV 使用 UTF-8 和 tab，
文件名可含空格。

### scan

```bash
python3 tools/xm_batch.py scan /path/to/xm-root \
  --report /tmp/scan.tsv \
  --output /tmp/compiled \
  --collect /path/to/accepted \
  --source-root \
    'Dalezy=https://ftp.modland.com/pub/modules/Fasttracker%202/Dalezy'
```

工具递归查找大小写不敏感的 `.xm`/`.mod`，每首执行完整解析、编译和 T10P 校验：

- `fit`：兼容且估算的最终固件不超过 `--flash-limit`；
- `too-large`：语义兼容，但 T10P 加固定固件开销超限；
- `incompatible`：解析、语义或资源校验失败。

`grade=exact` 表示无 warning；`approximate` 表示使用了明确报告的舍弃或近似。
`--output` 写所有语义兼容曲的 T10P，包括 `too-large`；`--collect` 只复制 `fit`
的源文件/T10P 对。两个选项都可省略，此时只写报告。

默认程序区上限是 65,024 字节。固定固件开销通过
`music-box-8051.bin` 大小减去 `scoreList.c` 中的 `ScoreSize` 自动计算，因此运行前
应先 `make clean && make`。也可用 `--firmware-overhead N` 显式指定；
`--firmware-bin` 和 `--score-c` 可指向其他构建。估算用于批筛，最接近上限的候选
仍应替换 `scoreList.c` 后做一次真实 clean link。

来源选项可重复：

```text
--source PATH=URL
--source-root DIRECTORY=BASE_URL
```

`--source` 精确匹配单个相对路径，优先级最高；`--source-root` 把余下路径按 URL
component 编码后追加到 base URL。参数整体应加引号，防止 shell 解释 `&`、空格
或 `#`。

### manifest

```bash
python3 tools/xm_batch.py manifest /path/to/collection \
  --report /path/to/collection/MANIFEST.tsv \
  --checksums /path/to/collection/SHA256SUMS \
  --source 'Mario/super mario 2.xm=https://api.modarchive.org/downloads.php?moduleid=153373'
```

对目录内每个 XM 查找同名 T10P，校验容器和 CRC，再从 XM fresh compile 并要求
与保存的 T10P 逐字节一致。全部成功返回 0；缺文件、损坏或不一致时相应行标为
`invalid`，命令返回 1。报告和 SHA256SUMS 都相对 collection 根目录生成，不依赖
当前工作目录，能正确处理空格和非 ASCII 文件名。

`manifest` 不下载文件、不删除多余文件，也不检查网络 URL 是否仍可访问。来源参数
与 `scan` 相同。

## musicbox_proto.py

默认连接：

```text
/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
115200 baud
```

可用 `--port PATH --baud N` 覆盖。每次命令打开串口、发送一帧、校验响应 command、
长度、状态和 XOR checksum，然后关闭串口。

同一时间只能有一个串口客户端。不要并行启动两个 `musicbox_proto.py`，否则一个
进程可能读走另一个请求的响应，表现为 `response command ... does not match`、
`invalid response header` 或超时。需要连续采样时应在一个客户端进程内串行发送
命令，普通人工诊断则逐条等待命令结束。

### 只读命令

| 命令 | 输出 |
|---|---|
| `ping` | 固件响应字符串 |
| `info` | 固件版本、storage backend、track 数 |
| `uptime` | 毫秒 uptime |
| `mem` | SP 与剩余 stack |
| `audio` | mix/PWM、mute mask、ring level、underrun、clip、ISR overrun、PCM mask |
| `format` | T10M magic/version、采样率、声道数 |
| `adc N` | ADC 通道值 |
| `status` | track、播放状态、parser error、order/row/tick |
| `flash-info` | SPI JEDEC、容量、sector 大小 |
| `flash-id` | 原始 JEDEC ID |
| `flash-read ADDR LEN [FILE]` | 读最多 120 字节；省略 FILE 时写 stdout 二进制 |

### 状态修改命令

```bash
python3 tools/musicbox_proto.py play
python3 tools/musicbox_proto.py stop
python3 tools/musicbox_proto.py panic
python3 tools/musicbox_proto.py prev
python3 tools/musicbox_proto.py next
python3 tools/musicbox_proto.py track 0
python3 tools/musicbox_proto.py mute 0x000
python3 tools/musicbox_proto.py reset
```

`mute` 接受 `0x000..0x7ff`；bit 0..9 是十个 voice，bit 10 是 PCM-only 诊断 mute。
`reset` 不等响应，立即请求 MCU 复位，`make flash` 会先调用同一 framed RESET。
当前板仍应按“先启动 `stcgal` 等待，再手动上电”的流程下载，不能仅依赖软件
RESET。

### SPI Flash 修改命令

```bash
python3 tools/musicbox_proto.py flash-write 0x0000 image.bin
python3 tools/musicbox_proto.py flash-erase 0x0000
python3 tools/musicbox_proto.py flash-erase-all
```

这些命令会修改或擦除外接 SPI NOR。`flash-write` 以最多 124 字节分块；写前是否
需要 sector erase 由调用者负责。当前板可能没有 SPI NOR，此时固件返回
`NOT_SUPPORTED`。它们不用于 T10M v4 内部 Flash 音频资源播放。

## boot.py

```bash
python3 tools/boot.py [PORT] [BAUD]
```

默认端口和波特率同上。脚本只发送 framed RESET `5A 02 00 02` 并关闭串口，不烧写
固件、不等待响应。`make flash` 先调用它，再调用 `stcgal`。串口不存在或写失败时
返回 1。

## 代码生成器

### gen_builtin_demo.py

无参数，从 Python IR 构造一首确定性的十声道兼容演示，编码成 T10P C 数组并直接
覆盖仓库根目录 `scoreList.c`：

```bash
python3 tools/gen_builtin_demo.py
# 等价 Makefile 入口
make generate-builtin-score
```

它不是第三方 XM 转换器，也不会保留当前内置歌曲；执行前应确认确实要替换曲谱。

### gen_pitch_table.py

无参数，按 32 kHz sample rate 和十二平均律重新计算 MIDI 0..128 的 24-bit DDS
phase increment，并直接覆盖 `WavetableSynth/PitchTable.c`：

```bash
python3 tools/gen_pitch_table.py
```

这是维护脚本。修改采样率、插值或音高模型时才应运行；生成后必须 clean build、
跑 host tests，并在硬件上复核音准和 ISR 时序。

## tracker10 主机库

`tools/tracker10/` 是上述 CLI 共用的实现，不是独立命令：

- `format.py`：`Cell`、`Instrument`、`PcmSample`、`Song` 数据模型；T10M v4
  `encode_track`/`decode_track`/`inspect_track`；T10P `pack_playlist`/
  `inspect_playlist`；`emit_c`。
- `xm.py`：边界检查的 `parse_xm`、报告用 `analyze_xm`、XM 到 T10M 的
  `compile_xm`。公开失败类型是 `XmError`。
- `mod.py`：31-sample ProTracker 兼容 MOD 的 `parse_mod`/`analyze_mod`/
  `compile_mod`，以及 `period_to_note`/`is_mod`。公开失败类型是 `ModError`。
- `reference.py`：`ReferencePlayer.step()` 按 tracker tick 产生 `TickFrame`，用于
  主机语义测试；它不渲染 32 kHz PCM 音频，也不替代硬件试听。
- `__init__.py`：包标识与简短说明。

名字以 `_` 开头的函数是内部实现，可能随格式实现调整，不应由外部脚本依赖。
格式的逐字节规范见 [T10Format.md](T10Format.md)，XM 转换与硬件验收见
[UsingXM.md](UsingXM.md)，互联网收集流程见 [XMCollection.md](XMCollection.md)。

## 推荐验证顺序

```bash
make host-test
make clean
make
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
```

更换曲目后先 host-test 和 clean link，再 flash。板上读取两次 `status/audio`，确认
位置前进、parser error 为零、buffer 稳定、underrun 不增长、clip 和 ISR overrun
均为 false。

## Makefile XM 工作流

常用的一步式入口是：

```bash
make flash-xm TRACKER_INPUT="/path/to/song.xm" \
  XM_HEX_OUTPUT=build/xm/song.hex
```

它依次执行完整 XM 编译、T10P/生成 C 输出、clean firmware build、复制 HEX 和
`make flash`。生成源位于 `build/xm/scoreList.c`，不会覆盖仓库的 `scoreList.c`。
只需要文件而不操作硬件时使用 `make xm-hex`。

`build/xm/` 会保留最近一次转换结果。若用户在两次下载之间换过歌曲，单独执行
`make flash SCORE_SOURCE=build/xm/scoreList.c` 会烧入该目录当前保存的歌曲，而
不是命令行中没有再次声明的旧 `TRACKER_INPUT`。要避免混淆，重新执行完整
`flash-xm TRACKER_INPUT=...`，或为重要试听设置独立 `XM_BUILD_DIR` 并校验 T10P：

```bash
make xm-hex TRACKER_INPUT="music/xm/Quazar/funky stars.xm" \
  XM_BUILD_DIR=build/funky-stars \
  XM_T10P_OUTPUT=build/funky-stars/song.t10p \
  XM_SCORE_C=build/funky-stars/scoreList.c \
  XM_HEX_OUTPUT=build/funky-stars/funky-stars.hex
cmp build/funky-stars/song.t10p "music/xm/Quazar/funky stars.t10p"
make flash SCORE_SOURCE=build/funky-stars/scoreList.c
```

最后一条命令启动后，等 `Waiting for MCU, please cycle power` 再给板子上电。

可覆盖变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `TRACKER_INPUT` | 无，必填 | 输入 XM |
| `XM_BUILD_DIR` | `build/xm` | 隔离生成目录 |
| `XM_T10P_OUTPUT` | `build/xm/song.t10p` | 完整 T10P 输出 |
| `XM_SCORE_C` | `build/xm/scoreList.c` | 本次固件使用的生成 C 源 |
| `XM_HEX_OUTPUT` | `build/xm/music-box-8051.hex` | 保留的 Intel HEX |
| `STCGAL_PORT` | 板载 USB 串口 by-id 路径 | 下载串口 |
| `STCGAL_BAUD` | `115200` | ISP 波特率 |

目标会清理并重建仓库的普通目标文件和 `music-box-8051.*` 构建产物，但不会改动
根目录 `scoreList.c`。`flash-xm` 会复位并重写 MCU Code Flash；`xm-hex` 不访问
硬件。
