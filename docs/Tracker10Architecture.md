# Tracker10 Architecture

## Product boundary

Tracker10 is a generic ten-channel 8-bit wavetable tracker player for the
STC8H3K64S2. It is not an NES, APU, VRC, MIDI or DPCM emulator. Source modules
are compiled offline to T10P/T10M v3; the MCU never parses XM structures or
sample data.

The design preserves high-level tracker structure instead of expanding a song
into timestamped oscillator writes. Pattern reuse, effect parameter memory and
instrument envelopes remain compact objects in the device score.

## Compilation pipeline

```text
XM bytes
  -> bounds-checked XM parser
  -> source patterns, orders, instruments, envelopes and decoded samples
  -> normalized ten-channel Tracker IR
  -> mono lowering and explicit unsupported-effect validation
  -> sample/instrument analysis
  -> pattern/order compaction and fixed instrument macros
  -> T10M v3 + CRC32
  -> T10P playlist or generated scoreList.c
```

The parser reads XM instrument headers, keymaps, volume envelopes, sample
headers, 8/16-bit delta-coded samples, loops, default gain, finetune, relative
note, sustain/loop points and fadeout. Current lowering maps the keymap entry around the middle tracker octave to
one device instrument. A future compiler pass may split multisample key zones
into several deduplicated T10 instruments and rewrite note cells; no MCU format
change is required.

XM sample `relative note` and `finetune` values calibrate the recorded sample's
native pitch. The tonal lowering replaces that recording with a normalized
single-cycle oscillator, so those calibration offsets are deliberately removed.
Applying them to the oscillator would transpose the written tracker note twice.
The T10 relative-pitch field remains available for compiler-generated timbre
macros and future frontends; it is not copied blindly from an XM sample header.

Looped samples are resampled and correlated against the six resident tonal
waveforms. Long non-looped samples are classified from transient length and
high-frequency derivative energy. Tonal percussion receives a falling pitch
macro; noisy percussion selects one of two noise modes. RMS windows produce a
bounded 16-step volume macro. This is a deterministic compiler transform, not a
hardcoded channel or source-instrument-number mapping.

Long source envelopes are uniformly resampled across their complete time span.
The compiled step duration preserves envelopes longer than 16 ticks, and mapped
sustain/loop positions plus 16-bit fadeout preserve key-off release behavior.
The XM frequency-table flag is retained only as normalized effect-model metadata:
the MCU still stores pitch as Q8.8 notes and never parses XM periods.

## Runtime split

The main loop owns musical work:

- T10P/T10M structural and range validation;
- order and pattern traversal;
- tracker effect memory and per-tick effect execution;
- instrument macro execution;
- Q8.8 pitch to 24-bit DDS increment conversion;
- exact tracker-tick sample scheduling;
- filling the four-entry XRAM control queue;
- protocol and storage access.

The Timer0 ISR owns only sample-boundary control application and synthesis:

```text
32 kHz interrupt
  -> consume a due queue item
  -> generate long and short shared noise samples
  -> ten unrolled oscillator/mixer lanes
  -> saturate and bias to unsigned 8-bit
  -> write PWMA CCR2
```

No pattern parsing, division, pitch lookup, macro execution, allocation, SPI
transaction or serial handling occurs in the synthesis hot path. P5.5 remains
high for the complete ISR and is the hardware timing point.

## Tracker VM

There are ten fixed source/runtime channels and no MCU voice allocator. Each
channel retains note and portamento target, volume, current effect, remembered
effect parameters, vibrato phase, key-on/release state, fadeout, instrument
definition and macro cursors. An active 48-byte instrument record is copied from score storage to XRAM, so
macro evaluation never thrashes the SPI cache.

At roughly 50 tracker ticks per second, the VM produces a `TrackerControlEvent`.
Only changed voices are marked, although the four queue slots have fixed size to
keep the ISR bounded. Each event contains an exact output-sample wait. Four
slots provide tens of milliseconds of main-loop and SPI-read tolerance.

The queue producer writes a complete slot before advancing its 8-bit tail. The
ISR reads a slot only after observing that tail. Head and tail updates are atomic
on the 8051; queue reset disables interrupts around the shared state change.

## Synthesis state

Each fixed voice occupies eight absolute DATA bytes:

| Bytes | Meaning |
|---:|---|
| 0..2 | 24-bit oscillator phase |
| 3..5 | 24-bit phase increment |
| 6 | volume, `0..31` |
| 7 | prepacked oscillator mode (`mode << 4`) |

Ten voices consume 80 bytes. The remaining absolute state is `mixOut` (2), PWM
sample (1), debug mute mask (2), long noise state (2), short noise state (1) and
two current signed noise samples (2). The complete block is 90 bytes at DATA
`0x21`. Register bank 1 belongs to Timer0. The linked stack begins at `0x7B`,
leaving 133 bytes.

Modes 0..5 use signed 16-sample code-memory tables. Modes 6 and 7 consume shared
long-period and short-period LFSR samples. Both LFSRs advance once per sample,
outside the voice expansion, so enabling a second drum voice cannot change the
noise rate or sequence.

Each lane performs mute/volume tests, oscillator selection, signed sample by
unsigned volume multiplication, 24-bit accumulation and phase advance. The
final sum is shifted by eight, giving ten simultaneously loud voices shared
headroom, then saturated to signed 8-bit and biased by 128 for PWM. Muting
suppresses mixing but continues phase advance.

## Memory and current build

With the full semantic Funky Stars hardware-validation score, the current clean
build uses:

| Resource | Used | Available |
|---|---:|---:|
| code Flash | 43,811 bytes | 65,536 bytes |
| XRAM | 2,590 bytes | 3,072 bytes |
| absolute DATA hot state | 90 bytes | fixed at `0x21` |
| stack | 133 bytes | starts at `0x7B` |
| T10P score image | 19,174 bytes | stored in code Flash or SPI |

The SPI backend retains a 1 KiB XRAM read cache. The current PCB crosses the SPI
data lines, so `SpiFlash.c` bit-bangs P3.2 clock, P3.3 MOSI, P3.4 MISO and P3.5
chip select. Hardware SPI must not be enabled without a board wiring change.

## Timing and quality verification

`tools/tracker10/reference.py` is the host semantic reference for row/tick,
effect-memory, envelope/release, Amiga scaling and timing behavior. Format tests
cover structural round-trip, CRC corruption, pattern reuse, instrument parsing
and explicit rejection of unsupported effects.

CRC32 is generated and verified by the host tools. The 8051 deliberately does
not scan the complete score at boot to calculate it; device reads remain guarded
by range, field-mask and macro validation.

Hardware acceptance requires:

- P5.5 worst-case ISR width below 25 microseconds at 32 kHz;
- zero queue underruns after startup;
- UART protocol responsiveness during playback;
- audible independent tonal and both noise modes;
- comparison of arpeggio, portamento, vibrato and retrigger passages against the
  host reference.

On the current STC8H3K64S2 board, a ten-second UART measurement advanced the
audio-derived system clock by 10.078 seconds (about 0.3%, including command
latency). During the same run the VM crossed multiple patterns with parser error
zero, the four-entry queue remained full, and underruns remained zero. Hardware
listening also exposed and verified the fix for source-sample tuning metadata:
copying XM `relative note` into a normalized oscillator had transposed individual
instruments by as much as 17 semitones.

The T10 v3 envelope/effect build was subsequently checked for 14 seconds across
multiple patterns. Firmware and format reported version 3, parser error remained
zero, queue depth remained four, underruns remained zero, and the audio-derived
clock advanced 14.119 seconds over 14.070 seconds of host wall time. The added
release/fadeout and Amiga-effect work therefore remains outside the 32 kHz ISR
and does not exhaust the producer queue.

After changing the ten-voice mix scaling from `sum >> 4` to `sum >> 8`, 500
live UART snapshots covered a signed mix range of -54 through +56 and a PWM
range of 74 through 184. No snapshot reached the signed saturation threshold or
either PWM rail, and the queue underrun counter remained zero. This is the
baseline gain for listening tests; louder output belongs in the analog stage or
in an explicitly measured limiter, not in unchecked digital gain.

Generated or downloaded music used for listening tests must have clear
redistribution permission before it is retained in a public repository. The
source XM for the current local Funky Stars conversion is intentionally absent.
The locally fetched verification file has SHA-256
`2b7ce3c9efa7bb94067c1c7b00ed8b43433120f2ac2992903b09afd3d33739e3`;
this identifies the exact source used to regenerate `scoreList.c` without
redistributing that source in the repository.

The complete byte-level contract is in [T10Format.md](T10Format.md).
