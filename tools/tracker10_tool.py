#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracker10.format import emit_c, inspect_track, pack_playlist
from tracker10.xm import compile_xm, parse_xm


def main() -> int:
    p=argparse.ArgumentParser(description="Compile FastTracker II XM into compact T10M/T10P data")
    p.add_argument("command",choices=("compile",))
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--max-orders",type=int)
    p.add_argument("--c-output",type=Path)
    a=p.parse_args()
    module=parse_xm(a.input.read_bytes())
    track,info=compile_xm(module,a.max_orders)
    info.update({f"validated_{key}":value for key,value in inspect_track(track).items()})
    image=pack_playlist([track])
    a.output.write_bytes(image)
    if a.c_output:
        a.c_output.write_text(emit_c(image),encoding="ascii")
    info["playlist_bytes"]=len(image)
    print(json.dumps(info,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
