# Tracker10 Architecture

## Product boundary

Tracker10 is a generic ten-channel 8-bit wavetable tracker player for the
STC8H3K64S2. It is not an NES, APU, VRC, MIDI or DPCM emulator. Source modules
are compiled offline to T10P/T10M v4; the MCU never parses XM structures or
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
  -> pattern/order compaction, fixed instrument macros and resource extraction
  -> T10M v4 + CRC32
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

Looped XM samples are converted to signed 16-point song wavetables. Short loops
are resampled directly; long loops first undergo bounded autocorrelation to
extract a representative period. Tables are DC-centered, normalized and
deduplicated. This preserves the original pulse-width family and distinctive
chip waveforms instead of reducing every source to a small built-in palette.

Long non-looped samples are treated as one-shot percussion. The host selects the
instrument's most frequent trigger note, applies XM relative-note and finetune
calibration, and linearly resamples to signed 8 kHz PCM. No PCM decoding, rate
conversion or interpolation remains for the MCU.

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

The Timer0 ISR is a constant-time audio consumer:

```text
32 kHz interrupt
  -> read one byte from the 256-byte XRAM audio ring
  -> write PWMA CCR2
```

No tracker control application, synthesis, pattern parsing, division, pitch
lookup, allocation, SPI transaction or serial handling occurs in Timer0. P5.5
remains high for the complete ISR and is the hardware timing point.

For timing diagnosis without a logic analyser, Timer0 checks its overflow flag
at ISR exit. A second overflow means the 31.25 microsecond deadline was crossed;
that condition latches AUDIO_INFO state bit 15. Final mixer saturation similarly
latches bit 14. Reading AUDIO_INFO clears both. These flags share otherwise
unused high mute-state bits and add no hot DATA or XRAM.

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
The main thread applies due tracker controls, calls the ten-lane unrolled
`WavetableSynthStep.s`, and publishes complete unsigned samples into a 256-byte
XRAM ring. Monotonic 8-bit indices give 255 usable bytes, or 7.97 milliseconds
at 32 kHz. `AudioRenderProcess` renders at most 16 samples per main-loop visit,
keeping protocol service responsive while maintaining average producer speed.
The write index advances only after a sample byte is complete; Timer0 owns the
read index. Timer0 uses register bank 2, while `AudioRenderOne` temporarily uses
bank 1, so an interrupt can safely preempt rendering.

## Synthesis state

Each fixed voice occupies eight absolute DATA bytes:

| Bytes | Meaning |
|---:|---|
| 0..2 | 24-bit DDS phase, or PCM cursor low/mid and remaining low |
| 3..5 | 24-bit DDS increment, or PCM remaining high, cached sample and hold counter |
| 6 | volume `0..31`; bit 7 selects PCM and bit 6 requests cache priming |
| 7 | prepacked wavetable ID (`ID << 4`), or unused in PCM mode |

Ten voices consume 80 bytes. The remaining absolute state is `mixOut` (2), PWM
sample (1), debug mute mask (2), long noise state (2), short noise state (1) and
two current signed noise samples (2). The complete block is 90 bytes at DATA
`0x21`. Register bank 1 belongs to Timer0. The linked stack begins at `0x7B`,
leaving 133 bytes.

Tonal voices use signed 16-sample tables from the active song's internal Code
Flash resource bank. PCM voices read signed 8 kHz one-shots from the same image
with `MOVC`; each fetched byte is cached and emitted for exactly four 32 kHz
interrupts. Because a tracker channel
owns its PCM state, all ten fixed voices may play PCM without allocation or new
hot-state bytes. The PCM lane applies a fixed 1.5x transient gain before the shared
24-bit accumulator; the final saturator remains the only clip point. The two
noise selectors consume shared long-period and short-period LFSR samples. Both LFSRs advance once per sample,
outside the voice expansion, so enabling a second drum voice cannot change the
noise rate or sequence.

PCM trigger priming is staggered by fixed channel index modulo four. This adds
at most three 32 kHz frames (93.75 microseconds) of one-time onset offset, then
every lane continues at exactly 8 kHz. The staggering prevents multiple drum
lanes from synchronizing all Code Flash fetch and cursor-update work into every
fourth ISR.

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
| program Flash | 53,318 bytes | 65,024-byte programmed region |
| XRAM | 2,871 bytes | 3,072 bytes |
| absolute DATA hot state | 90 bytes | fixed at `0x21` |
| stack | 133 bytes | starts at `0x7B` |
| T10P score image | 24,599 bytes | internal Code Flash |

The SPI backend remains compiled and retains a 1 KiB XRAM read cache. T10M v4
audio resources are internal-Code-Flash-only and opening v4 from SPI is rejected.
The current PCB crosses the SPI
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

The T10 v4 resource build was checked on the same board after compiling Funky
Stars to 12 deduplicated song wavetables and five 8 kHz PCM percussion samples
totalling 5,185 bytes. UART snapshots observed simultaneous PCM activity on two
fixed tracker voices; the complete reference run contains ticks with three PCM
triggers, which the ten-channel design handles without allocation. Firmware
reported format v4, parser error and underruns stayed zero, signed mix snapshots
ranged from -39 through +26, and PWM ranged from 89 through 154 without reaching
either rail.

After changing the ten-voice mix scaling from `sum >> 4` to `sum >> 8`, 500
live UART snapshots covered a signed mix range of -54 through +56 and a PWM
range of 74 through 184. No snapshot reached the signed saturation threshold or
either PWM rail, and the queue underrun counter remained zero. This remains the
tonal baseline gain.

Listening with the extracted percussion isolated showed that PCM triggering was
correct but its short transients were masked by the continuous tonal voices. A
fixed PCM-only mixer gain made percussion audible in the complete mix. The first
2x setting later reached the saturator in a 60-second run, so the retained 1.5x
setting balances audibility with headroom. With
all channels enabled, live snapshots in a two-PCM passage ranged from -30
through +41, did not approach either PWM rail, and queue underruns remained zero.

The first v4 PCM build latched Timer0 deadline overruns at tracker-event
boundaries despite a full producer queue. Replacing the C queue consumer with a
fixed-layout assembly path was not sufficient when all ten controls were still
applied in one interrupt. Splitting control application across interrupts also
reduced but did not reliably eliminate the peak. The final architecture instead
pre-renders complete samples in the main thread and makes Timer0 a byte-only
consumer. A 90-second hardware run ended with 152 buffered samples while two PCM
voices were active: audio underruns zero, `isr_overrun=false`, `clip=false` and
parser error zero. The board remains at 33.1776 MHz; increasing the clock is not
required.

Generated or downloaded music used for listening tests must have clear
redistribution permission before it is retained in a public repository. The
source XM for the current local Funky Stars conversion is intentionally absent.
The locally fetched verification file has SHA-256
`2b7ce3c9efa7bb94067c1c7b00ed8b43433120f2ac2992903b09afd3d33739e3`;
this identifies the exact source used to regenerate `scoreList.c` without
redistributing that source in the repository.

The complete byte-level contract is in [T10Format.md](T10Format.md).
