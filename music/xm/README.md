# Tracker10 XM compatible collection

Collected and verified on 2026-08-13. The directory contains 147 complete XM/T10P
pairs: 13 compile without warnings (`exact`), while 134 use one or more explicitly
reported approximations (`approximate`). Every stored T10P matches a fresh compile
byte for byte and passes T10P v4 structure/CRC validation.

`MANIFEST.tsv` is authoritative. It records grade, sizes, SHA-256, warnings, source
URL and relative path for every pair. `SHA256SUMS` covers all XM and T10P files.

Sources are the Modland FastTracker 2 mirror and explicitly identified Mod Archive
module downloads. These are third-party works. Repository inclusion documents
compatibility and provenance; it does not claim that the compositions are public
domain or grant rights beyond those provided by their authors and source archives.

The 390-file Cerror/Dalezy/Dubmood scan produced 143 flash-fitting tracks, nine
semantically compatible but oversized tracks, and 238 incompatible tracks. Four
additional accepted files are JAM / Simple Chip Tune, Mod Archive / Super Mario 2,
Traven / Tetris GB high scores, and Beethoven / Fur Elise. Tracks fit individually,
not together in one internal-Flash playlist.

To install one track without replacing the repository's built-in `scoreList.c`:

```bash
make flash-xm TRACKER_INPUT="music/xm/Cerror/matrix (n-gen#06).xm"
```

Verify every stored T10P against a fresh compile and regenerate checksums:

```bash
python3 tools/xm_batch.py manifest music/xm \
  --report /tmp/tracker10-manifest.tsv \
  --checksums /tmp/tracker10-sha256sums
diff -u music/xm/SHA256SUMS /tmp/tracker10-sha256sums
(cd music/xm && sha256sum -c SHA256SUMS)
```

The report must show 147 verified and zero invalid files. Its `source_url` column
is empty unless the source mappings documented in `docs/XMCollection.md` are also
supplied; the checked-in manifest preserves the original mappings. See
`docs/PythonTools.md` for all options and side effects.
