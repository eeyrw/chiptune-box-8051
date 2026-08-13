#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from tracker10.format import (ENV_ENABLED, MODE_NOISE_LONG, Cell, Instrument, Song, emit_c,
                              encode_track, pack_playlist)


notes = (37, 41, 44, 49, 44, 41, 39, 44)
rows = []
for index, note in enumerate(notes):
    row = [Cell() for _ in range(10)]
    row[index & 3] = Cell(note=note, instrument=(index & 3) + 1, volume=57)
    row[5] = Cell(note=note - 12, instrument=5, volume=53)
    if index & 1 == 0:
        row[6] = Cell(note=49, instrument=6, volume=65)
    rows.append(tuple(row))

instruments = (
    Instrument(0, 18), Instrument(1, 18), Instrument(3, 18), Instrument(4, 16),
    Instrument(3, 18),
    Instrument(MODE_NOISE_LONG, 24, volume_macro=(32, 24, 14, 7, 2, 0),
               volume_flags=ENV_ENABLED),
)
waves = (
    (96,) * 8 + (-96,) * 8,
    (112,) * 4 + (-48,) * 12,
    (120,) * 2 + (-24,) * 14,
    (-112, -96, -80, -64, -48, -32, -16, 0, 16, 32, 48, 64, 80, 96, 112, 0),
    (-120, -104, -88, -72, -56, -40, -24, -8, 8, 24, 40, 56, 72, 88, 104, 120),
)
song = Song((0,), 0, 6, 125, (tuple(rows),), instruments, waves=waves)
image = pack_playlist([encode_track(song)])
Path("scoreList.c").write_text(emit_c(image), encoding="ascii")
print(f"generated {len(image)} bytes")
