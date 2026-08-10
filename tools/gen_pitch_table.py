#!/usr/bin/env python3
from pathlib import Path

rate=32000
values=[]
for midi in range(129):
    hz=440.0*2.0**((midi-69)/12.0)
    values.append(min(0xffffff,round(hz*(1<<24)/rate)))
lines=["/* Generated equal-tempered note phase increments at 32 kHz. */",
       '#include "WavetableSynth.h"',"",
       "static MEM_CODE(uint32_t) pitchIncrement[129] = {"]
for start in range(0,len(values),8):
    lines.append("    "+", ".join(f"{x}UL" for x in values[start:start+8])+",")
lines += ["};","",
"uint32_t WavetablePitchToIncrement(uint16_t pitch)","{",
"    uint8_t note;", "    uint8_t fraction;", "    uint32_t base, delta;",
"    if (!pitch) return 0;", "    note = (uint8_t)(pitch >> 8);", "    fraction = (uint8_t)pitch;",
"    if (note >= 128) return pitchIncrement[128];", "    base = pitchIncrement[note];",
"    delta = pitchIncrement[note + 1] - base;", "    return base + (delta * fraction >> 8);", "}", ""]
Path("WavetableSynth/PitchTable.c").write_text("\n".join(lines),encoding="ascii")
