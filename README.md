# Tracker10 8051

STC8H3K64S2 上的 10 路 8-bit Tracker 音乐播放器。它不是 NES/APU
模拟器：PC 端把 XM 编译为保留 order、pattern、乐器宏和效果语义的
`T10M v3` 曲谱，MCU 主线程运行受限 tracker VM，32 kHz ISR 只负责合成。

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
python3 tools/tracker10_tool.py compile song.xm song.t10p --c-output scoreList.c
make clean && make
```

也可以使用：

```bash
make compile-tracker TRACKER_INPUT=song.xm TRACKER_OUTPUT=song.t10p
```

当前内置测试曲是 Quazar / Sanxion 的 Funky Stars 原始 10-channel XM 的
波表化测试转换。源 XM 不纳入仓库，`scoreList.c` 是供当前硬件验证的编译结果。

## Runtime

- 10 个固定逻辑声道，不做动态复音分配。
- 每声道 24-bit DDS 相位、24-bit 步进、5-bit 音量、6 种波形和2种噪声模式。
- Timer0 ISR 中的 10 路热路径由汇编完全展开。
- 主线程按 tracker tick 执行效果、长音量包络、sustain、release 和 fadeout。
- `T10M v3` 复用 pattern，并保存定时乐器宏与 linear/Amiga 音高效果模型。
- 曲谱正文带供主机工具校验的 CRC32；8051 不计算 CRC。不支持的 XM 效果会明确拒绝，不能静默近似。
- 内置 Flash 和可选 SPI Flash 共用 `ScoreStream`。未安装 SPI Flash 时自动回退内置曲谱。

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
