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
| `05` | AUDIO_INFO | mix, PWM, audio-buffer state, u16 state, u16 PCM voice mask |
| `07` | VOICE_DUMP | 10 x 8-byte hot voice state |
| `10..15` | PLAY/STOP/PREV/NEXT/SET/STATUS | playback control |
| `17` | FORMAT_INFO | `T10M`, version `4`, channel count, rate |
| `18` | CHANNEL_MUTE | u16 LE, bits 0..9 are voices; bit 10 mutes PCM mixing only |
| `20..25` | FLASH commands | optional SPI NOR management |

The AUDIO_INFO state word uses bits 0..9 as the voice mute mask and bit 10 as a
PCM-only diagnostic mix mute. PCM state and timing continue while bit 10 is set.
Bit 14 latches final
mixer saturation and bit 15 latches a Timer0 ISR that crossed the next 32 kHz
deadline. Reading AUDIO_INFO clears both diagnostic bits; they do not affect
channel muting.

AUDIO_INFO byte 3 is the audio-buffer underrun counter; bytes 4 and 5 are its
8-bit read and write indices. `(write - read) & 0xff` is the buffered sample
count in the 255-byte effective ring.

SPI erase/write is rejected while playback is active. On boards without the
SPI NOR populated, flash commands return `NOT_SUPPORTED` and playback uses the
internal `scoreList.c` image.
