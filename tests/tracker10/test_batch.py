from pathlib import Path

import pytest

from tools.xm_batch import firmware_overhead, parse_assignments, resolve_source


def test_firmware_overhead_uses_generated_score_size(tmp_path: Path):
    binary = tmp_path / "firmware.bin"
    score = tmp_path / "scoreList.c"
    binary.write_bytes(bytes(120))
    score.write_text("const uint32_t ScoreSize = 20UL;\n", encoding="ascii")
    assert firmware_overhead(binary, score) == 100


def test_source_resolution_quotes_each_path_component():
    roots = {"Dalezy": "https://example.invalid/Dalezy/"}
    assert resolve_source(Path("Dalezy/a song #1.xm"), {}, roots) == (
        "https://example.invalid/Dalezy/a%20song%20%231.xm")
    exact = {"Dalezy/a song #1.xm": "https://archive.invalid/42"}
    assert resolve_source(Path("Dalezy/a song #1.xm"), exact, roots) == (
        "https://archive.invalid/42")


def test_assignment_requires_explicit_url_mapping():
    with pytest.raises(ValueError, match="PATH=URL"):
        parse_assignments(["Dalezy"], "--source-root")
