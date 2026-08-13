#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from tracker10.format import emit_c, inspect_playlist, inspect_track, pack_playlist
from tracker10.xm import XmError, analyze_xm, compile_xm, parse_xm


def atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and compile FastTracker II XM into semantic T10M v4/T10P data")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser("compile", help="compile one XM into a one-track T10P")
    compile_parser.add_argument("input", type=Path)
    compile_parser.add_argument("output", type=Path)
    compile_parser.add_argument("--max-orders", type=int)
    compile_parser.add_argument("--c-output", type=Path)
    inspect_xm_parser = commands.add_parser("inspect-xm", help="analyze and compile-check an XM without writing output")
    inspect_xm_parser.add_argument("input", type=Path)
    inspect_xm_parser.add_argument("--max-orders", type=int)
    inspect_t10p_parser = commands.add_parser("inspect-t10p", help="validate and describe a T10P image")
    inspect_t10p_parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect-t10p":
            info = inspect_playlist(args.input.read_bytes())
        else:
            module = parse_xm(args.input.read_bytes())
            info = analyze_xm(module)
            track, compiled = compile_xm(module, args.max_orders)
            compiled.update({f"validated_{key}": value
                             for key, value in inspect_track(track).items()})
            info["compiled"] = compiled
            if args.command == "compile":
                image = pack_playlist([track])
                atomic_write(args.output, image)
                if args.c_output:
                    atomic_write(args.c_output, emit_c(image).encode("ascii"))
                info["playlist_bytes"] = len(image)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, XmError) as exc:
        parser.error(str(exc))


if __name__=="__main__":
    raise SystemExit(main())
