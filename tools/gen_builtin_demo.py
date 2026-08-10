#!/usr/bin/env python3
from pathlib import Path
import math
import sys

sys.path.insert(0,str(Path(__file__).parent))
from tracker10.format import Frame, Voice, RATE, emit_c, encode_track, pack_playlist

notes=(48,52,55,60,55,52,50,55)
frames=[]
state=[Voice() for _ in range(10)]
sample=0
for step,note in enumerate(notes*4):
    midi=note+11
    inc=round(440.0*2**((midi-69)/12)*(1<<24)/RATE)
    state[step%4]=Voice(inc,12,step%6)
    bass=round(440.0*2**(((note-24+11)-69)/12)*(1<<24)/RATE)
    state[5]=Voice(bass,10,3)
    state[6]=Voice(0x56000+(step&3)*0x10000,10 if step%2==0 else 0,7)
    frames.append(Frame(sample,tuple(state),(1<<(step%4))|(1<<5)|(1<<6)))
    sample+=RATE//8
track=encode_track(frames,sample,0)
image=pack_playlist([track])
Path("scoreList.c").write_text(emit_c(image),encoding="ascii")
print(f"generated {len(image)} bytes")
