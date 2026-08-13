#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

try:
    from tracker10.format import inspect_playlist, pack_playlist
    from tracker10.xm import XmError, analyze_xm, compile_xm, parse_xm
except ModuleNotFoundError:  # Imported as tools.xm_batch by the test suite.
    from tools.tracker10.format import inspect_playlist, pack_playlist
    from tools.tracker10.xm import XmError, analyze_xm, compile_xm, parse_xm


FIELDS = ("status", "grade", "xm_bytes", "t10p_bytes", "firmware_bytes",
          "xm_sha256", "t10p_sha256", "warnings", "source_url", "path", "error")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def firmware_overhead(binary: Path, score_c: Path) -> int:
    match = re.search(rb"\bScoreSize\s*=\s*(\d+)UL", score_c.read_bytes())
    if match is None:
        raise ValueError(f"cannot find ScoreSize in {score_c}")
    overhead = binary.stat().st_size - int(match.group(1))
    if overhead < 0:
        raise ValueError("firmware binary is smaller than its embedded score")
    return overhead


def parse_assignments(values: list[str], option: str) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{option} expects PATH=URL")
        path, url = value.split("=", 1)
        result[Path(path).as_posix().strip("/")] = url
    return result


def resolve_source(relative: Path, exact: dict[str, str], roots: dict[str, str]) -> str:
    key = relative.as_posix()
    if key in exact:
        return exact[key]
    matches = [(prefix, url) for prefix, url in roots.items()
               if key == prefix or key.startswith(prefix + "/")]
    if not matches:
        return ""
    prefix, url = max(matches, key=lambda item: len(item[0]))
    suffix = key[len(prefix):].lstrip("/")
    return url.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in suffix.split("/"))


def compile_one(path: Path) -> tuple[bytes, dict]:
    module = parse_xm(path.read_bytes())
    info = analyze_xm(module)
    track, compiled = compile_xm(module)
    image = pack_playlist([track])
    inspect_playlist(image)
    info["compiled"] = compiled
    return image, info


def write_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as output:
        temporary = Path(output.name)
        writer = csv.DictWriter(output, FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def scan(args: argparse.Namespace) -> int:
    overhead = (args.firmware_overhead if args.firmware_overhead is not None
                else firmware_overhead(args.firmware_bin, args.score_c))
    exact = parse_assignments(args.source, "--source")
    roots = parse_assignments(args.source_root, "--source-root")
    rows = []
    for xm in sorted(path for path in args.input.rglob("*") if path.is_file()
                     and path.suffix.lower() == ".xm"):
        relative = xm.relative_to(args.input)
        row: dict[str, object] = {field: "" for field in FIELDS}
        row.update(path=relative.as_posix(), xm_bytes=xm.stat().st_size,
                   xm_sha256=sha256(xm.read_bytes()),
                   source_url=resolve_source(relative, exact, roots))
        try:
            image, info = compile_one(xm)
            warnings = "; ".join(info["warnings"])
            total = overhead + len(image)
            row.update(status="fit" if total <= args.flash_limit else "too-large",
                       grade="exact" if not warnings else "approximate",
                       t10p_bytes=len(image), firmware_bytes=total,
                       t10p_sha256=sha256(image), warnings=warnings)
            if args.output is not None:
                output = args.output / relative.with_suffix(".t10p")
                atomic_write(output, image)
            if args.collect is not None and total <= args.flash_limit:
                target = args.collect / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(xm, target)
                atomic_write(target.with_suffix(".t10p"), image)
        except (OSError, ValueError, XmError) as exc:
            row.update(status="incompatible", error=str(exc))
        rows.append(row)
    write_tsv(args.report, rows)
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("fit", "too-large", "incompatible")}
    print(json.dumps({"files": len(rows), "firmware_overhead": overhead,
                      "flash_limit": args.flash_limit, **counts}, indent=2))
    return 0


def manifest(args: argparse.Namespace) -> int:
    exact = parse_assignments(args.source, "--source")
    roots = parse_assignments(args.source_root, "--source-root")
    rows = []
    failed = False
    for xm in sorted(path for path in args.collection.rglob("*") if path.is_file()
                     and path.suffix.lower() == ".xm"):
        relative = xm.relative_to(args.collection)
        t10p = xm.with_suffix(".t10p")
        row: dict[str, object] = {field: "" for field in FIELDS}
        row.update(path=relative.as_posix(), xm_bytes=xm.stat().st_size,
                   xm_sha256=sha256(xm.read_bytes()),
                   source_url=resolve_source(relative, exact, roots))
        try:
            if not t10p.is_file():
                raise ValueError("matching .t10p is missing")
            stored = t10p.read_bytes()
            inspect_playlist(stored)
            rebuilt, info = compile_one(xm)
            if rebuilt != stored:
                raise ValueError("stored T10P does not match a fresh compile")
            warnings = "; ".join(info["warnings"])
            row.update(status="verified", grade="exact" if not warnings else "approximate",
                       t10p_bytes=len(stored), t10p_sha256=sha256(stored), warnings=warnings)
        except (OSError, ValueError, XmError) as exc:
            failed = True
            row.update(status="invalid", error=str(exc))
        rows.append(row)
    write_tsv(args.report, rows)
    checksum_lines = []
    for path in sorted(p for p in args.collection.rglob("*") if p.is_file()
                       and p.suffix.lower() in (".xm", ".t10p")):
        checksum_lines.append(f"{sha256(path.read_bytes())}  {path.relative_to(args.collection).as_posix()}\n")
    atomic_write(args.checksums, "".join(checksum_lines).encode("utf-8"))
    print(json.dumps({"files": len(rows), "verified": sum(r["status"] == "verified" for r in rows),
                      "invalid": sum(r["status"] == "invalid" for r in rows)}, indent=2))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-scan and verify Tracker10 XM collections")
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="recursively compile and classify XM files")
    scan_parser.add_argument("input", type=Path)
    scan_parser.add_argument("--report", type=Path, required=True)
    scan_parser.add_argument("--output", type=Path, help="write every compatible T10P here")
    scan_parser.add_argument("--collect", type=Path, help="copy only flash-fitting XM/T10P pairs here")
    scan_parser.add_argument("--flash-limit", type=int, default=65024)
    scan_parser.add_argument("--firmware-overhead", type=int)
    scan_parser.add_argument("--firmware-bin", type=Path, default=Path("music-box-8051.bin"))
    scan_parser.add_argument("--score-c", type=Path, default=Path("scoreList.c"))
    for command in (scan_parser,):
        command.add_argument("--source", action="append", default=[], metavar="PATH=URL")
        command.add_argument("--source-root", action="append", default=[], metavar="PATH=BASE_URL")
    scan_parser.set_defaults(run=scan)

    manifest_parser = commands.add_parser("manifest", help="verify XM/T10P pairs and write hashes")
    manifest_parser.add_argument("collection", type=Path)
    manifest_parser.add_argument("--report", type=Path, required=True)
    manifest_parser.add_argument("--checksums", type=Path, required=True)
    manifest_parser.add_argument("--source", action="append", default=[], metavar="PATH=URL")
    manifest_parser.add_argument("--source-root", action="append", default=[], metavar="PATH=BASE_URL")
    manifest_parser.set_defaults(run=manifest)
    args = parser.parse_args()
    try:
        return args.run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
