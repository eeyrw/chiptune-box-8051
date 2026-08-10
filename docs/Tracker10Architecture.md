# Tracker10 Architecture

## Goal

The target is an efficient 8-bit tracker renderer for an STC8H3K64S2, not an
emulation of any console sound chip. The source format may be XM or another
tracker format; the device format is deliberately independent of its source.

## Split of work

The host compiler owns musical complexity. It parses patterns and order flow,
expands tracker ticks, applies tempo/speed changes, arpeggio, tone portamento,
vibrato and volume slides, assigns a small waveform to each source instrument,
then emits only timestamped oscillator state changes.

The MCU main loop validates `T10P/T10M`, decompresses upcoming events, converts
8.8 fixed-point pitch to a 24-bit DDS increment, and keeps a four-entry XRAM
control queue filled. The 32 kHz ISR consumes the queue, applies all changes at
one sample boundary, mixes ten voices, and writes the 8-bit PWM sample.

No tracker effect parser, floating-point calculation, allocator, ADSR machine,
SPI transaction, or serial command runs in the synthesis hot path.

## Hot state and memory

Each voice occupies exactly eight DATA bytes:

| Bytes | Meaning |
|---:|---|
| 0..2 | 24-bit phase |
| 3..5 | 24-bit phase increment |
| 6 | volume, 0..31 |
| 7 | prepacked waveform offset (`wave << 4`) |

Ten voices consume 80 bytes. `mixOut`, `pwmSample`, and the 10-bit debug mute
mask bring the absolute block to 85 bytes at DATA `0x21`. Register bank 1 is
reserved for Timer0. The linked stack starts at `0x76`, leaving 138 bytes.

The queue and decoder live in XRAM. The optional SPI driver retains its 1 KiB
read cache; the firmware still fits comfortably in the 3 KiB internal XRAM.

## Synthesis

There are eight signed 16-sample waveforms: square, two pulse widths, triangle,
saw, sine-like, hollow and deterministic noise. A table index is formed from
the top four phase bits plus the prepacked waveform offset. No interpolation is
used. The deliberately coarse table is part of the 8-bit sound and substantially
reduces cycles compared with generic PCM wavetable interpolation.

For every voice the assembly hot path performs a code-memory lookup, signed
8-bit sample by unsigned volume multiplication, 24-bit accumulation and 24-bit
phase advance. The loop is assembler-expanded ten times. After all voices the
sum is shifted by eight, saturated to signed 8-bit, biased by 128, and written
to PWM. A muted voice continues advancing phase, so debug unmute does not
retrigger it.

## T10P container

`T10P` starts with a 16-byte header: magic, version, track count, total image
size and reserved word. Each eight-byte table entry contains a track offset and
size. Tracks are contiguous `T10M` objects. The same image works in internal
code Flash or at SPI address zero.

## T10M track

The 32-byte header contains magic/version, voice count, flags, 32 kHz sample
rate, event offset/size, loop event offset, total samples and reserved data.
Time is expressed in output samples, avoiding millisecond scheduling jitter.

General voice events update any combination of increment, volume, waveform and
phase reset. Frequent cases have shorter opcodes:

- absolute 8.8 pitch, with or without phase reset;
- signed 8-bit movement from the previous pitch for vibrato and slides;
- packed complete note state: pitch plus five-bit volume and three-bit wave;
- volume-only update;
- repeated wait or signed change from the previous wait duration.

Pitch values are converted in the main loop with a 129-entry semitone increment
table and linear interpolation. Thus an ordinary pitch event stores two bytes
instead of a 24-bit phase increment, and a small pitch movement stores one byte.
Loop entry begins with a full ten-voice snapshot and an absolute wait, so decoder
history at the end of the song cannot corrupt the loop.

## Source conversion

`tools/tracker10_tool.py` currently accepts FastTracker II XM. It maps all ten
source channels directly to ten logical device channels. It does not collapse
voices into NES-style roles. Instrument samples are presently approximated by
the eight resident waveforms; percussion uses the deterministic noise waveform.

Future sample support should be a generic optional 4-bit ADPCM one-shot layer.
It must not expose NES DPCM registers or change the ten-channel tracker model.
With internal Flash, samples are admitted only after the event stream and code
budget are known; the current full Funky Stars event image leaves roughly 8 KiB
after retaining the SPI backend.

## Storage

At boot, `storage_auto_detect()` reads the SPI JEDEC ID. A valid response selects
the SPI backend; `0x00/0xFF` selects the internal code-Flash image. The player
only uses `ScoreStream`, so synthesis and decoding do not depend on the backend.
The current physical board may omit the SPI NOR without requiring a build flag.
