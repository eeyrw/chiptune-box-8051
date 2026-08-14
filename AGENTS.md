# AGENTS.md - Tracker10 8051

## Build

```bash
make
make clean
make flash
make host-test
```

Do not reintroduce `RUN_TEST`. `Bsp.c` uses `MAIN_Fosc=33177600UL`; the Makefile
`F_CPU` default is not authoritative for UART or Timer0 timing.

## Product boundary

This is a generic ten-channel 8-bit wavetable tracker player. It is not an NES,
APU, VRC, MIDI or DPCM emulator. XM is compiled offline to `T10P/T10M`; the MCU
does not parse tracker effects in real time.

## Runtime

- STC8H3K64S2: 64 KiB Flash, 256 B IRAM, 3 KiB internal XRAM.
- All firmware uses register bank 0. The 32 kHz Timer0 audio ISR explicitly
  saves only A, DPTR, PSW and R0 and consumes one byte from the 256-byte XRAM
  audio ring.
- Ten fixed voices, no dynamic voice allocation.
- Hot state is absolute DATA `0x21`, 90 bytes; keep C and `.inc` layouts equal.
- Each voice: phase24, increment24, volume8, prepacked waveform offset8.
- `AudioRender.s` is the main-thread render hot path and contains no per-sample
  calls. It includes the ten-lane unrolled `WavetableSynthStep.inc` body and
  briefly stacks the three batch registers that live across each inline step.
- The intended Class-D output is a PWM2 hardware-complementary pair with dead
  time zero. On STC8H3K64S2, PWM2P/P1.2 is valid but P1.3 does not exist; do not
  describe the current P1.2/P1.3 setup as a working pair. The valid PWM2 pair is
  P2.2/P2.3, subject to PCB connectivity verification. See
  `docs/HardwareInvestigation.md`.
- Main loop runs effects, timed envelopes, release/fadeout, fills a four-entry
  control queue, and pre-renders a 255-sample effective audio ring.
- Pitch conversion, effects and instrument state belong in the main loop, never ISR.

Always `make clean` after changing `.inc` files because SDCC assembly dependency
tracking is manual.

## Score toolchain

```bash
python3 tools/tracker10_tool.py compile song.xm song.t10p --c-output scoreList.c
```

T10M v4 stores orders, patterns, row cells, timed envelopes, release/fadeout,
song wavetables, internal-Flash PCM one-shots and the normalized pitch-effect
model. The VM uses 8.8 note values and schedules
tracker ticks with an exact sample remainder. See `docs/T10Format.md`; do not
reintroduce the v1 sample-timed event stream or the v2 40-byte instrument layout.

## Storage

Both internal and SPI backends are compiled. `storage_auto_detect()` chooses SPI
after a valid JEDEC ID and otherwise uses `scoreList.c`. The current board may
have no SPI NOR installed; that is not permission to remove the backend.

PCB SPI data lines are crossed, so `SpiFlash.c` uses GPIO bit banging on P3.2
(clock), P3.3 (MOSI), P3.4 (MISO), P3.5 (CS). Do not enable hardware SPI without
physically swapping P3.3/P3.4.

T10M v4 wavetable and PCM resources are an internal Code Flash feature. The ISR
reads them directly with `MOVC`; do not add SPI reads, buffering or decoding to
the audio hot path. Preserve the SPI backend source even though v4 rejects it.

## Hardware verification

UART1 is 115200 on `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.
`tools/boot.py` sends the framed RESET used by
`make flash`. P5.5 is high for the whole audio ISR and is the timing measurement
point. Queue underruns should remain zero after startup.
