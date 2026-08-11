# T10 v3 Semantic Score Format

## 1. Scope and byte order

T10 v3 is the device format for Tracker10. It stores tracker-level musical
semantics, not MIDI messages, PCM timestamps, NES register writes, or expanded
32 kHz oscillator states. All multibyte integers are unsigned little-endian
unless a field is explicitly described as signed. All offsets inside a T10M
object are relative to the first byte of that T10M object.

The format has two layers:

- `T10P` is a playlist/container containing one or more tracks.
- `T10M` is one semantic tracker song containing orders, patterns and compiled
  instruments.

Unknown versions must be rejected. Reserved fields and bits are written as zero
and ignored by a v3 reader only where this document explicitly permits it.

## 2. T10P playlist

### 2.1 Header

The playlist header is 16 bytes.

| Offset | Size | Field | Constraint |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `T10P` |
| 4 | 1 | version | `3` |
| 5 | 1 | flags | `0` in v3 |
| 6 | 2 | track count | `1..65535` |
| 8 | 4 | total image size | Includes header, directory and all tracks |
| 12 | 4 | reserved | Written as zero |

The header is followed immediately by `track count` directory entries. Each
entry is eight bytes:

| Relative offset | Size | Field |
|---:|---:|---|
| 0 | 4 | T10M offset from start of T10P |
| 4 | 4 | T10M byte size |

An entry must not overlap the T10P header or directory and must fit completely
inside `total image size`. T10P has no separate CRC because every T10M carries a
body CRC and all container ranges are validated before a track is opened.

## 3. T10M header

The T10M v3 header is exactly 48 bytes.

| Offset | Size | Field | Constraint or meaning |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `T10M` |
| 4 | 1 | version | `3` |
| 5 | 1 | physical voices | `10` |
| 6 | 1 | flags | Bit 0: loop; bit 1: Amiga-period effects; bits 2..7: zero |
| 7 | 1 | reserved | Zero |
| 8 | 4 | output sample rate | `32000` |
| 12 | 4 | order table offset | Usually 48 |
| 16 | 2 | order count | `1..256` |
| 18 | 2 | restart order | Less than order count |
| 20 | 4 | pattern directory offset | Valid in-track range |
| 24 | 2 | pattern count | `1..255` |
| 26 | 2 | instrument count | `1..255` |
| 28 | 4 | instrument table offset | Valid in-track range |
| 32 | 4 | total T10M size | Must equal container entry size |
| 36 | 4 | body CRC32 | CRC of bytes `[48, total size)` |
| 40 | 2 | initial speed | Tracker ticks per row, `1..255` |
| 42 | 2 | initial BPM | `32..999` |
| 44 | 4 | reserved | Zero |

CRC32 is the reflected IEEE polynomial `0xEDB88320`, initialized to
`0xFFFFFFFF` and finally XORed with `0xFFFFFFFF`. This is the same result as
Python `zlib.crc32(body)`. Host tools verify it when compiling, inspecting or
writing an image. The 8051 does not scan the score to calculate CRC at boot;
doing so is disproportionately slow on this target. It relies on bounded table,
row, cell and macro validation while reading.

## 4. Orders and patterns

### 4.1 Order table

Each order is one byte containing a pattern index. Repeating a pattern therefore
costs one byte and does not duplicate its rows. When the last order finishes:

- with the loop flag set, execution continues at `restart order`;
- otherwise the VM queues a terminal event and silences all voices.

Tracker channel state is continuous across an order jump, including the end to
restart jump. Pattern reuse does not implicitly reset instruments, effects,
macro positions or oscillator phases.

### 4.2 Pattern directory

Every pattern has an eight-byte directory entry:

| Relative offset | Size | Field |
|---:|---:|---|
| 0 | 4 | pattern data offset |
| 4 | 2 | encoded byte size |
| 6 | 2 | row count, `1..256` |

Pattern rows are decoded sequentially. Random row seeking is deliberately not
required, which keeps both the internal-Flash and bit-banged SPI readers simple.

### 4.3 Row encoding

Each row starts with a 16-bit changed-channel mask. Bits 0..9 correspond to the
ten physical tracker channels; bits 10..15 must be zero. For every set bit, in
ascending channel order, one cell token follows.

The first byte of a cell is a field mask:

| Bit | Name | Following bytes |
|---:|---|---|
| 0 | NOTE | One note byte |
| 1 | INSTRUMENT | One 1-based instrument number |
| 2 | VOLUME | One normalized volume byte |
| 3 | EFFECT | Effect byte followed by parameter byte |
| 4..7 | reserved | Must be zero |

The cell mask must be nonzero. Fields appear strictly in NOTE, INSTRUMENT,
VOLUME, EFFECT order. This permits a bounded decoder without opcode dispatch.

Note values `1..96` are XM chromatic notes and `97` is key-off. Instrument zero
is represented by omission of the field. On disk, VOLUME is the actual tracker
volume `0..64`; omission means no volume command. In the Python IR only, values
`1..65` are used so zero can remain the omitted-field sentinel; encoding
subtracts one and decoding adds one.

At every new row the active effect opcode is cleared on every channel, while
effect memory remains. Missing note, instrument and volume fields retain their
previous musical state.

## 5. Compiled instruments

Each instrument record is exactly 48 bytes. It is intentionally fixed-size so a
complete active instrument can be copied to XRAM with one bounded operation;
the 32 kHz ISR never reads score storage.

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | oscillator mode |
| 1 | 1 | gain, `0..31` |
| 2 | 2 | signed relative pitch, Q8.8 semitones |
| 4 | 1 | volume macro length, `1..16` |
| 5 | 1 | volume step duration in tracker ticks, `1..255` |
| 6 | 1 | volume flags: bit 0 enabled, bit 1 sustain, bit 2 loop |
| 7 | 1 | volume sustain position or `0xFF` |
| 8 | 1 | volume loop-start position or `0xFF` |
| 9 | 1 | volume loop-end position or `0xFF` |
| 10 | 1 | pitch macro length, `1..16` |
| 11 | 1 | pitch loop index or `0xFF` |
| 12 | 2 | key-off fadeout decrement per tracker tick |
| 14 | 2 | reserved, zero |
| 16 | 16 | volume macro storage |
| 32 | 16 | signed pitch macro storage |

Unused macro bytes are zero. A pitch loop index must be less than its macro
length; `0xFF` means advance to the final element and hold it. Disabled volume
sustain and loop positions must be `0xFF`. Enabled loop positions satisfy
`start <= end < length`.

The volume position advances only after `volume step duration` tracker ticks.
While key-on is true it holds at the sustain position. After key-off, sustain is
released and the position continues. An enabled loop moves from loop end back to
loop start both before and after key-off. Fadeout, rather than disabling the
envelope loop, eventually silences a released voice.

Fadeout state starts at `65535`. On every released tracker tick the instrument
fadeout value is subtracted with saturation at zero. Output volume is additionally
scaled by this state. Fadeout is evaluated only when the volume-envelope enabled
flag is set. Key-off immediately silences an instrument with no enabled volume
envelope, matching the source tracker rule.

Volume macro entries are `0..32`. Runtime output volume is evaluated in two
rounded 16-bit stages to avoid overflow on the 8051:

```text
level = (channel_volume * instrument_gain + 32) / 64
output = (level * macro_value + 16) / 32
```

The result is clamped to `0..31`. Pitch macro entries are signed
quarter-semitone offsets. They are added to the
Q8.8 channel pitch by multiplying by 64.

The relative-pitch field describes a deliberate offset in the compiled
instrument, not the source sample's tuning metadata. When an XM sample is
replaced by a normalized single-cycle oscillator, its `relative note` and
`finetune` calibration must be removed or baked into sample analysis rather than
copied here; otherwise the musical note is transposed a second time.

Oscillator modes are:

| Mode | Meaning |
|---:|---|
| 0 | 50% square |
| 1 | 25% pulse |
| 2 | 12.5% pulse |
| 3 | triangle |
| 4 | saw |
| 5 | sine-like |
| 6 | long-period broadband noise |
| 7 | short-period metallic noise |

Modes 6 and 7 select shared noise generators. Each generator advances exactly
once per output sample regardless of how many voices select it. Voice count and
channel order therefore cannot alter noise pitch or sequence.

Macros are evaluated at tracker-tick rate. Tick 0 reads element zero; positions
advance after the tick output has been produced. A normal note resets envelope,
fadeout and pitch-macro state and requests an oscillator phase reset. Tone
portamento does none of those. An instrument command resets its envelope state.

## 6. Tracker VM semantics

The VM executes in the MCU main loop. For each tracker tick it performs this
strict sequence:

1. On tick 0, decode the next pattern row and clear per-row effect selections.
2. Resolve effect parameter memory and apply note/instrument/volume commands.
3. On ticks greater than zero, advance continuous effects.
4. Evaluate arpeggio, vibrato and instrument pitch macro without mutating the
   underlying note except where the effect itself is a slide.
5. Evaluate volume envelope, release fadeout and produce changed DDS controls.
6. Advance envelope and pitch-macro cursors, then update fadeout.
7. Calculate the exact wait to the next tracker tick and enqueue the control.

Tick duration uses an integer remainder accumulator:

```text
numerator = remainder + 80000
wait_samples = numerator / BPM
remainder = numerator - wait_samples * BPM
```

This implements `32000 * 2.5 / BPM` without floating point and without long-term
rounding drift. Speed controls ticks per row and does not enter this calculation.

### 6.1 Supported normalized effects

| Effect | Semantics | Parameter memory |
|---:|---|---|
| `0xy` | Arpeggio: base, +x, +y semitones | No |
| `1xx` | Pitch slide up, linear or Amiga-period scaled | Yes |
| `2xx` | Pitch slide down, linear or Amiga-period scaled | Yes |
| `3xx` | Tone portamento to note target | Yes |
| `4xy` | Sine vibrato speed x, depth y | Nibble-wise |
| `Axy` | Volume slide; up wins when x is nonzero | Yes |
| `E9x` | Retrigger oscillator and instrument macros every x ticks | No |
| `Fxx` | Speed when `< 0x20`, otherwise BPM | No |

Effect movement begins on tick 1. A zero parameter for effects with memory reuses
the previous nonzero value. `400` therefore continues both remembered vibrato
speed and depth. Panning effect `8xx` and volume-column `Cx` are deliberately
discarded by the host because the hardware output is mono.

When header flag bit 1 is clear, slide parameters use `xx/16` semitone per
nonzero tick and vibrato depth uses the normalized linear model. When bit 1 is
set, the main-thread VM uses a code-memory scale table indexed by the current
Q8.8 note. It converts source period deltas into note deltas for `1xx`, `2xx`,
`3xx`, and `4xy`. This preserves the note-dependent strength of Amiga-period
effects without carrying periods, logarithms or division into the ISR.

Any other source effect currently causes compilation to fail with its pattern,
row and channel location. Silent approximation is forbidden. Future frontends
may lower an otherwise unsupported effect to a bounded per-channel automation
macro without changing the device VM.

## 7. Validation and storage rules

A reader validates, in order:

1. container magic, version, count and total size;
2. selected track range;
3. T10M magic, version, voice count and sample rate;
4. table offsets, counts and all multiplication/addition bounds;
5. every pattern directory entry and row/cell mask while it is decoded;
6. instrument modes, macro lengths, values and loop points when loaded.

Host tools additionally validate the complete track CRC32. Device-side CRC is
deliberately omitted from the 8051 runtime budget.

Internal code Flash and SPI NOR use the same bytes. SPI data is read only by the
main loop through `ScoreStream`; pattern access is sequential and active
instrument records are copied into channel XRAM. No SPI transaction occurs in
the audio ISR.

## 8. Versioning

Incompatible header, row, effect or instrument semantics require a new T10M
version. New source frontends do not require a format version when they lower to
the existing normalized effects and instrument modes. Reserved fields are not a
license to change behavior without versioning.

T10M v1 was the sample-timed oscillator event stream. T10M v2 introduced orders,
patterns and 40-byte fixed macro instruments. T10M v3 replaces those instruments
with 48-byte timed envelope records and adds the Amiga-period effect flag. The v3
firmware intentionally rejects both older versions; the host compiler is the
migration boundary.
