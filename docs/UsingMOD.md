# 使用 ProTracker 兼容 MOD

本文描述从 31-sample ProTracker 兼容 MOD 到板上试听的流程。MOD 只在主机端
解析；8051 播放的是编译后的 `T10P/T10M v4`。与 XM 共用同一设备 VM 和资源格式。

- 命令参数与副作用：[PythonTools.md](PythonTools.md)
- 曲库来源与批量结果：[MODCollection.md](MODCollection.md)
- 二进制格式：[T10Format.md](T10Format.md)
- XM 对照流程：[UsingXM.md](UsingXM.md)

## 1. 能力与边界

- 最多 10 声道（`M.K.`/`4CHN`/`6CHN`/`8CHN`/`10CH` 等）；超过 10 直接拒绝
- 仅 31-sample 有签名格式；15-sample Soundtracker 暂不支持
- 音高：Amiga period → FT2/XM note（libxmp `PERIOD_BASE=13696`）
- effect 模型：固定 Amiga-period（T10M flag bit 1）
- 默认 speed=6、BPM=125
- 输出单声道；`8xx` panning 丢弃并 warning
- 资源策略（有意近似，见 warning）：

| 源采样 | 编译结果 |
|---|---|
| 短 forward loop，长度 8..64 | 16-point 波表，持续到 cut |
| 更长 loop | 16 kHz PCM one-shot（不保留无限循环） |
| 非循环 | 16 kHz PCM one-shot |
| PCM 被多个 note 触发 | 按最常 note 烘焙播放率 |

长循环若收成 16 点波表，鼓类 loop 会变成“永续纯音”。当前策略用 PCM 体积换听感。

## 2. 预检与编译

```bash
python3 tools/tracker10_tool.py inspect song.mod
python3 tools/tracker10_tool.py compile song.mod song.t10p \
  --c-output scoreList.c
python3 tools/tracker10_tool.py inspect-t10p song.t10p
```

`inspect` 会完整 lowering 并校验 T10M；不写盘。JSON 中注意：

| 字段 | 含义 |
|---|---|
| `format` / `signature` | `mod` 与 `M.K.` 等 |
| `frequency_mode` | 恒为 `amiga` |
| `warnings` | 有意降级（资源、panning、fine effect 等） |
| `compiled.waves` / `pcm_*` | 波表与 PCM 占用 |

常见 warning 含义：

- `non-looped samples become 16 kHz PCM one-shots`
- `long sample loops become one-shot PCM`
- `short loops become 16-point wavetables and sustain until cut`
- `multi-note PCM instruments use the most-common note rate only`（可用 `--multi-note-pcm` 拆分）
- 资源策略：`--resource-policy wave|pcm`（默认 wave；MOD 上长 loop/非循环仍多为 PCM）
- panning 丢弃（单声道）

## 3. 支持的 effect（设备侧）

| 命令 | 行为 |
|---|---|
| `0xy` | arpeggio |
| `1xx` / `2xx` | pitch slide |
| `3xx` | tone portamento |
| `4xy` | vibrato |
| `5xy` | tone porta + volume slide |
| `6xy` | vibrato + volume slide |
| `7xy` | tremolo |
| `9xx` | sample offset（PCM；host 按源长→PCM 长缩放） |
| `Axy` | volume slide |
| `Bxx` | position jump |
| `Cxx` | set volume（`>64` 钳位） |
| `Dxx` | pattern break（十进制行号） |
| `E1x` / `E2x` | fine porta（tick 0） |
| `E6x` | pattern loop |
| `E9x` | retrigger |
| `EAx` / `EBx` | fine volume（tick 0） |
| `ECx` | note cut |
| `EDx` | note delay |
| `EEx` | pattern delay |
| `Fxx` | speed（`<0x20`）或 BPM |

有意丢弃：`8xx` panning（单声道）。

硬失败：未知主 effect、未支持的 `Exx`（`E0`/`E3`/`E4`/`E5`/`E7`/`EF` 等）、
损坏头、>10 声道、>16 去重波表、截断采样区、`Dxx` 目标行超出目标 pattern 长度等。
`E00` 视为空命令并清除。

## 4. 烧写到板子

推荐隔离构建，不覆盖仓库根目录 `scoreList.c`：

```bash
make flash-tracker TRACKER_INPUT="music/mod/Dizzy/fanaatti.mod" \
  XM_HEX_OUTPUT=build/mod/fanaatti.hex \
  XM_BUILD_DIR=build/mod
```

`tracker-hex` / `flash-tracker` 是 `xm-hex` / `flash-xm` 的别名；`TRACKER_INPUT`
可为 `.xm` 或 `.mod`。

下载顺序：先执行命令 → 等 `Waiting for MCU, please cycle power` → 重新上电 →
等到 `Finishing write: done`。

板上验收：

```bash
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
# 数秒后再读一次，确认 order/row 前进
python3 tools/musicbox_proto.py status
```

要求：`parser error=0`、缓冲接近满、播放窗口 underrun 不增长、`isr_overrun=False`。

## 5. 问题定位

| 现象 | 优先检查 |
|---|---|
| 某个音一直嗡 | 是否短循环波表未 cut；旧固件是否仍把长 loop 收成波表 |
| 鼓/采样突然断 | 长 loop 已变成 one-shot PCM（看 warning） |
| 音高不随 note 变 | 该乐器是 PCM，速率按最常 note 烘焙 |
| 编译失败 `E6x`/`EEx` | 更新固件与工具；二者已在 VM 中实现 |
| 固件过大 | `compiled.pcm_bytes`；缩短采样或换更小曲 |

曲库复检：

```bash
python3 tools/xm_batch.py manifest music/mod \
  --report /tmp/mod-manifest.tsv \
  --checksums /tmp/mod-sha256sums
diff -u music/mod/SHA256SUMS /tmp/mod-sha256sums
```
