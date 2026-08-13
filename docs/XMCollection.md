# XM 曲库来源与筛选记录

本文记录第三方 XM 从哪里找、怎样下载、怎样判断能否在 Tracker10 上完整播放。
仓库内的可复现工作副本放在 `music/xm/`。其中第三方作品的版权和再分发边界见
`music/xm/README.md`；收录用于兼容性测试，不代表作品进入公有领域。

## 来源

- Modland FastTracker 2 公共目录：
  `https://ftp.modland.com/pub/modules/Fasttracker%202/`。目录按作者分组，文件可用
  `作者/文件名.xm` 直接下载。本次批量样本来自 Cerror、Dalezy、Dubmood；
  Tetris Game Boy 候选来自 Traven。
- Mod Archive：模块页使用
  `https://modarchive.org/index.php?request=view_by_moduleid&query=ID`，下载使用
  `https://api.modarchive.org/downloads.php?moduleid=ID`。模块页可核对格式、通道数、
  文件大小和站点声明的 license。
- `.mod Sample Master`：用文件名、MD5 和 instances 交叉索引候选。它适合反查，
  最终下载 URL 和许可仍回到实际镜像或模块页记录。

来源站允许下载不等于作品版权进入公有领域；清单保留来源 URL、哈希和已知的许可
信息。没有明确许可的文件仍属于各自作者，仓库收录不附加新的使用授权。

## 查找方法

1. 用网页搜索精确曲名加 `xm module`，再加 `site:modarchive.org`、
   `site:ftp.modland.com` 或 `site:modsamplemaster.org` 缩小范围。
2. 在 Modland 作者目录中搜索文件名。目录和文件名里的空格必须 URL encode。
3. 在 Mod Archive 搜索页或收藏页中反查 module ID。例如本次从收藏页找到
   `super_mario_2.xm` 的 ID 153373：

```bash
curl -fsSL \
  'https://modarchive.org/index.php?page=28&query=91424&request=view_member_favourites' \
  | rg -n -C 3 'super_mario_2'
```

4. 用模块 ID 下载，并立即记录文件类型和哈希：

```bash
curl -fL 'https://api.modarchive.org/downloads.php?moduleid=153373' \
  -o super_mario_2.xm
file super_mario_2.xm
sha256sum super_mario_2.xm
```

`curl -fL` 很重要：`-f` 拒绝把 HTML 错误页当 XM，`-L` 跟随下载重定向。

## 筛选流程

单首候选依次执行：

```bash
python3 tools/tracker10_tool.py inspect-xm song.xm
python3 tools/tracker10_tool.py compile song.xm song.t10p
python3 tools/tracker10_tool.py inspect-t10p song.t10p
```

收录必须满足：源文件可完整解析；不超过 10 通道；没有未处理的 effect、volume
column 或损坏资源；生成完整且 CRC 有效的 T10P；最终 clean link 不超过 65,024
字节程序区。不能只看 XM 大小，长 one-shot 降为 16 kHz PCM 后可能显著变大。

曲目分两级：

- `exact`：转换无 warning；
- `approximate`：只使用转换器明确报告的降级，例如单声道丢弃 panning、丢弃
  fine volume slide 或自动 vibrato 尚未渲染。

不允许无 warning 静默舍弃语义。超过 10 通道也不直接截断，因为后几路可能是
主旋律或鼓；将来若加入离线声道选择，必须报告保留/舍弃的声道和依据。

板上验收至少间隔数秒读取两次 `status` 和 `audio`，要求位置前进、parser error
为零、缓冲接近 255、播放窗口内 underrun 不增长、`clip=False`、
`isr_overrun=False`。

## 2026-08-13 批量结果

对 Modland 的 Cerror、Dalezy、Dubmood 共 390 首本地 XM 重扫：加入 `Bxx/Dxx`
控制流、`ECx` note cut、volume-column `6x/7x` lowering，并对 `6xy`、`9xx`、
`EDx`、volume-column `8x/9x` 作明确告警的有损近似后，转换通过数从 28 提升到
152；按当前 29,712 字节固定固件开销计算，143 首可单独链接，9 首因 T10P 过大
排除。`music/xm/MANIFEST.tsv` 保存逐首结果、warning、哈希和 URL。
加上单独收录的 JAM、Super Mario 2、Traven Tetris high scores 和贝多芬
`Fur Elise`，当前集合共 147 对 XM/T10P：13 首 `exact`，134 首 `approximate`；
全部通过 fresh compile
逐字节比对和 SHA-256 校验。

当前剩余阻塞点以超过 10 通道、`E6x` pattern loop、复合效果、损坏/非标准 XM
为主。优先级建议：

| 特性 | 建议 | 理由 |
|---|---|---|
| `EDx` 精确 note delay | 后续实现 | 当前 tick 0 近似；精确版需要延迟 note/instrument 状态 |
| `9xx` 精确 sample offset | 后续实现 | 当前从头播放；PCM offset、波表语义和参数记忆需定义 |
| `E6x` pattern loop | 谨慎实现 | 需要 pattern loop row/count 状态及嵌套边界 |
| 无效 sample/envelope loop | 可选修复模式 | 只能在明确 warning 下 clamp，默认仍拒绝损坏输入 |
| 超过 10 通道 | 不直接截断 | 固定十声道产品边界；离线选择可能明显改变编曲 |

## Tetris 与 Mario 候选

| 候选 | 来源 | 结果 |
|---|---|---|
| Dalezy / `tetris.xm` | Modland `Dalezy/tetris.xm` | exact；4,808-byte T10P；已上板通过 |
| Traven / `tetris gb hiscore.xm` | Modland `Traven/` | approximate；2,336-byte T10P；`Bxx` 支持后通过 |
| Traven / `tetris gb ingame.xm` | Modland `Traven/` | 拒绝：`E21` fine porta |
| Traven / `tetris gb title.xm` | Modland `Traven/` | `EC1` 已支持；仍拒绝：`EE1` pattern delay |
| `Super Mario 2` | Mod Archive ID 153373 | exact；6,335-byte T10P；34,701-byte 固件已上板通过 |
| `Super Mario-64` | Mod Archive ID 57522 | 拒绝收录：72,372-byte T10P，超过程序区 |
| Traven / `super mario cave.xm` | Modland `Traven/` | 拒绝：`EE4` pattern delay |
| Dubmood / `mario airlines (keygen edit).xm` | Modland `Dubmood/` | 拒绝：12 通道 |

可复现实例 URL：

```text
https://ftp.modland.com/pub/modules/Fasttracker%202/Dalezy/tetris.xm
https://api.modarchive.org/downloads.php?moduleid=153373
https://api.modarchive.org/downloads.php?moduleid=57522
```

## 古典音乐候选

| 候选 | 来源 | 结果 |
|---|---|---|
| Beethoven / `Fur Elise` | Modland `Steven Stojanovich/fur elise.xm` | approximate；7,450-byte T10P；多采样乐器简化；已收录并上板 |
| Tchaikovsky / `The Nutcracker Overture` | Modland `Dat/the nutcracker overature.xm` | approximate；14,028-byte T10P；多采样乐器简化 |
| Pachelbel / `Canon In D-igital` | Modland `Pachebel/canon in d-igital.xm` | 62,921-byte T10P，最终固件超过程序区 |
| Mozart / `Rondo Alla Turca` | Modland `Toed/rondo alla turca.xm` | 拒绝：12 通道 |
| Bach-Busoni / `Toccata` | Modland `Mad God/bach-busoni toccata.xm` | 拒绝：`E01` fine portamento up 尚未支持 |

`E1x` 只在 tracker row 的 tick 0 向上细调 x 个 period 单位。后续可在主机编译器
中按 XM 的 linear/Amiga frequency mode 降为一次性的 8.8 note delta，不需要增加
音频 ISR 工作。
