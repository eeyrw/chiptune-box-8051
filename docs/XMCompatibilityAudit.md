# XM to Tracker10 Compatibility Audit

## 1. Scope and conclusion

This audit compares the Tracker10 XM frontend and runtime against FastTracker II
semantics. The goal is not to emulate FT2 on the STC8H. XM remains a host-side
source format and must be lowered to compact, bounded T10M operations. The audit
therefore separates:

1. source parsing errors;
2. semantic lowering errors that should be fixed offline or in the tick VM;
3. intentional product reductions that must be explicit and measurable;
4. audio-quality reductions that require listening tests rather than semantic
   claims.

The reported momentary "stuck" sensation is not a Timer0 or queue scheduling
failure. Hardware A/B tests established all of the following:

- muting PCM mixing globally removes the sensation;
- the audio ring does not underrun during playback;
- Timer0 does not cross its deadline;
- the final mixer does not clip;
- muting tracker channel 8 removes the sensation;
- leaving channel 8 enabled while muting channel 7 retains it;
- channel 8's PCM is almost never cut off by a later trigger.

The remaining high-confidence cause was a source-to-device rendering difference
in channel 8, not a fluctuating clock. That channel repeatedly plays instrument
6 every 115.38 ms. Its source playback rate is about 16.726 kHz and its one-shot
duration is about 105 ms. The original v4 implementation resampled it to 8 kHz
and emitted each stored byte four times at 32 kHz. The retained implementation
now uses 16 kHz PCM, a two-frame hold and an anti-aliased host resampler.

## 2. References

The comparison used these sources:

- the [unofficial XM specification](https://milkytracker.org/docs/XM_file_format.pdf),
  which incorporates the original Triton XM 1.04 description;
- the [MilkyTracker manual](https://milkytracker.org/docs/manual/MilkyTracker.html),
  especially the XM effect reference;
- the [OpenMPT XM effect reference](https://wiki.openmpt.org/Manual%3A_Effect_Reference);
- the [OpenMPT compatible-playback notes](https://wiki.openmpt.org/Manual%3A_Compatible_Playback);
- 8bitbubsy's FT2 clone at commit
  `e725a93b21cd5455e748dbac7b3173213367a8bb`;
- libxmp at commit `a13276d27feabcf9ee4f982913f718ee05a65cb7`;
- libxmp's FT2/XM event and mixer regression corpus;
- the original `/home/yuan/funky stars.xm`, SHA-256
  `2b7ce3c9efa7bb94067c1c7b00ed8b43433120f2ac2992903b09afd3d33739e3`.

MilkyTracker or FT2-clone playback of the original module is the subjective
reference. FFmpeg's libopenmpt demuxer is useful for repeatable host rendering,
but compatibility modes differ in small FT2 edge cases.

## 3. Source module profile

Funky Stars contains:

| Property | Value |
|---|---:|
| XM channels | 10 |
| Orders | 19 |
| Referenced patterns | 15 |
| Instruments | 22 |
| Initial speed / BPM | 6 / 130 |
| Frequency mode | Amiga periods |
| Compiled row cells | 4,755 |
| T10 song waves | 12 |
| T10 PCM samples | 5 |

Effects actually used are:

| Source command | Occurrences | Current treatment |
|---|---:|---|
| `8xx` panning | 1,847 | discarded |
| `4xy` vibrato | 560 | normalized |
| `Axy` volume slide | 507 | supported |
| `3xx` tone portamento | 283 | normalized |
| `0xy` arpeggio | 224 nonzero | supported |
| `2xx` slide down | 25 | normalized |
| `E93` retrigger | 1 | supported |

The volume column contains 1,586 panning commands (`Cx`) and normal volume-set
commands. There are 68 new-note events without an instrument number, all on
channel 5. There are no instrument-only rows.

## 4. Parser audit

### 4.1 Correctly handled

The parser correctly handles the structures exercised by this module:

- variable module, pattern, instrument and sample header sizes;
- packed and unpacked pattern cells;
- 8-bit and 16-bit delta-coded PCM;
- sample length and loop byte-to-sample conversion for 16-bit sources;
- sample volume, finetune and relative note;
- volume envelope points, sustain, loop and fadeout;
- restart order and Amiga/linear frequency flag;
- instrument keymaps and multiple source samples at parse time.

Bounds checks reject truncated headers, pattern data and sample data.

### 4.2 Missing source fields

The parser currently does not retain:

- sample default panning;
- panning envelope points and flags;
- instrument automatic-vibrato type, sweep, depth and rate;
- the sample reserved/name-length byte and ModPlug ADPCM extension;
- stereo and nonstandard compressed sample extensions.

The compiler now parses these flags and reports warnings when active
auto-vibrato or panning-envelope semantics will be dropped. This is explicit but
still not faithful lowering; strict applications should treat warnings as a
manual-review gate. Funky Stars instrument
21 uses automatic vibrato `(type=3, sweep=29, depth=5, rate=20)`, and instrument
22 enables a panning envelope. Instrument 21 is actively used, so auto-vibrato
loss is audible in this song.

### 4.3 Keymap lowering

Parsing preserves the full 96-key map, but compilation selects only the sample
mapped by an instrument's most frequent note and then assigns that one resource
to the entire device instrument. This is an intentional reduction, not faithful
XM behavior. It is harmless for Funky Stars because every nonempty instrument
has a single source sample. General XM input needs an offline key-zone split:
duplicate the compiled instrument per used sample zone and rewrite pattern
instrument numbers.

## 5. Event semantics audit

### 5.1 Tick timing

XM tick duration is `2.5 / BPM` seconds. T10 uses:

```text
numerator = remainder + 32000 * 5 / 2
tick_samples = numerator / BPM
remainder = numerator % BPM
```

This is exact in the long term. Speed remains ticks per row. The buffered audio
consumer prevents main-loop latency from moving event boundaries. This part is
sound and is not implicated by the hardware evidence.

### 5.2 New note without instrument: definite bug

FT2 plays a new note without an instrument number using the current instrument,
sample and current channel volume. Tracker10 currently resets channel volume to
64 for every new note unless that same cell has a volume command. This is wrong.

Funky Stars hits this case 68 times on channel 5, so the bug is not theoretical.
The fix is small: reset channel volume to 64 only when a new instrument is
present; otherwise retain the current volume. A same-cell volume command still
wins.

### 5.3 Instrument without note

FT2 keeps the current sample playing, restores the old sample's default volume
and panning, and retriggers instrument envelope state. Tracker10 immediately
replaces the channel's compiled instrument definition. Funky Stars has no such
rows, but a general compiler needs either normalized explicit operations or a
compile-time rejection until the behavior is represented.

### 5.4 Tone portamento

The important high-level rule is present: a note with `3xx` sets a target and
does not restart oscillator phase or instrument envelopes. Parameter memory is
implemented. The conversion of Amiga-period increments to Q8.8 note increments
is an approximation and should be tested against reference traces over the note
range, especially at low notes.

### 5.5 Arpeggio

For ordinary speed values, the base, high-nibble and low-nibble cycle is
correct. FT2 has an out-of-bounds lookup quirk for ticks 16..31; Tracker10 uses a
clean modulo-three model. This song uses speed 6, so the quirk is irrelevant.

### 5.6 Vibrato

The apparent factor-of-four difference is not a bug. FT2 advances a 256-step
phase by `4*x`; Tracker10 advances a 64-step phase by `x`, producing the same
cycle duration. Remaining differences are:

- only sine is represented; `E4x` waveform selection is unsupported;
- FT2 has exact period-domain depth rules and phase-reset rules;
- T10's Amiga conversion is a note-domain approximation;
- instrument automatic vibrato is not parsed or compiled.

These differences explain earlier reports that some vibrato sounded unusual.

### 5.7 Volume slide

`Axy` parameter memory and tick-1-through-last execution agree with FT2 for the
values used here. FT2 gives the high nibble priority when both are nonzero;
Tracker10 does the same.

### 5.8 `E9x` retrigger

FT2 restarts the sample and retriggers instrument state. Tracker10 also resets
its compiled volume/pitch macro and fade state. The tick condition must remain
covered by a trace test because FT2 internally counts ticks down, but the one
`E93` in Funky Stars is not responsible for the repeated channel-8 symptom.

### 5.9 Note-off and fadeout: definite scale bug

FT2 initializes fadeout to 32768 and subtracts the instrument fadeout value once
per released tick. T10 initializes it to 65535 and subtracts the unscaled source
value. Release therefore lasts roughly twice as long. Correcting the initial
range also requires changing the output scaling divisor; changing only one side
would halve released-note volume.

Funky Stars' PCM instruments have no volume envelope and therefore cut according
to the existing no-envelope rule. The fadeout bug mainly affects its tonal
instruments.

## 6. Instrument and sample lowering

### 6.1 Wavetable classification

Looped samples with at least eight loop points become 16-point cyclic tables.
This is appropriate for chip-like periodic material but deliberately discards:

- the non-looped attack before the loop;
- timbral variation over a long loop;
- sample interpolation behavior;
- sample-relative tuning after waveform normalization.

The generated table is DC-centered and peak-normalized, so source sample volume
is represented separately by instrument gain. The autocorrelation period search
is heuristic and needs golden waveform tests.

### 6.2 PCM classification

Long non-looped samples become fixed-rate one-shots. The source playback rate
at the most common trigger note is baked into the resampling operation. Funky
Stars produces:

| PCM ID | XM instrument | Common note | Source playback rate | 8 kHz bytes |
|---:|---:|---:|---:|---:|
| 0 | 2 | 61 | 16.726 kHz | 679 |
| 1 | 3 | 54 | 11.163 kHz | 1,365 |
| 2 | 4 | 61 | 16.726 kHz | 963 |
| 3 | 5 | 61 | 17.721 kHz | 1,335 |
| 4 | 6 | 61 | 16.726 kHz | 843 |

The current converter uses pointwise linear interpolation but no low-pass
filter before downsampling. Spectral analysis of the five source voices found
approximately `3.5%, 0.8%, 38.5%, 87.3%, 2.3%` of energy above the 8 kHz
resource Nyquist limit. PCM IDs 2 and 3 therefore alias heavily. ID 4, used on
channel 8, has little out-of-band energy, so aliasing alone does not fully
explain that channel's subjective effect.

### 6.3 PCM pitch and control after trigger

Once a T10 PCM voice starts, its byte rate and volume are latched. Later tracker
ticks do not alter an active one-shot's volume or rate. This differs from XM,
where envelopes and effects continue while the sample runs. A general solution
does not require an XM interpreter on the MCU. The compiler can:

- bake short one-shot automation into sample data when unique;
- emit bounded PCM-rate and PCM-volume control events;
- create deduplicated pre-rendered variants for common effect sequences;
- reject a source construct when none of those reductions is safe.

Funky Stars' percussion has no enabled volume envelope and is mostly triggered
at one pitch without pitch effects, so this limitation is not the primary
channel-8 cause.

### 6.4 Trigger transitions

FT2-clone optionally crossfades an old voice out and a newly triggered voice in
over about 5 ms. T10 resets a fixed voice immediately. Analysis found only one
channel-8 trigger before the previous one-shot ended, about 5 ms early, and the
old/new byte discontinuity was only one quantization step. Retrigger transition
handling is desirable but is not the repeated symptom.

The compiler's 4 ms tail fade removes end-to-silence clicks. It cannot smooth a
mid-sample retrigger and should not be described as doing so.

## 7. Panning and mono lowering

Discarding panning is valid for channel placement but not exactly neutral for
loudness. FT2 uses a square-root panning law:

```text
left  = volume * sqrt((256 - pan) / 256)
right = volume * sqrt(pan / 256)
```

If stereo is averaged to mono, the resulting gain depends slightly on pan.
Funky Stars places most channel-8 drum notes at pan `0xE0`; the average-fold
gain is about 0.81 dB below center. T10 currently makes every pan equally loud.
This contributes to balance but is too small to explain the symptom alone.

The correct host lowering is to track effective source pan, apply a documented
mono-fold gain to each note or instrument variant, then discard pan position.
Panning envelopes require tick automation or an offline approximation.

## 8. Mixer and hardware audit

The 32 kHz Timer0 ISR only consumes a byte from the 255-sample effective XRAM
ring and writes PWM. Synthesis and event application run ahead in the main
thread. Hardware diagnostics during the faulty-sounding passages show no new
audio-ring underruns, no ISR overrun and no final saturation.

The ten-lane mixer uses a 24-bit accumulator and shifts by ten before signed
8-bit saturation. Tonal control gain is 7-bit, so its full-scale gain equals the
older 5-bit/shift-by-eight design while retaining two extra quiet-volume bits.
PCM is multiplied by 1.5 and mapped into the same gain domain before accumulation. A previous 2x
experiment clipped; 1.5x did not. Global RMS normalization and a PCM-body
attenuation experiment both made drums too light or failed to remove the
reported sensation. Those experiments are rejected as general format policy.

The underrun diagnostic is an 8-bit counter. STOP empties the audio ring while
Timer0 continues consuming silence, so the counter can increase between test
runs. A valid experiment must compare the counter before and after the listening
window, not assume a nonzero absolute value means an active-playback underrun.

## 9. Retained 16 kHz PCM result

The implemented 16 kHz experiment confirms this is feasible for the song:

- PCM storage grows from 5,185 to 10,365 bytes;
- the experiment image was 58,546 bytes in the 65,024-byte program region, leaving a
  smaller but usable margin;
- XRAM and hot DATA layouts do not grow;
- the PCM cache hold changes from four output frames to two;
- Code Flash fetch frequency doubles only while PCM voices are active;
- Timer0 remains a byte-only consumer because synthesis is pre-rendered.

The compiler must use a proper bounded low-pass resampler for any source rate
above 16 kHz. A small windowed-sinc/polyphase implementation in Python is
appropriate; adding SciPy as a build dependency is unnecessary.

Acceptance was not merely "sounds brighter". The 16 kHz experiment used the
same song start and mute state and verified:

- channel-8 rapid drum passage no longer sounds momentarily stuck;
- drum attacks remain clearly audible;
- buffer level remains stable and playback underruns do not increase;
- ISR overrun and clip flags remain clear;
- program Flash remains within the programmed 65,024-byte region.

## 10. Prioritized remediation

### Phase A: deterministic semantic fixes (implemented)

1. Preserve current channel volume for a new note without an instrument.
2. Correct the fadeout range and output scaling to FT2's 32768 domain.
3. Add trace tests based on libxmp's FT2 event cases. (Partial; more golden traces remain.)
4. Parse active auto-vibrato and panning-envelope metadata and report explicit
   compiler warnings. (Lowering remains future work.)
5. Document instrument-only reduced behavior. (Exact lowering remains future work.)

These changes are independent of PCM sample-rate experiments and should be
committed separately.

### Phase B: PCM quality experiment (implemented and retained)

1. Remove the rejected PCM-body contour experiment.
2. Add a dependency-free, anti-aliased host resampler.
3. Change the T10M resource PCM rate from 8 kHz to 16 kHz.
4. Change the hot-path hold divider from four frames to two.
5. rebuild Funky Stars and verify memory use;
6. run the controlled board A/B described above.

The 16 kHz path was retained after board listening and runtime diagnostics. If a
future module exposes a similar symptom, capture the board PWM and compare it to
MilkyTracker/libopenmpt at aligned note boundaries before changing mixer gain.
The mixer now carries tonal gain at
7-bit precision while retaining the same full-scale headroom. This prevents quiet
XM rows (for example volume-column values near `0x14`) from disappearing during
two successive 5-bit rounding operations.

### Phase C: general XM lowering

1. Split multisample instruments by used key zones.
2. Preserve mono-fold panning gain.
3. lower automatic vibrato and supported panning envelopes to compact macros;
4. represent bounded PCM volume/rate updates or pre-render effect variants;
5. add effect-level golden traces for Amiga and linear modes;
6. add explicit compiler diagnostics for every unsupported semantic construct.

## 11. Required regression suite

The host suite should cover:

- packed/unpacked cells and 8/16-bit delta samples;
- note with/without instrument and instrument without note;
- note-off with and without an enabled volume envelope;
- fadeout timing in the 32768 domain;
- tone-portamento non-retrigger behavior;
- arpeggio and vibrato phase sequences;
- effect parameter memory;
- E90 and E9x retrigger timing;
- volume-envelope interpolation, sustain and loop boundaries;
- key-zone selection and rewriting;
- panning-to-mono gain;
- anti-aliased PCM resampling frequency response and exact duration;
- full Funky Stars event statistics and resource-size budget.

Hardware tests must always restart the same track from the beginning for each
mute condition. Mid-song serial mute switching is useful for exploration but is
not a controlled A/B because compared passages may differ.
