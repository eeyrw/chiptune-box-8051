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
- 32 kHz Timer0 audio ISR, register bank 1.
- Ten fixed voices, no dynamic voice allocation.
- Hot state is absolute DATA `0x21`, 85 bytes; keep C and `.inc` layouts equal.
- Each voice: phase24, increment24, volume8, prepacked waveform offset8.
- `WavetableSynthStep.s` is the only synthesis hot path and is unrolled ten times.
- Main loop decodes score events and fills a four-entry XRAM queue.
- Pitch conversion and event decompression belong in the main loop, never ISR.

Always `make clean` after changing `.inc` files because SDCC assembly dependency
tracking is manual.

## Score toolchain

```bash
python3 tools/tracker10_tool.py compile song.xm song.t10p --c-output scoreList.c
```

T10M stores sample-timed state changes. Common pitch events use 8.8 note values;
small pitch movements and repeated waits are delta-coded. Loop entry must contain
a full ten-voice snapshot and reset compression history.

## Storage

Both internal and SPI backends are compiled. `storage_auto_detect()` chooses SPI
after a valid JEDEC ID and otherwise uses `scoreList.c`. The current board may
have no SPI NOR installed; that is not permission to remove the backend.

PCB SPI data lines are crossed, so `SpiFlash.c` uses GPIO bit banging on P3.2
(clock), P3.3 (MOSI), P3.4 (MISO), P3.5 (CS). Do not enable hardware SPI without
physically swapping P3.3/P3.4.

## Hardware verification

UART1 is 115200 on `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`.
`tools/boot.py` sends the framed RESET used by
`make flash`. P5.5 is high for the whole audio ISR and is the timing measurement
point. Queue underruns should remain zero after startup.
