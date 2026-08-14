import math
import struct

import pytest

from tools.tracker10.format import Cell, decode_track, inspect_playlist, pack_playlist
from tools.tracker10.mod import (ModError, analyze_mod, compile_mod, detect_mod_signature,
                                 parse_mod, period_to_note)
from tools.tracker10.reference import ReferencePlayer


def build_mod(
    *,
    title: bytes = b"TESTMOD",
    signature: bytes = b"M.K.",
    channels: int | None = None,
    song_length: int = 1,
    restart: int = 0x7F,
    orders: bytes | None = None,
    patterns: list[bytes] | None = None,
    samples: list[dict] | None = None,
) -> bytes:
    if channels is None:
        detected = {
            b"M.K.": 4, b"M!K!": 4, b"4CHN": 4, b"6CHN": 6, b"8CHN": 8, b"10CH": 10,
        }.get(signature)
        if detected is None and signature.endswith(b"CHN") and signature[:1].isdigit():
            detected = signature[0] - 48
        if detected is None and signature.endswith(b"CH") and signature[:2].isdigit():
            detected = int(signature[:2])
        channels = detected or 4
    if orders is None:
        orders = bytes([0]) + bytes(127)
    if samples is None:
        # One looped square-ish sample used by the default note cell.
        wave = bytes([20, 20, 20, 20, 236, 236, 236, 236] * 2)  # 16 bytes signed via 2s
        samples = [{
            "name": b"lead",
            "length_words": 8,
            "finetune": 0,
            "volume": 64,
            "loop_start_words": 0,
            "loop_length_words": 8,
            "data": wave,
        }]
    while len(samples) < 31:
        samples.append({
            "name": b"",
            "length_words": 0,
            "finetune": 0,
            "volume": 0,
            "loop_start_words": 0,
            "loop_length_words": 1,
            "data": b"",
        })
    if patterns is None:
        # Row 0 channel 0: period 428 (C-2/PT -> note 61), sample 1, no effect.
        cell = bytes([0x01, 0xAC, 0x10, 0x00])  # period 0x1AC=428, sample 1
        row = cell + bytes(4 * (channels - 1))
        empty = bytes(4 * channels)
        pattern = row + empty * 63
        patterns = [pattern]

    out = bytearray(20)
    out[:len(title)] = title[:20]
    for sample in samples:
        block = bytearray(30)
        block[:len(sample["name"])] = sample["name"][:22]
        struct.pack_into(">H", block, 22, sample["length_words"])
        block[24] = sample["finetune"] & 0x0F
        block[25] = sample["volume"]
        struct.pack_into(">H", block, 26, sample["loop_start_words"])
        struct.pack_into(">H", block, 28, sample["loop_length_words"])
        out += block
    out.append(song_length)
    out.append(restart)
    out += orders[:128].ljust(128, b"\0")
    out += signature
    for pattern in patterns:
        assert len(pattern) == 64 * channels * 4
        out += pattern
    for sample in samples:
        out += sample["data"]
    return bytes(out)


def test_period_to_note_matches_libxmp_base():
    assert period_to_note(0) == 0
    assert period_to_note(856) == 49
    assert period_to_note(428) == 61
    assert period_to_note(214) == 73
    with pytest.raises(ModError):
        period_to_note(1)


def test_detect_signatures():
    for signature, channels in ((b"M.K.", 4), (b"M!K!", 4), (b"6CHN", 6),
                                (b"8CHN", 8), (b"10CH", 10)):
        data = build_mod(signature=signature)
        assert detect_mod_signature(data) == (signature, channels)


def test_parse_and_compile_minimal_mod_is_deterministic():
    data = build_mod()
    module = parse_mod(data)
    assert module.channels == 4
    assert module.patterns[0][0][0].note == 61
    assert module.patterns[0][0][0].instrument == 1
    assert module.samples[0].loop_type == 1
    track_a, info = compile_mod(module)
    track_b, _ = compile_mod(parse_mod(data))
    assert track_a == track_b
    image = pack_playlist([track_a])
    inspect_playlist(image)
    decoded = decode_track(track_a)
    assert decoded.amiga_effects is True
    assert decoded.instruments[0].gain == 31
    assert info["waves"] == 1
    assert info["pcm_samples"] == 0


def test_default_volume_is_lowered_into_cells():
    data = build_mod(samples=[{
        "name": b"quiet",
        "length_words": 8,
        "finetune": 0,
        "volume": 32,
        "loop_start_words": 0,
        "loop_length_words": 8,
        "data": bytes([20, 20, 20, 20, 236, 236, 236, 236] * 2),
    }])
    decoded = decode_track(compile_mod(parse_mod(data))[0])
    assert decoded.patterns[0][0][0].volume == 33
    assert decoded.instruments[0].gain == 31


def test_set_volume_and_speed_effects():
    channels = 4
    # note + C20 set volume 32, then F06 speed
    cell0 = bytes([0x01, 0xAC, 0x1C, 0x20])  # sample 1, period 428, C20
    cell1 = bytes([0x00, 0x00, 0x0F, 0x06])
    row0 = cell0 + bytes(4 * (channels - 1))
    row1 = cell1 + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row0 + row1 + empty * 62
    module = parse_mod(build_mod(patterns=[pattern]))
    decoded = decode_track(compile_mod(module)[0])
    assert decoded.patterns[0][0][0].volume == 33  # 32 + presence
    assert decoded.patterns[0][0][0].effect == 0
    assert decoded.patterns[0][1][0].effect == 0x0F
    assert decoded.patterns[0][1][0].parameter == 6


def test_pattern_break_is_decimal():
    channels = 4
    cell = bytes([0x00, 0x00, 0x0D, 0x20])  # D20 -> row 20
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(patterns=[pattern]))
    decoded = decode_track(compile_mod(module)[0])
    assert decoded.patterns[0][0][0].effect == 0x0D
    assert decoded.patterns[0][0][0].parameter == 0x20


def test_pattern_loop_and_delay_are_accepted():
    channels = 4

    def row_effect(param: int) -> bytes:
        cell = bytes([0x00, 0x00, 0x0E, param])
        return cell + bytes(4 * (channels - 1))

    empty = bytes(4 * channels)
    pattern = row_effect(0x60) + row_effect(0x61) + row_effect(0xE1) + empty * 61
    module = parse_mod(build_mod(patterns=[pattern]))
    track, _ = compile_mod(module)
    decoded = decode_track(track)
    assert decoded.patterns[0][0][0] == Cell(effect=0x0E, parameter=0x60)
    assert decoded.patterns[0][1][0] == Cell(effect=0x0E, parameter=0x61)
    assert decoded.patterns[0][2][0] == Cell(effect=0x0E, parameter=0xE1)


def test_unsupported_effect_is_rejected_with_location():
    channels = 4
    cell = bytes([0x00, 0x00, 0x0B, 0x7F])  # B7F exceeds one-order song
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(patterns=[pattern]))
    with pytest.raises(ModError, match=r"B7F"):
        compile_mod(module)


def test_more_than_ten_channels_rejected():
    data = build_mod(signature=b"12CH")
    with pytest.raises(ModError, match="12 channels"):
        parse_mod(data)


def test_analyze_reports_panning_warning():
    channels = 4
    cell = bytes([0x00, 0x00, 0x08, 0x80])
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(patterns=[pattern]))
    warnings = analyze_mod(module)["warnings"]
    assert any("panning" in item for item in warnings)
    track, _ = compile_mod(module)
    assert decode_track(track).patterns[0][0][0].effect == 0


def test_long_non_looped_sample_becomes_pcm():
    pcm_data = bytes([10, 246] * 200)  # 400 bytes > 256
    samples = [{
        "name": b"kick",
        "length_words": 200,
        "finetune": 0,
        "volume": 64,
        "loop_start_words": 0,
        "loop_length_words": 1,
        "data": pcm_data,
    }]
    cell = bytes([0x01, 0xAC, 0x10, 0x00])
    channels = 4
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(samples=samples, patterns=[pattern]))
    track, info = compile_mod(module)
    assert info["pcm_samples"] == 1
    assert info["pcm_bytes"] > 0
    decoded = decode_track(track)
    assert decoded.instruments[0].mode >= 0x80


def test_reference_player_accepts_compiled_mod():
    module = parse_mod(build_mod())
    track, _ = compile_mod(module)
    song = decode_track(track)
    frame = ReferencePlayer(song).step()
    assert frame.voices[0].volume > 0


def test_short_non_looped_sample_becomes_pcm():
    samples = [{
        "name": b"hit",
        "length_words": 50,
        "finetune": 0,
        "volume": 64,
        "loop_start_words": 0,
        "loop_length_words": 1,
        "data": bytes([20, 236] * 50),
    }]
    cell = bytes([0x01, 0xAC, 0x10, 0x00])
    channels = 4
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    track, info = compile_mod(parse_mod(build_mod(samples=samples, patterns=[pattern])))
    assert info["pcm_samples"] == 1
    assert info["waves"] == 1  # default placeholder wave still present when only PCM
    decoded = decode_track(track)
    assert decoded.instruments[0].mode >= 0x80


def test_long_loop_is_pcm_not_infinite_wave():
    # 100-word loop (>64 samples*2? loop_length_words=100 -> 200 bytes > 64)
    samples = [{
        "name": b"drumloop",
        "length_words": 100,
        "finetune": 0,
        "volume": 64,
        "loop_start_words": 0,
        "loop_length_words": 100,
        "data": bytes([10, 246] * 100),
    }]
    cell = bytes([0x01, 0xAC, 0x10, 0x00])
    channels = 4
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    track, info = compile_mod(parse_mod(build_mod(samples=samples, patterns=[pattern])))
    assert info["pcm_samples"] == 1
    assert decode_track(track).instruments[0].mode >= 0x80


def test_resource_warnings_describe_lowering():
    # Non-looped short sample -> PCM one-shot warning.
    samples = [{
        "name": b"hit",
        "length_words": 40,
        "finetune": 0,
        "volume": 64,
        "loop_start_words": 0,
        "loop_length_words": 1,
        "data": bytes([12, 244] * 40),
    }]
    cell = bytes([0x01, 0xAC, 0x10, 0x00])
    channels = 4
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(samples=samples, patterns=[pattern]))
    warnings = analyze_mod(module)["warnings"]
    assert any("non-looped samples become 16 kHz PCM" in item for item in warnings)
    track, info = compile_mod(module)
    assert info["pcm_samples"] == 1


def test_long_loop_warning_and_pcm_path():
    samples = [{
        "name": b"loop",
        "length_words": 80,
        "finetune": 0,
        "volume": 64,
        "loop_start_words": 0,
        "loop_length_words": 80,
        "data": bytes([8, 248] * 80),
    }]
    cell = bytes([0x01, 0xAC, 0x10, 0x00])
    channels = 4
    row = cell + bytes(4 * (channels - 1))
    empty = bytes(4 * channels)
    pattern = row + empty * 63
    module = parse_mod(build_mod(samples=samples, patterns=[pattern]))
    warnings = analyze_mod(module)["warnings"]
    assert any("long sample loops become one-shot PCM" in item for item in warnings)
    assert compile_mod(module)[1]["pcm_samples"] == 1
