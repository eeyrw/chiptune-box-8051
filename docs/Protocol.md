# Tracker10 Serial Protocol

UART1 使用 115200 baud。请求帧为：

```
5A | command | payload_length | payload | xor(command..payload)
```

响应帧为：

```
5A | command|80 | status | data_length | data | xor(command|80..data)
```

主要命令：

| Code | Command | Data |
|---:|---|---|
| `00` | PING | ASCII firmware name |
| `01` | GET_INFO | version, backend, track count |
| `02` | RESET | software reset into ISP |
| `03` | UPTIME | u32 LE milliseconds |
| `04` | MEM_INFO | SP and free stack |
| `05` | AUDIO_INFO | mix, PWM, queue state, u16 mute mask |
| `07` | VOICE_DUMP | 10 x 8-byte hot voice state |
| `10..15` | PLAY/STOP/PREV/NEXT/SET/STATUS | playback control |
| `17` | FORMAT_INFO | `T10M`, version, channel count, rate |
| `18` | CHANNEL_MUTE | u16 LE, bits 0..9 |
| `20..25` | FLASH commands | optional SPI NOR management |

SPI erase/write is rejected while playback is active. On boards without the
SPI NOR populated, flash commands return `NOT_SUPPORTED` and playback uses the
internal `scoreList.c` image.
