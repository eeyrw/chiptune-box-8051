# Tracker10 8051

运行在 STC8H3K64S2 上的十声道 8-bit 波表 tracker 播放器。PC 端把
FastTracker II XM 离线编译为 `T10P/T10M v4`；MCU 不解析 XM，也不是 NES、
APU、VRC、MIDI 或 DPCM 模拟器。

播放器保留 order、pattern、tracker tick、乐器包络和受支持的 effect 语义。
主线程执行 tracker VM，并提前渲染到 256-byte XRAM 音频环；32 kHz Timer0 ISR
只取一个字节写入 PWM。十个固定声部可使用歌曲自带的 16-point 波表、两种噪声
或内部 Code Flash 中的 16 kHz PCM one-shot。

## 快速开始

主机构建需要 GNU Make、SDCC 和 Python 3；测试需要 `pytest`，下载和串口工具还
需要 `stcgal`、`pyserial`。

```bash
make clean
make
make host-test
```

默认构建链接根目录 `scoreList.c`，当前内容由仓库中的
`music/xm/Quazar/funky stars.xm` 确定性生成。XM 的内部标题是
`Hybrid song 2:20`，即 Funky Stars，不是另一首曲目。

## 转换和下载 XM

只读预检、生成 T10P/C 和检查容器：

```bash
python3 tools/tracker10_tool.py inspect-xm "song.xm"
python3 tools/tracker10_tool.py compile "song.xm" song.t10p \
  --c-output scoreList.c
python3 tools/tracker10_tool.py inspect-t10p song.t10p
```

推荐使用隔离构建目标，不覆盖根目录 `scoreList.c`：

```bash
# XM -> T10P + scoreList.c + firmware HEX
make xm-hex TRACKER_INPUT="song.xm" \
  XM_HEX_OUTPUT=build/xm/song.hex

# 完成相同构建后启动 stcgal 下载
make flash-xm TRACKER_INPUT="song.xm" \
  XM_HEX_OUTPUT=build/xm/song.hex
```

本板的可靠下载顺序是：先运行下载命令，等 `stcgal` 显示
`Waiting for MCU, please cycle power`，再给板子重新上电；看到
`Finishing write: done` 和 `Disconnected!` 才算完成。下载过程中不要断电。

`make flash` 只下载当前已经链接的 `music-box-8051.ihx`；它不会因为
`build/xm/song.t10p` 被其他歌曲覆盖就自动选择那首歌。更换 XM 时优先使用
`flash-xm`，或显式指定本次生成的 `SCORE_SOURCE`。

完整流程、空间检查、板上验收和问题定位见
[使用其他 XM](docs/UsingXM.md)。XM 如何被编译成 T10P 的教学说明见
[XM 编译导读](docs/XMCompilation.md)。Makefile 变量和所有 Python 工具见
[Python 工具参考](docs/PythonTools.md)。

## 当前能力

- 十个固定逻辑声道，不进行动态 voice allocation。
- 最多 16 张歌曲波表；长非循环采样可成为 16 kHz PCM one-shot。
- 音量保持 XM 的线性幅度语义；不做逐乐器 peak normalization、DC centering
  或 PCM 类型专属增益。
- 主线程执行 effect、定时包络、sustain、release、fadeout 和精确 tick 调度。
- 四项控制队列与 255-sample 有效音频环吸收主线程延迟。
- 全系统只使用寄存器组 0。Timer0 ISR 只消费一个音频字节，并显式保存
  `A/DPTR/PSW/R0`；主线程的汇编渲染在内联合成前后只暂存三个跨采样寄存器。
- Class-D 计划使用 PWM2 硬件互补对且 `DTR=0`。当前 PWM2P/P1.2 有效，但
  `STC8H3K64S2` 没有 P1.3，因而当前 `PWM2N/P1.3` 配置不能形成第二路物理输出；
  完整互补对需在确认 PCB 后改用 P2.2/P2.3。
- 板载 P1.7/PWM4N LED 显示音频峰值，快速响应并按毫秒衰减。
- T10M v4 的波表和 PCM 通过 `MOVC` 从内部 Code Flash 读取。SPI 后端仍编译和
  自动探测，但 v4 音频资源不从 SPI 热路径读取。

`Bsp.c` 的 `MAIN_Fosc=33177600UL` 决定 UART 和 Timer0 定时；Makefile 的
`F_CPU` 默认值不是这两项时序的依据。程序区上限是 65,024 字节，XRAM 是
3,072 字节。

## 曲库

[`music/xm/`](music/xm/) 保存 149 对 XM/T10P、来源 manifest、转换 warning 和
SHA-256。13 首无 warning，136 首使用有明确报告的近似；每首都能单独装入内部
Flash，但 149 首不能同时放入。第三方作品仍受原作者和来源站许可约束。

互联网来源、检索和批量筛选方法见 [XM 曲库记录](docs/XMCollection.md)，当前
兼容边界与后续可支持特性见 [XM 兼容性审计](docs/XMCompatibilityAudit.md)。

## 板上诊断

串口默认是 `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`，115200 baud：

```bash
python3 tools/musicbox_proto.py info
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
python3 tools/musicbox_proto.py mute 0x3fe
python3 tools/musicbox_proto.py mute 0
```

串口命令必须串行执行，不能让两个 `musicbox_proto.py` 进程同时访问同一端口。
正常播放应保持 parser error 为零、播放窗口内 underrun 不增长，并保持
`isr_overrun=False`。当前 `>>9` 输出允许极少量 `clip` 锁存；应记录出现频率并
试听对应段落，而不是只看某一次瞬时布尔值。

架构见 [Tracker10Architecture.md](docs/Tracker10Architecture.md)，格式规范见
[T10Format.md](docs/T10Format.md)，串口帧和命令见
[Protocol.md](docs/Protocol.md)，测试要求见 [Testing.md](docs/Testing.md)，尚未实现的
计划项见 [Backlog.md](docs/Backlog.md)，硬件引脚与 PWM 冲突盘点见
[HardwareInvestigation.md](docs/HardwareInvestigation.md)。
