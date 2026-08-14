# Tracker10 MOD collection

Rebuilt 2026-08-14 after the MOD resource-policy audit.

## Contents

- 49 flash-fitting MOD/T10P pairs from a 200-file Modland Protracker sample
- Authors: 4-Mat, Laxity, Dizzy, Jogeir Liljedahl, Dr. Awesome, Walkman, Tip,
  Firefox, Captain, Mahoney
- All pairs grade `approximate` because resource lowering is explicitly warned
- `MANIFEST.tsv` — per-track grade, sizes, hashes, warnings, source URL
- `SHA256SUMS` — every `.mod` and `.t10p`
- `SCAN-200.tsv` — full batch (49 fit / 150 too-large / 1 incompatible)

## Resource policy

See `tools/tracker10/mod.py` and `docs/UsingMOD.md`:

- short loops (8..64 samples) → 16-point wavetables
- longer loops and all non-looped samples → 16 kHz PCM one-shots
- PCM rate baked from each instrument's most common note

Third-party works remain under their authors' and source-archive terms.
Repository inclusion is for compatibility testing only.

## Flash one track

```bash
make flash-tracker TRACKER_INPUT="music/mod/Dizzy/fanaatti.mod"
# or
make flash-tracker TRACKER_INPUT="music/mod/4-Mat/ace ii.mod"
```

## Verify

```bash
python3 tools/xm_batch.py manifest music/mod \
  --report /tmp/mod-manifest.tsv \
  --checksums /tmp/mod-sha256sums
diff -u music/mod/SHA256SUMS /tmp/mod-sha256sums
```

Expect 49 verified, 0 invalid.
