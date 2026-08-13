# Tracker10 8051

STC8H3K64S2 上的 10 路 8-bit Tracker 音乐播放器。它不是 NES/APU
模拟器：PC 端把 XM 编译为保留 order、pattern、乐器宏和效果语义的
`T10M v4` 曲谱，并提取歌曲波表与短 PCM 打击采样。MCU 主线程运行受限
tracker VM，并在主线程预渲染到 XRAM 音频环形缓冲；32 kHz ISR 只取一个
字节写 PWM。

## Build

```bash
make
make flash
make host-test
```

时钟与硬件定时以 `Bsp.c` 的 `MAIN_Fosc=33177600UL` 为准。音频 ISR 为
32 kHz，UART 为 115200 baud。

## Compile XM

```bash
python3 tools/tracker10_tool.py inspect-xm song.xm
python3 tools/tracker10_tool.py compile song.xm song.t10p --c-output scoreList.c
python3 tools/tracker10_tool.py inspect-t10p song.t10p
make clean && make
```

也可以使用：

```bash
make compile-tracker TRACKER_INPUT=song.xm TRACKER_OUTPUT=song.t10p
```

从 XM 隔离生成 HEX 并直接下载，不覆盖仓库 `scoreList.c`：

```bash
make flash-xm TRACKER_INPUT="song.xm" \
  XM_HEX_OUTPUT=build/xm/song.hex
```

当前内置测试曲是 Quazar / Sanxion 的 Funky Stars 原始 10-channel XM 的
波表化测试转换。源 XM 不纳入仓库，`scoreList.c` 是供当前硬件验证的编译结果。
日后更换 XM 时的完整预检、转换、空间检查、下载、逐声道试听和排错流程见
[docs/UsingXM.md](docs/UsingXM.md)。注意：当前板子播放的是编进内部 Code Flash
的 `scoreList.c`，单独生成 `.t10p` 不会更换板上曲目。
互联网 XM 的来源、检索方法、批量筛选标准和 2026-08-13 收集结果见
[docs/XMCollection.md](docs/XMCollection.md)。
可复现兼容曲库保存在 [`music/xm/`](music/xm/)，包括 146 对 XM/T10P、来源
manifest、转换 warning 和 SHA-256；第三方版权说明见该目录 README。

## Runtime

- 10 个固定逻辑声道，不做动态复音分配。
- 每声道可在 24-bit DDS 歌曲波表和 16 kHz PCM one-shot 间切换，并保留2种噪声模式。
- 主线程的 10 路合成热路径由汇编完全展开；Timer0 ISR 是恒定时间的缓冲消费者。
- 主线程按 tracker tick 执行效果、长音量包络、sustain、release 和 fadeout。
- `T10M v4` 复用 pattern，并保存定时乐器宏、最多16张歌曲波表、PCM资源与
  linear/Amiga 音高效果模型。
- 曲谱正文带供主机工具校验的 CRC32；8051 不计算 CRC。不支持的 XM effect 和
  volume-column command 会明确拒绝；已知乐器语义降级会由 `inspect-xm` 输出 warning。
- v4 波表和 PCM 热路径只支持内部 Code Flash，并通过 `MOVC` 直接读取。SPI
  后端源码仍保留，但不参与本阶段的 v4 音频资源播放。

串口示例：

```bash
python3 tools/musicbox_proto.py ping
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
python3 tools/musicbox_proto.py mute 0x3fe
python3 tools/musicbox_proto.py mute 0
```

架构见 [docs/Tracker10Architecture.md](docs/Tracker10Architecture.md)，逐字节格式规范见
[docs/T10Format.md](docs/T10Format.md)，
串口协议见 [docs/Protocol.md](docs/Protocol.md)。
所有 Python CLI、批处理脚本、生成器和 `tools/tracker10` 主机库的完整参考见
[docs/PythonTools.md](docs/PythonTools.md)。
