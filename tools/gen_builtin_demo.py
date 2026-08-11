#!/usr/bin/env python3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from tracker10.format import (ENV_ENABLED, Cell, Instrument, Song, emit_c,
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
    Instrument(6, 24, volume_macro=(32, 24, 14, 7, 2, 0),
               volume_flags=ENV_ENABLED),
)
song = Song((0,), 0, 6, 125, (tuple(rows),), instruments)
image = pack_playlist([encode_track(song)])
Path("scoreList.c").write_text(emit_c(image), encoding="ascii")
print(f"generated {len(image)} bytes")
