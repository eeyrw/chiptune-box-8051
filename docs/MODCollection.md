# MOD 曲库来源与筛选记录

本文记录 ProTracker 兼容 MOD 的来源、批量筛选和当前收录结果。
可复现工作副本在 [`music/mod/`](../music/mod/)。操作步骤见 [UsingMOD.md](UsingMOD.md)。

第三方作品版权与再分发边界见 `music/mod/README.md`。收录只表示兼容性测试，
不授予额外许可。

## 1. 规范与实现对照

| 主题 | 依据 |
|---|---|
| 文件布局 | [Protracker Module Format](https://www.aes.id.au/modformat.html)（Andrew Scott） |
| 签名 / 加载 | libxmp `src/loaders/mod_load.c` |
| period→note | libxmp `libxmp_period_to_note`，`PERIOD_BASE = 13696` |
| 主机实现 | `tools/tracker10/mod.py` |
| 设备语义 | `TrackerPlayer.c` + [T10Format.md](T10Format.md) |

开发时曾对照本地 libxmp / MilkyTracker 源码；它们不是运行时依赖。

## 2. 来源

- Modland Protracker：
  `https://ftp.modland.com/pub/modules/Protracker/`
- 200 首样本作者：`4-Mat`、`Laxity`、`Dizzy`、`Jogeir Liljedahl`、
  `Dr. Awesome`、`Walkman`、`Tip`、`Firefox`、`Captain`、`Mahoney`

```bash
curl -fL \
  'https://ftp.modland.com/pub/modules/Protracker/4-Mat/ace%20ii.mod' \
  -o ace_ii.mod
file ace_ii.mod
sha256sum ace_ii.mod
```

## 3. 筛选流程

```bash
python3 tools/tracker10_tool.py inspect song.mod
python3 tools/tracker10_tool.py compile song.mod song.t10p
python3 tools/tracker10_tool.py inspect-t10p song.t10p

python3 tools/xm_batch.py scan /path/to/mod-root \
  --report /tmp/mod-scan.tsv \
  --collect /path/to/accepted \
  --firmware-overhead 30233 \
  --source-root '4-Mat=https://ftp.modland.com/pub/modules/Protracker/4-Mat'
```

收录条件：

1. 完整解析且 ≤10 声道
2. 无未声明近似的硬失败 effect / 损坏资源
3. T10P 结构与 CRC 有效
4. 估算固件（T10P + 固定开销）≤ 65,024 字节

`grade`：`exact` = 无 warning；`approximate` = 仅有文档化 warning。
当前资源策略下，几乎所有真实 MOD 都会因 PCM/波表 lowering 成为 `approximate`。

## 4. 资源 lowering（产品策略）

| 源采样 | T10 资源 | 听感含义 |
|---|---|---|
| 短 forward loop，8..64 点 | 16-point 波表 | 芯片音色，持续到 cut |
| 更长 loop | 16 kHz PCM one-shot | 不再无限循环；避免鼓 loop→永续纯音 |
| 非循环 | 16 kHz PCM one-shot | 与 PT 一次播完接近 |
| 同一 PCM 乐器多 note | 按最常 note 烘焙速率 | 其它音高不跟踪 |

`inspect` 会对上述情况发出明确 warning，禁止静默降级。

## 5. 2026-08-14 批量结果（审计后策略）

对 200 首本地下载样本：

| 分类 | 数量 |
|---|---:|
| fit | 49 |
| too-large | 150 |
| incompatible | 1 |

相对“更多波表”的早期试探，fit 变少是因为 PCM 体积上升；这是用 Flash 换取
“鼓 loop 不嗡”的有意选择。

`music/mod/` 收录全部 49 对 fit 结果：

- 全部 `approximate`
- `MANIFEST.tsv` + `SHA256SUMS` 权威
- `SCAN-200.tsv` 保存完整 200 首扫描
- `xm_batch.py manifest` 逐字节复检 49/49 verified

仍失败的主因：>16 去重波表、截断采样、或 T10P+固件超限。

代表试听曲：

| 曲 | 说明 |
|---|---|
| `Dizzy/fanaatti.mod` | 体积小，约 9 KiB T10P |
| `4-Mat/ace ii.mod` | 经典 4 声道，曾用于板上回归 |

```bash
make flash-tracker TRACKER_INPUT="music/mod/Dizzy/fanaatti.mod"
```

## 6. 与 XM 前端对照

| 项目 | XM | MOD |
|---|---|---|
| 解析器 | `xm.py` | `mod.py` |
| 音高 | 谱面 note 1..96 | Amiga period → note |
| effect 模型 | linear 或 Amiga flag | 固定 Amiga |
| 默认 speed/BPM | 文件头 | 6 / 125 |
| 采样编码 | delta 8/16-bit | signed 8-bit，长度 unit=word |
| 包络 | XM volume envelope → 宏 | 无 envelope；靠 `Cxx`/cut |
| 资源 | 循环偏波表；长 one-shot→PCM | 短循环→波表；长循环/非循环→PCM |
| 目标 | 同一 `Song` IR → T10M v4 | 同左 |

MCU 不区分输入源格式。未完成项见 [Backlog.md](Backlog.md)。
