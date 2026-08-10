from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

RATE = 32000
VOICES = 10
TRACK_HEADER_SIZE = 48
INSTRUMENT_SIZE = 40
PATTERN_DIR_SIZE = 8

CELL_NOTE = 0x01
CELL_INSTRUMENT = 0x02
CELL_VOLUME = 0x04
CELL_EFFECT = 0x08


@dataclass(frozen=True)
class Cell:
    note: int = 0
    instrument: int = 0
    volume: int = 0
    effect: int = 0
    parameter: int = 0


@dataclass(frozen=True)
class Instrument:
    mode: int = 0
    gain: int = 20
    relative_pitch: int = 0
    volume_macro: tuple[int, ...] = (32,)
    volume_loop: int = 0
    pitch_macro: tuple[int, ...] = (0,)
    pitch_loop: int = 0


@dataclass(frozen=True)
class Song:
    orders: tuple[int, ...]
    restart: int
    speed: int
    bpm: int
    patterns: tuple[tuple[tuple[Cell, ...], ...], ...]
    instruments: tuple[Instrument, ...]


def _encode_cell(cell: Cell) -> bytes:
    mask = 0
    out = bytearray()
    if cell.note:
        if not 1 <= cell.note <= 97:
            raise ValueError("note is outside XM range")
        mask |= CELL_NOTE
        out.append(cell.note)
    if cell.instrument:
        if not 1 <= cell.instrument <= 255:
            raise ValueError("instrument is outside T10 range")
        mask |= CELL_INSTRUMENT
        out.append(cell.instrument)
    if cell.volume:
        if not 1 <= cell.volume <= 65:
            raise ValueError("volume is outside normalized XM range")
        mask |= CELL_VOLUME
        out.append(cell.volume - 1)
    if cell.effect or cell.parameter:
        mask |= CELL_EFFECT
        out.extend((cell.effect, cell.parameter))
    return bytes((mask,)) + out


def _encode_pattern(pattern: tuple[tuple[Cell, ...], ...]) -> bytes:
    if not 1 <= len(pattern) <= 256:
        raise ValueError("pattern row count is outside T10 range")
    out = bytearray()
    for row in pattern:
        if len(row) != VOICES:
            raise ValueError("normalized pattern must have ten channels")
        encoded: list[tuple[int, bytes]] = []
        changed = 0
        for channel, cell in enumerate(row):
            token = _encode_cell(cell)
            if token != b"\x00":
                changed |= 1 << channel
                encoded.append((channel, token))
        out += struct.pack("<H", changed)
        for _, token in encoded:
            out += token
    return bytes(out)


def _encode_instrument(instrument: Instrument) -> bytes:
    if not 0 <= instrument.mode < 8 or not 0 <= instrument.gain <= 31:
        raise ValueError("invalid instrument mode or gain")
    volume = tuple(instrument.volume_macro)
    pitch = tuple(instrument.pitch_macro)
    if not 1 <= len(volume) <= 16 or not 1 <= len(pitch) <= 16:
        raise ValueError("instrument macro exceeds 16 steps")
    if any(not 0 <= x <= 32 for x in volume):
        raise ValueError("volume macro value is outside 0..32")
    if any(not -128 <= x <= 127 for x in pitch):
        raise ValueError("pitch macro value is outside signed byte range")
    if instrument.volume_loop != 0xFF and not 0 <= instrument.volume_loop < len(volume):
        raise ValueError("invalid volume macro loop")
    if instrument.pitch_loop != 0xFF and not 0 <= instrument.pitch_loop < len(pitch):
        raise ValueError("invalid pitch macro loop")
    out = bytearray(INSTRUMENT_SIZE)
    struct.pack_into("<BBhBBBB", out, 0, instrument.mode, instrument.gain,
                     instrument.relative_pitch, len(volume), instrument.volume_loop,
                     len(pitch), instrument.pitch_loop)
    out[8:8 + len(volume)] = bytes(volume)
    out[24:24 + len(pitch)] = bytes(x & 0xFF for x in pitch)
    return bytes(out)


def encode_track(song: Song, loop: bool = True) -> bytes:
    if not song.orders or len(song.orders) > 256:
        raise ValueError("invalid order count")
    if not song.patterns or len(song.patterns) > 255:
        raise ValueError("invalid pattern count")
    if not song.instruments or len(song.instruments) > 255:
        raise ValueError("invalid instrument count")
    if any(x >= len(song.patterns) for x in song.orders):
        raise ValueError("order references an unknown pattern")
    if not 0 <= song.restart < len(song.orders) or not 1 <= song.speed <= 255:
        raise ValueError("invalid restart or speed")
    if not 32 <= song.bpm <= 999:
        raise ValueError("invalid BPM")

    encoded_patterns = [_encode_pattern(pattern) for pattern in song.patterns]
    orders_offset = TRACK_HEADER_SIZE
    pattern_dir_offset = orders_offset + len(song.orders)
    instrument_dir_offset = pattern_dir_offset + len(song.patterns) * PATTERN_DIR_SIZE
    data_offset = instrument_dir_offset + len(song.instruments) * INSTRUMENT_SIZE

    pattern_dir = bytearray()
    pattern_data = bytearray()
    cursor = data_offset
    for pattern, encoded in zip(song.patterns, encoded_patterns):
        pattern_dir += struct.pack("<IHH", cursor, len(encoded), len(pattern))
        pattern_data += encoded
        cursor += len(encoded)

    instrument_data = b"".join(_encode_instrument(x) for x in song.instruments)
    body = (bytes(song.orders) + bytes(pattern_dir) + instrument_data +
            bytes(pattern_data))
    total_size = TRACK_HEADER_SIZE + len(body)
    header = struct.pack(
        "<4sBBBBI IHH IHH III HH I",
        b"T10M", 2, VOICES, 1 if loop else 0, 0, RATE,
        orders_offset, len(song.orders), song.restart,
        pattern_dir_offset, len(song.patterns), len(song.instruments),
        instrument_dir_offset, total_size, zlib.crc32(body) & 0xFFFFFFFF,
        song.speed, song.bpm, 0,
    )
    if len(header) != TRACK_HEADER_SIZE:
        raise AssertionError("T10M header layout changed")
    return header + body


def pack_playlist(tracks: list[bytes]) -> bytes:
    if not tracks:
        raise ValueError("playlist is empty")
    offset = 16 + 8 * len(tracks)
    entries = bytearray()
    for track in tracks:
        entries += struct.pack("<II", offset, len(track))
        offset += len(track)
    return struct.pack("<4sBBHII", b"T10P", 2, 0, len(tracks), offset, 0) + entries + b"".join(tracks)


def emit_c(image: bytes) -> str:
    lines = ["/* Generated T10P v2 semantic tracker image. */", "#include <stdint.h>", "",
             f"__code const unsigned char Score[{len(image)}] = {{"]
    for start in range(0, len(image), 12):
        lines.append("    " + ", ".join(f"0x{x:02X}" for x in image[start:start + 12]) + ",")
    lines += ["};", f"__code const uint32_t ScoreSize = {len(image)}UL;", ""]
    return "\n".join(lines)


def inspect_track(track: bytes) -> dict[str, int]:
    if len(track) < TRACK_HEADER_SIZE or track[:4] != b"T10M" or track[4] != 2 or track[5] != VOICES:
        raise ValueError("invalid T10M v2 header")
    order_offset, order_count, restart = struct.unpack_from("<IHH", track, 12)
    pattern_dir_offset, pattern_count, instrument_count = struct.unpack_from("<IHH", track, 20)
    instrument_offset, total_size, expected_crc = struct.unpack_from("<III", track, 28)
    speed, bpm = struct.unpack_from("<HH", track, 40)
    if total_size != len(track) or zlib.crc32(track[TRACK_HEADER_SIZE:]) & 0xFFFFFFFF != expected_crc:
        raise ValueError("track size or CRC mismatch")
    if not order_count or order_offset + order_count > len(track) or restart >= order_count:
        raise ValueError("invalid order table")
    if not pattern_count or pattern_dir_offset + pattern_count * PATTERN_DIR_SIZE > len(track):
        raise ValueError("invalid pattern directory")
    if instrument_offset + instrument_count * INSTRUMENT_SIZE > len(track):
        raise ValueError("invalid instrument table")
    orders = track[order_offset:order_offset + order_count]
    if any(x >= pattern_count for x in orders) or not speed or bpm < 32:
        raise ValueError("invalid song metadata")

    nonempty_rows = cells = 0
    for index in range(pattern_count):
        offset, size, rows = struct.unpack_from("<IHH", track, pattern_dir_offset + index * PATTERN_DIR_SIZE)
        if not rows or offset + size > len(track):
            raise ValueError("invalid pattern range")
        pos = offset
        for _ in range(rows):
            if pos + 2 > offset + size:
                raise ValueError("truncated pattern row")
            changed = struct.unpack_from("<H", track, pos)[0]
            pos += 2
            if changed & ~0x03FF:
                raise ValueError("pattern channel mask exceeds ten voices")
            if changed:
                nonempty_rows += 1
            for channel in range(VOICES):
                if not changed & (1 << channel):
                    continue
                if pos >= offset + size:
                    raise ValueError("truncated cell")
                mask = track[pos]
                pos += 1
                if not mask or mask & 0xF0:
                    raise ValueError("invalid cell mask")
                pos += (1 if mask & CELL_NOTE else 0) + (1 if mask & CELL_INSTRUMENT else 0)
                pos += (1 if mask & CELL_VOLUME else 0) + (2 if mask & CELL_EFFECT else 0)
                cells += 1
            if pos > offset + size:
                raise ValueError("cell exceeds pattern")
        if pos != offset + size:
            raise ValueError("pattern has trailing data")
    return {"orders": order_count, "patterns": pattern_count, "instruments": instrument_count,
            "cells": cells, "nonempty_rows": nonempty_rows, "track_bytes": len(track)}


def decode_track(track: bytes) -> Song:
    inspect_track(track)
    order_offset, order_count, restart = struct.unpack_from("<IHH", track, 12)
    pattern_dir_offset, pattern_count, instrument_count = struct.unpack_from("<IHH", track, 20)
    instrument_offset = struct.unpack_from("<I", track, 28)[0]
    speed, bpm = struct.unpack_from("<HH", track, 40)
    instruments = []
    for index in range(instrument_count):
        offset = instrument_offset + index * INSTRUMENT_SIZE
        mode, gain, relative, volume_len, volume_loop, pitch_len, pitch_loop = struct.unpack_from(
            "<BBhBBBB", track, offset)
        volume = tuple(track[offset + 8:offset + 8 + volume_len])
        pitch = tuple(x - 256 if x & 0x80 else x
                      for x in track[offset + 24:offset + 24 + pitch_len])
        instruments.append(Instrument(mode, gain, relative, volume, volume_loop,
                                      pitch, pitch_loop))
    patterns = []
    for index in range(pattern_count):
        offset, size, rows = struct.unpack_from("<IHH", track, pattern_dir_offset + index * PATTERN_DIR_SIZE)
        pos = offset
        pattern = []
        for _ in range(rows):
            changed = struct.unpack_from("<H", track, pos)[0]
            pos += 2
            row = [Cell() for _ in range(VOICES)]
            for channel in range(VOICES):
                if not changed & (1 << channel):
                    continue
                mask = track[pos]
                pos += 1
                note = instrument = volume = effect = parameter = 0
                if mask & CELL_NOTE:
                    note = track[pos]
                    pos += 1
                if mask & CELL_INSTRUMENT:
                    instrument = track[pos]
                    pos += 1
                if mask & CELL_VOLUME:
                    volume = track[pos] + 1
                    pos += 1
                if mask & CELL_EFFECT:
                    effect, parameter = track[pos:pos + 2]
                    pos += 2
                row[channel] = Cell(note, instrument, volume, effect, parameter)
            pattern.append(tuple(row))
        if pos != offset + size:
            raise ValueError("pattern decode did not consume its range")
        patterns.append(tuple(pattern))
    return Song(tuple(track[order_offset:order_offset + order_count]), restart, speed, bpm,
                tuple(patterns), tuple(instruments))
