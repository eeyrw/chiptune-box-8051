#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from tracker10.effects import DEFAULT_MULTI_NOTE_PCM, DEFAULT_RESOURCE_POLICY, RESOURCE_POLICIES
from tracker10.format import emit_c, inspect_playlist, inspect_track, pack_playlist
from tracker10.mod import ModError, analyze_mod, compile_mod, is_mod, parse_mod
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


def load_module(path: Path):
    data = path.read_bytes()
    if data[:17] == b"Extended Module: ":
        module = parse_xm(data)
        return "xm", module, analyze_xm(module), compile_xm
    if is_mod(data):
        module = parse_mod(data)
        return "mod", module, analyze_mod(module), compile_mod
    suffix = path.suffix.lower()
    if suffix == ".xm":
        module = parse_xm(data)
        return "xm", module, analyze_xm(module), compile_xm
    if suffix == ".mod":
        module = parse_mod(data)
        return "mod", module, analyze_mod(module), compile_mod
    raise ValueError("input is neither a FastTracker II XM nor a recognized 31-sample MOD")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and compile XM/MOD into semantic T10M v4/T10P data")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser(
        "compile", help="compile one XM or MOD into a one-track T10P")
    compile_parser.add_argument("input", type=Path)
    compile_parser.add_argument("output", type=Path)
    compile_parser.add_argument("--max-orders", type=int)
    compile_parser.add_argument("--c-output", type=Path)
    compile_parser.add_argument(
        "--resource-policy", choices=sorted(RESOURCE_POLICIES),
        default=DEFAULT_RESOURCE_POLICY,
        help="wave: prefer 16-pt wavetables (default); pcm: prefer PCM one-shots")
    compile_parser.add_argument(
        "--multi-note-pcm", action="store_true", default=DEFAULT_MULTI_NOTE_PCM,
        help="split multi-note PCM instruments into per-note variants (larger)")
    inspect_parser = commands.add_parser(
        "inspect", help="analyze and compile-check an XM or MOD without writing output")
    inspect_parser.add_argument("input", type=Path)
    inspect_parser.add_argument("--max-orders", type=int)
    inspect_parser.add_argument(
        "--resource-policy", choices=sorted(RESOURCE_POLICIES),
        default=DEFAULT_RESOURCE_POLICY)
    inspect_parser.add_argument(
        "--multi-note-pcm", action="store_true", default=DEFAULT_MULTI_NOTE_PCM)
    # Backward-compatible aliases.
    inspect_xm_parser = commands.add_parser(
        "inspect-xm", help="alias for inspect on XM input")
    inspect_xm_parser.add_argument("input", type=Path)
    inspect_xm_parser.add_argument("--max-orders", type=int)
    inspect_xm_parser.add_argument(
        "--resource-policy", choices=sorted(RESOURCE_POLICIES),
        default=DEFAULT_RESOURCE_POLICY)
    inspect_xm_parser.add_argument(
        "--multi-note-pcm", action="store_true", default=DEFAULT_MULTI_NOTE_PCM)
    inspect_mod_parser = commands.add_parser(
        "inspect-mod", help="alias for inspect on MOD input")
    inspect_mod_parser.add_argument("input", type=Path)
    inspect_mod_parser.add_argument("--max-orders", type=int)
    inspect_mod_parser.add_argument(
        "--resource-policy", choices=sorted(RESOURCE_POLICIES),
        default=DEFAULT_RESOURCE_POLICY)
    inspect_mod_parser.add_argument(
        "--multi-note-pcm", action="store_true", default=DEFAULT_MULTI_NOTE_PCM)
    inspect_t10p_parser = commands.add_parser(
        "inspect-t10p", help="validate and describe a T10P image")
    inspect_t10p_parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect-t10p":
            info = inspect_playlist(args.input.read_bytes())
        else:
            fmt, module, info, compile_fn = load_module(args.input)
            if args.command == "inspect-xm" and fmt != "xm":
                raise ValueError(f"{args.input} is not an XM module")
            if args.command == "inspect-mod" and fmt != "mod":
                raise ValueError(f"{args.input} is not a MOD module")
            resource_policy = getattr(args, "resource_policy", DEFAULT_RESOURCE_POLICY)
            multi_note_pcm = getattr(args, "multi_note_pcm", DEFAULT_MULTI_NOTE_PCM)
            track, compiled = compile_fn(
                module, args.max_orders,
                resource_policy=resource_policy,
                multi_note_pcm=multi_note_pcm)
            compiled.update({f"validated_{key}": value
                             for key, value in inspect_track(track).items()})
            info["format"] = fmt
            info["compiled"] = compiled
            if args.command == "compile":
                image = pack_playlist([track])
                atomic_write(args.output, image)
                if args.c_output:
                    atomic_write(args.c_output, emit_c(image).encode("ascii"))
                info["playlist_bytes"] = len(image)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, XmError, ModError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
