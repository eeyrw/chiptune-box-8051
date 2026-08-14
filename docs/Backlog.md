# Backlog

This file records planned work that has not yet been implemented. Items here
are not part of the current firmware or tool compatibility contract.

## Correct the Class-D PWM2 pin mapping

Status: blocked on PCB connectivity verification

The current firmware requests PWM2P/P1.2 and PWM2N/P1.3, but STC8H3K64S2 has
no P1.3 pin. Its complete PWM2 hardware-complementary mapping is P2.2/P2.3.
Keep PWM4N/P1.7 assigned to the visualization LED; channel 2 and channel 4 use
independent compare and enable registers and do not otherwise conflict.

Before changing firmware, trace or probe P2.2/P2.3 and confirm that they reach
the Class-D inputs or can be reworked safely. Then:

- select `PWM2_USE_P22P23()` and configure P2.2/P2.3 push-pull;
- initialize CCR2 to the unsigned center duty 128, or defer audio-output enable
  until the audio ring is primed;
- explicitly configure P1.7 for the PWM4N LED output;
- reject ADC reads on unavailable channels and pins reserved by active PWM;
- verify complementary duties 0, 128 and 255 and startup behavior on a scope;
- confirm that LED visualization remains independent and playback underruns do
  not grow.

The evidence, resource-conflict matrix and measurement procedure are in
[HardwareInvestigation.md](HardwareInvestigation.md).

## MOD input support

Status: planned

Goal: compile common ProTracker-compatible MOD files into the existing T10P /
T10M v4 representation. MOD remains a host-side input format; the MCU will not
parse MOD at runtime, and no new firmware playback path or format version is
required.

### Phase 1: common 31-sample MOD

- Add `tools/tracker10/mod.py` with bounds-checked parsing for the standard
  31-sample header, order table, 64-row patterns and signed 8-bit sample data.
- Recognize unambiguous channel signatures including `M.K.`, `M!K!`, `4CHN`
  and common 6/8/10-channel variants. Reject more than ten channels.
- Decode the packed four-byte MOD cell: sample number, 12-bit Amiga period,
  effect and parameter.
- Convert note periods and the signed four-bit sample finetune using the Amiga
  period model. Do not treat MOD periods as XM linear note numbers.
- Represent each MOD sample as one source instrument with its authored default
  volume, finetune, loop start and loop length.
- Reuse the existing resource policy: suitable loops become 16-point song
  wavetables; long non-looped samples become 16 kHz PCM one-shots.
- Normalize supported effects into the existing semantic cell model. The first
  compatibility set is `0xx`, `1xx`, `2xx`, `3xx`, `4xx`, `Axx`, `Bxx`, `Dxx`,
  `Fxx`, `E9x` and `ECx`.
- Preserve MOD-specific rules: `Dxx` uses decimal pattern-row digits and `Fxx`
  selects speed below `0x20` and BPM from `0x20` upward.
- Reject malformed sample ranges, invalid orders, truncated patterns, unknown
  signatures and effects without a declared approximation.

### Phase 2: practical compatibility

- Add faithful or explicitly approximate handling for effects commonly found in
  real MOD collections: `5xx`, `6xx`, `7xx`, `9xx` and remaining `Exx`
  subcommands.
- Audit effect parameter memory and tick-zero behavior against a ProTracker or
  libxmp reference trace. XM behavior must not be assumed where MOD differs.
- Report discarded stereo panning and other format reductions in the same JSON
  warning model currently used by XM.
- Batch-scan a sourced MOD collection and record exact, approximate,
  incompatible and too-large counts before claiming general MOD compatibility.

### Phase 3: legacy variants

- Evaluate unsigned-signature and less common multichannel identifiers using
  documented, deterministic detection rules.
- Add 15-sample Soundtracker MOD only after false-positive detection tests are
  available. Its lack of a channel signature makes automatic identification
  ambiguous, so it is outside the first release.

### Tool and build integration

- Refactor XM-specific parsing away from common module lowering so XM and MOD
  feed one typed intermediate representation before T10M encoding.
- Add automatic input detection by file contents. File extensions may improve
  error messages but must not be the sole format check.
- Extend `tools/tracker10_tool.py` with documented MOD inspection and compilation
  commands, while keeping existing XM commands compatible.
- Add format-neutral Make targets such as `tracker-hex` and `flash-tracker`.
  Keep `xm-hex` and `flash-xm` as compatible aliases or documented wrappers.
- Extend `tools/xm_batch.py` or replace it with a format-neutral batch command;
  do not silently classify MOD files with XM-only warning rules.
- Document every new Python command, option, exit condition, warning and example
  in `docs/PythonTools.md`, `docs/UsingXM.md` or a new format-neutral usage guide.

### Acceptance criteria

- Parser tests cover every supported signature, channel count, period boundary,
  finetune value, loop boundary, truncated section and invalid order reference.
- Semantic tests cover MOD period conversion, default sample volume, looped and
  one-shot resources, effect memory, `Dxx` decimal decoding and `Fxx` speed/BPM.
- Fixture files are either generated by repository scripts or checked in with
  source and license information.
- Fresh compilation is deterministic and emitted T10P passes the existing v4
  structure and CRC validator.
- Existing XM fixtures remain byte-for-byte stable unless a separately reviewed
  lowering fix intentionally changes them.
- At least one representative four-channel MOD passes host reference comparison,
  clean firmware link, board flash and a full active-playback diagnostic window
  with no parser error, ISR overrun or growing underrun count.
