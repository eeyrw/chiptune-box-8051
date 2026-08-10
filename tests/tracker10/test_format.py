import struct

import pytest

from tools.tracker10.format import Cell, Instrument, Song, decode_track, encode_track, inspect_track, pack_playlist
from tools.tracker10.reference import ReferencePlayer
from tools.tracker10.xm import XmError, compile_xm, parse_xm


def simple_song() -> Song:
    empty = (Cell(),) * 10
    first = list(empty)
    first[0] = Cell(note=49, instrument=1, volume=65, effect=4, parameter=0x47)
    second = list(empty)
    second[0] = Cell(effect=4, parameter=0)
    pattern = (tuple(first), tuple(second), empty)
    instrument = Instrument(mode=1, gain=24, relative_pitch=-32,
                            volume_macro=(32, 20, 0), volume_loop=0xFF,
                            pitch_macro=(8, 0), pitch_loop=0xFF)
    return Song((0, 0, 0), 1, 3, 125, (pattern,), (instrument,))


def test_semantic_track_playlist_and_crc():
    track = encode_track(simple_song())
    image = pack_playlist([track])
    info = inspect_track(track)
    assert track[:6] == b"T10M\x02\x0a"
    assert image[:5] == b"T10P\x02"
    assert info == {"orders": 3, "patterns": 1, "instruments": 1,
                    "cells": 2, "nonempty_rows": 2, "track_bytes": len(track)}
    assert decode_track(track) == simple_song()


def test_order_reuse_does_not_duplicate_pattern_data():
    one = encode_track(simple_song())
    song = simple_song()
    many = encode_track(Song(song.orders * 20, song.restart, song.speed, song.bpm,
                             song.patterns, song.instruments))
    assert len(many) - len(one) == len(song.orders) * 19


def test_crc_rejects_corruption():
    track = bytearray(encode_track(simple_song()))
    track[-1] ^= 0x40
    with pytest.raises(ValueError, match="CRC"):
        inspect_track(bytes(track))


def test_macro_validation():
    song = simple_song()
    bad = Instrument(volume_macro=(33,))
    with pytest.raises(ValueError, match="volume macro"):
        encode_track(Song(song.orders, song.restart, song.speed, song.bpm,
                          song.patterns, (bad,)))


def minimal_xm(effect: int = 0, parameter: int = 0) -> bytes:
    header = bytearray(336)
    header[:17] = b"Extended Module: "
    header[17:21] = b"TEST"
    header[37] = 0x1A
    header[38:45] = b"pytest\0"
    struct.pack_into("<H", header, 58, 0x0104)
    struct.pack_into("<I8H", header, 60, 276, 1, 0, 1, 1, 1, 0, 3, 125)
    header[80] = 0

    pattern = bytearray(14)
    struct.pack_into("<IBHH", pattern, 0, 9, 0, 1, 5)
    pattern[9:14] = bytes((49, 1, 0x50, effect, parameter))

    instrument = bytearray(263)
    struct.pack_into("<I", instrument, 0, 263)
    instrument[4:8] = b"INST"
    struct.pack_into("<H", instrument, 27, 1)
    struct.pack_into("<I", instrument, 29, 40)
    instrument[33:129] = bytes(96)

    sample_header = bytearray(40)
    struct.pack_into("<III", sample_header, 0, 16, 0, 16)
    sample_header[12] = 48
    sample_header[13] = 0x40
    sample_header[14] = 1
    sample_header[16] = 12
    sample = bytes((20, 20, 20, 20, 236, 236, 236, 236,
                    20, 20, 20, 20, 236, 236, 236, 236))
    return bytes(header + pattern + instrument + sample_header + sample)


def test_xm_instrument_and_pattern_are_preserved_semantically():
    module = parse_xm(minimal_xm(4, 0x47))
    track, info = compile_xm(module)
    assert len(module.instruments) == 1
    assert module.instruments[0].samples[0].loop_length == 16
    assert module.instruments[0].samples[0].finetune == 64
    assert module.instruments[0].samples[0].relative_note == 12
    assert decode_track(track).instruments[0].relative_pitch == 0
    assert info["orders"] == 1
    assert info["patterns"] == 1
    assert inspect_track(track)["cells"] == 1


def test_unsupported_effect_fails_instead_of_sounding_wrong():
    module = parse_xm(minimal_xm(5, 1))
    with pytest.raises(XmError, match="unsupported effect"):
        compile_xm(module)


def test_reference_vm_keeps_vibrato_parameter_memory():
    player = ReferencePlayer(decode_track(encode_track(simple_song())))
    frames = [player.step() for _ in range(6)]
    assert [x.tick for x in frames] == [0, 1, 2, 0, 1, 2]
    assert frames[0].voices[0].reset
    assert not frames[3].voices[0].reset
    assert frames[4].voices[0].pitch != frames[3].voices[0].pitch
    assert sum(x.wait_samples for x in frames) == 3840
