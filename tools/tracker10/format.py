from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

RATE = 32000
PCM_RATE = 8000
VOICES = 10
TRACK_HEADER_SIZE = 48
VERSION = 4
INSTRUMENT_SIZE = 48
PATTERN_DIR_SIZE = 8
RESOURCE_HEADER_SIZE = 8
PCM_ENTRY_SIZE = 8
WAVE_SIZE = 16
MAX_WAVES = 16

MODE_PCM_BASE = 0x80
MODE_NOISE_LONG = 0xFE
MODE_NOISE_SHORT = 0xFF

TRACK_LOOP = 0x01
TRACK_AMIGA_EFFECTS = 0x02

ENV_ENABLED = 0x01
ENV_SUSTAIN = 0x02
ENV_LOOP = 0x04

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
    volume_step: int = 1
    volume_flags: int = 0
    volume_sustain: int = 0xFF
    volume_loop_start: int = 0xFF
    volume_loop_end: int = 0xFF
    fadeout: int = 0
    pitch_macro: tuple[int, ...] = (0,)
    pitch_loop: int = 0


@dataclass(frozen=True)
class PcmSample:
    data: bytes


DEFAULT_WAVE = (96,) * 8 + (-96,) * 8


@dataclass(frozen=True)
class Song:
    orders: tuple[int, ...]
    restart: int
    speed: int
    bpm: int
    patterns: tuple[tuple[tuple[Cell, ...], ...], ...]
    instruments: tuple[Instrument, ...]
    amiga_effects: bool = False
    waves: tuple[tuple[int, ...], ...] = (DEFAULT_WAVE,)
    pcm_samples: tuple[PcmSample, ...] = ()


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


def _valid_mode(mode: int, wave_count: int, pcm_count: int) -> bool:
    return (0 <= mode < wave_count or mode in (MODE_NOISE_LONG, MODE_NOISE_SHORT)
            or MODE_PCM_BASE <= mode < MODE_PCM_BASE + pcm_count)


def _encode_instrument(instrument: Instrument, wave_count: int, pcm_count: int) -> bytes:
    if not _valid_mode(instrument.mode, wave_count, pcm_count) or not 0 <= instrument.gain <= 31:
        raise ValueError("invalid instrument mode or gain")
    volume = tuple(instrument.volume_macro)
    pitch = tuple(instrument.pitch_macro)
    if not 1 <= len(volume) <= 16 or not 1 <= len(pitch) <= 16:
        raise ValueError("instrument macro exceeds 16 steps")
    if any(not 0 <= x <= 32 for x in volume):
        raise ValueError("volume macro value is outside 0..32")
    if any(not -128 <= x <= 127 for x in pitch):
        raise ValueError("pitch macro value is outside signed byte range")
    if not 1 <= instrument.volume_step <= 255 or instrument.volume_flags & ~0x07:
        raise ValueError("invalid volume envelope timing or flags")
    if instrument.volume_flags & ENV_SUSTAIN:
        if not 0 <= instrument.volume_sustain < len(volume):
            raise ValueError("invalid volume sustain point")
    elif instrument.volume_sustain != 0xFF:
        raise ValueError("disabled volume sustain must be 0xff")
    if instrument.volume_flags & ENV_LOOP:
        if not (0 <= instrument.volume_loop_start <= instrument.volume_loop_end < len(volume)):
            raise ValueError("invalid volume envelope loop")
    elif instrument.volume_loop_start != 0xFF or instrument.volume_loop_end != 0xFF:
        raise ValueError("disabled volume loop points must be 0xff")
    if not 0 <= instrument.fadeout <= 0xFFFF:
        raise ValueError("invalid volume fadeout")
    if instrument.pitch_loop != 0xFF and not 0 <= instrument.pitch_loop < len(pitch):
        raise ValueError("invalid pitch macro loop")
    out = bytearray(INSTRUMENT_SIZE)
    struct.pack_into("<BBhBBBBBBBBH", out, 0, instrument.mode, instrument.gain,
                     instrument.relative_pitch, len(volume), instrument.volume_step,
                     instrument.volume_flags, instrument.volume_sustain,
                     instrument.volume_loop_start, instrument.volume_loop_end,
                     len(pitch), instrument.pitch_loop, instrument.fadeout)
    out[16:16 + len(volume)] = bytes(volume)
    out[32:32 + len(pitch)] = bytes(x & 0xFF for x in pitch)
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
    if not 1 <= len(song.waves) <= MAX_WAVES:
        raise ValueError("invalid wavetable count")
    if len(song.pcm_samples) > MODE_NOISE_LONG - MODE_PCM_BASE:
        raise ValueError("too many PCM samples")
    for wave in song.waves:
        if len(wave) != WAVE_SIZE or any(not -128 <= value <= 127 for value in wave):
            raise ValueError("wavetable must contain sixteen signed bytes")
    for sample in song.pcm_samples:
        if not sample.data or len(sample.data) > 0xFFFF:
            raise ValueError("PCM sample length is outside 1..65535")

    encoded_patterns = [_encode_pattern(pattern) for pattern in song.patterns]
    orders_offset = TRACK_HEADER_SIZE
    pattern_dir_offset = orders_offset + len(song.orders)
    instrument_dir_offset = pattern_dir_offset + len(song.patterns) * PATTERN_DIR_SIZE
    resource_offset = instrument_dir_offset + len(song.instruments) * INSTRUMENT_SIZE

    wave_data = b"".join(bytes(value & 0xFF for value in wave) for wave in song.waves)
    pcm_data_offset = (resource_offset + RESOURCE_HEADER_SIZE + len(wave_data)
                       + len(song.pcm_samples) * PCM_ENTRY_SIZE)
    pcm_directory = bytearray()
    pcm_data = bytearray()
    cursor = pcm_data_offset
    for sample in song.pcm_samples:
        pcm_directory += struct.pack("<IHH", cursor, len(sample.data), 0)
        pcm_data += sample.data
        cursor += len(sample.data)
    resource_data = (struct.pack("<4sBBH", b"T10R", len(song.waves),
                                 len(song.pcm_samples), PCM_RATE)
                     + wave_data + bytes(pcm_directory) + bytes(pcm_data))
    data_offset = resource_offset + len(resource_data)

    pattern_dir = bytearray()
    pattern_data = bytearray()
    cursor = data_offset
    for pattern, encoded in zip(song.patterns, encoded_patterns):
        pattern_dir += struct.pack("<IHH", cursor, len(encoded), len(pattern))
        pattern_data += encoded
        cursor += len(encoded)

    instrument_data = b"".join(_encode_instrument(x, len(song.waves), len(song.pcm_samples))
                               for x in song.instruments)
    body = (bytes(song.orders) + bytes(pattern_dir) + instrument_data + resource_data +
            bytes(pattern_data))
    total_size = TRACK_HEADER_SIZE + len(body)
    header = struct.pack(
        "<4sBBBBI IHH IHH III HH I",
        b"T10M", VERSION, VOICES,
        (TRACK_LOOP if loop else 0) | (TRACK_AMIGA_EFFECTS if song.amiga_effects else 0), 0, RATE,
        orders_offset, len(song.orders), song.restart,
        pattern_dir_offset, len(song.patterns), len(song.instruments),
        instrument_dir_offset, total_size, zlib.crc32(body) & 0xFFFFFFFF,
        song.speed, song.bpm, resource_offset,
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
    return struct.pack("<4sBBHII", b"T10P", VERSION, 0, len(tracks), offset, 0) + entries + b"".join(tracks)


def emit_c(image: bytes) -> str:
    lines = [f"/* Generated T10P v{VERSION} semantic tracker image. */", "#include <stdint.h>", "",
             f"__code const unsigned char Score[{len(image)}] = {{"]
    for start in range(0, len(image), 12):
        lines.append("    " + ", ".join(f"0x{x:02X}" for x in image[start:start + 12]) + ",")
    lines += ["};", f"__code const uint32_t ScoreSize = {len(image)}UL;", ""]
    return "\n".join(lines)


def inspect_track(track: bytes) -> dict[str, int]:
    if len(track) < TRACK_HEADER_SIZE or track[:4] != b"T10M" or track[4] != VERSION or track[5] != VOICES:
        raise ValueError(f"invalid T10M v{VERSION} header")
    order_offset, order_count, restart = struct.unpack_from("<IHH", track, 12)
    pattern_dir_offset, pattern_count, instrument_count = struct.unpack_from("<IHH", track, 20)
    instrument_offset, total_size, expected_crc = struct.unpack_from("<III", track, 28)
    speed, bpm = struct.unpack_from("<HH", track, 40)
    resource_offset = struct.unpack_from("<I", track, 44)[0]
    if track[6] & ~(TRACK_LOOP | TRACK_AMIGA_EFFECTS) or track[7]:
        raise ValueError("invalid T10M flags or reserved bytes")
    if total_size != len(track) or zlib.crc32(track[TRACK_HEADER_SIZE:]) & 0xFFFFFFFF != expected_crc:
        raise ValueError("track size or CRC mismatch")
    if not order_count or order_offset + order_count > len(track) or restart >= order_count:
        raise ValueError("invalid order table")
    if not pattern_count or pattern_dir_offset + pattern_count * PATTERN_DIR_SIZE > len(track):
        raise ValueError("invalid pattern directory")
    if instrument_offset + instrument_count * INSTRUMENT_SIZE > len(track):
        raise ValueError("invalid instrument table")
    if resource_offset + RESOURCE_HEADER_SIZE > len(track) or track[resource_offset:resource_offset + 4] != b"T10R":
        raise ValueError("invalid resource header")
    wave_count, pcm_count, pcm_rate = struct.unpack_from("<BBH", track, resource_offset + 4)
    if not 1 <= wave_count <= MAX_WAVES or pcm_rate != PCM_RATE:
        raise ValueError("invalid resource metadata")
    wave_offset = resource_offset + RESOURCE_HEADER_SIZE
    pcm_directory_offset = wave_offset + wave_count * WAVE_SIZE
    if pcm_directory_offset + pcm_count * PCM_ENTRY_SIZE > len(track):
        raise ValueError("invalid PCM directory")
    pcm_samples: list[bytes] = []
    for index in range(pcm_count):
        offset = pcm_directory_offset + index * PCM_ENTRY_SIZE
        data_offset, length, reserved = struct.unpack_from("<IHH", track, offset)
        if reserved or not length or data_offset + length > len(track):
            raise ValueError("invalid PCM sample range")
        pcm_samples.append(track[data_offset:data_offset + length])
    for index in range(instrument_count):
        offset = instrument_offset + index * INSTRUMENT_SIZE
        (mode, gain, _relative, volume_len, volume_step, volume_flags,
         sustain, loop_start, loop_end, pitch_len, pitch_loop,
         _fadeout) = struct.unpack_from("<BBhBBBBBBBBH", track, offset)
        if not _valid_mode(mode, wave_count, pcm_count) or gain > 31 or not 1 <= volume_len <= 16 or not volume_step:
            raise ValueError("invalid instrument header")
        if volume_flags & ~0x07 or not 1 <= pitch_len <= 16:
            raise ValueError("invalid instrument macro metadata")
        if bool(volume_flags & ENV_SUSTAIN) != (sustain != 0xFF):
            raise ValueError("invalid instrument sustain")
        if sustain != 0xFF and sustain >= volume_len:
            raise ValueError("invalid instrument sustain position")
        if volume_flags & ENV_LOOP:
            if not 0 <= loop_start <= loop_end < volume_len:
                raise ValueError("invalid instrument envelope loop")
        elif loop_start != 0xFF or loop_end != 0xFF:
            raise ValueError("invalid disabled envelope loop")
        if pitch_loop != 0xFF and pitch_loop >= pitch_len:
            raise ValueError("invalid pitch macro loop")
        if track[offset + 14:offset + 16] != b"\0\0":
            raise ValueError("invalid instrument reserved bytes")
        if any(value > 32 for value in track[offset + 16:offset + 16 + volume_len]):
            raise ValueError("invalid volume macro value")
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
            "waves": wave_count, "pcm_samples": pcm_count,
            "pcm_bytes": sum(len(sample) for sample in pcm_samples),
            "cells": cells, "nonempty_rows": nonempty_rows, "track_bytes": len(track)}


def decode_track(track: bytes) -> Song:
    inspect_track(track)
    order_offset, order_count, restart = struct.unpack_from("<IHH", track, 12)
    pattern_dir_offset, pattern_count, instrument_count = struct.unpack_from("<IHH", track, 20)
    instrument_offset = struct.unpack_from("<I", track, 28)[0]
    speed, bpm = struct.unpack_from("<HH", track, 40)
    resource_offset = struct.unpack_from("<I", track, 44)[0]
    wave_count, pcm_count, _pcm_rate = struct.unpack_from("<BBH", track, resource_offset + 4)
    wave_offset = resource_offset + RESOURCE_HEADER_SIZE
    waves = []
    for index in range(wave_count):
        offset = wave_offset + index * WAVE_SIZE
        waves.append(tuple(value - 256 if value & 0x80 else value
                           for value in track[offset:offset + WAVE_SIZE]))
    pcm_directory_offset = wave_offset + wave_count * WAVE_SIZE
    pcm_samples = []
    for index in range(pcm_count):
        offset = pcm_directory_offset + index * PCM_ENTRY_SIZE
        data_offset, length = struct.unpack_from("<IH", track, offset)
        pcm_samples.append(PcmSample(track[data_offset:data_offset + length]))
    instruments = []
    for index in range(instrument_count):
        offset = instrument_offset + index * INSTRUMENT_SIZE
        (mode, gain, relative, volume_len, volume_step, volume_flags,
         volume_sustain, volume_loop_start, volume_loop_end, pitch_len,
         pitch_loop, fadeout) = struct.unpack_from("<BBhBBBBBBBBH", track, offset)
        volume = tuple(track[offset + 16:offset + 16 + volume_len])
        pitch = tuple(x - 256 if x & 0x80 else x
                      for x in track[offset + 32:offset + 32 + pitch_len])
        instruments.append(Instrument(mode, gain, relative, volume, volume_step,
                                      volume_flags, volume_sustain, volume_loop_start,
                                      volume_loop_end, fadeout, pitch, pitch_loop))
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
    flags = track[6]
    return Song(tuple(track[order_offset:order_offset + order_count]), restart, speed, bpm,
                tuple(patterns), tuple(instruments), bool(flags & TRACK_AMIGA_EFFECTS),
                tuple(waves), tuple(pcm_samples))
