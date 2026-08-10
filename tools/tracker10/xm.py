from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .format import Cell, Instrument, Song, encode_track


class XmError(ValueError):
    pass


@dataclass(frozen=True)
class XmSample:
    name: str
    values: tuple[int, ...]
    loop_start: int
    loop_length: int
    volume: int
    finetune: int
    loop_type: int
    relative_note: int


@dataclass(frozen=True)
class XmInstrument:
    name: str
    keymap: tuple[int, ...]
    samples: tuple[XmSample, ...]
    volume_points: tuple[tuple[int, int], ...]
    volume_sustain: int
    volume_loop_start: int
    volume_loop_end: int
    volume_type: int


@dataclass(frozen=True)
class XmModule:
    title: str
    orders: tuple[int, ...]
    restart: int
    channels: int
    speed: int
    bpm: int
    patterns: tuple[tuple[tuple[Cell, ...], ...], ...]
    instruments: tuple[XmInstrument, ...]


def _text(value: bytes) -> str:
    return value.rstrip(b" \0").decode("cp437", errors="replace")


def _decode_sample(raw: bytes, sixteen_bit: bool) -> tuple[int, ...]:
    values: list[int] = []
    accumulator = 0
    if sixteen_bit:
        if len(raw) & 1:
            raise XmError("odd-sized 16-bit XM sample")
        for (delta,) in struct.iter_unpack("<h", raw):
            accumulator = ((accumulator + delta + 32768) & 0xFFFF) - 32768
            values.append(accumulator >> 8)
    else:
        for byte in raw:
            delta = byte - 256 if byte & 0x80 else byte
            accumulator = ((accumulator + delta + 128) & 0xFF) - 128
            values.append(accumulator)
    return tuple(values)


def parse_xm(data: bytes) -> XmModule:
    if len(data) < 80 or data[:17] != b"Extended Module: " or data[37] != 0x1A:
        raise XmError("not a FastTracker II XM module")
    u16 = lambda p: struct.unpack_from("<H", data, p)[0]
    u32 = lambda p: struct.unpack_from("<I", data, p)[0]
    header_size = u32(60)
    song_length, restart = u16(64), u16(66)
    channel_count, pattern_count, instrument_count = u16(68), u16(70), u16(72)
    speed, bpm = u16(76), u16(78)
    if not 1 <= song_length <= 256 or not 1 <= channel_count <= 64 or not speed or not bpm:
        raise XmError("invalid XM header")
    orders = tuple(data[80:80 + song_length])
    if len(orders) != song_length or any(x >= pattern_count for x in orders):
        raise XmError("invalid XM order list")

    cursor = 60 + header_size
    patterns: list[tuple[tuple[Cell, ...], ...]] = []
    for _ in range(pattern_count):
        if cursor + 9 > len(data):
            raise XmError("truncated XM pattern header")
        header_length, rows, packed_size = u32(cursor), u16(cursor + 5), u16(cursor + 7)
        pos, end = cursor + header_length, cursor + header_length + packed_size
        if header_length < 9 or not 1 <= rows <= 256 or end > len(data):
            raise XmError("invalid XM pattern")
        pattern: list[tuple[Cell, ...]] = []
        for _row in range(rows):
            row: list[Cell] = []
            for _channel in range(channel_count):
                values = [0, 0, 0, 0, 0]
                if pos < end:
                    marker = data[pos]
                    pos += 1
                    if marker & 0x80:
                        for index, bit in enumerate((1, 2, 4, 8, 16)):
                            if marker & bit:
                                if pos >= end:
                                    raise XmError("truncated XM cell")
                                values[index] = data[pos]
                                pos += 1
                    else:
                        if pos + 4 > end:
                            raise XmError("truncated XM cell")
                        values[0] = marker
                        values[1:] = data[pos:pos + 4]
                        pos += 4
                row.append(Cell(*values))
            pattern.append(tuple(row))
        if pos != end:
            raise XmError("trailing XM pattern data")
        patterns.append(tuple(pattern))
        cursor = end

    instruments: list[XmInstrument] = []
    for _ in range(instrument_count):
        if cursor + 29 > len(data):
            raise XmError("truncated XM instrument")
        start = cursor
        header_length = u32(start)
        sample_count = u16(start + 27)
        if header_length < 29 or start + header_length > len(data):
            raise XmError("invalid XM instrument header")
        keymap: tuple[int, ...] = ()
        volume_points: tuple[tuple[int, int], ...] = ()
        sustain = loop_start = loop_end = volume_type = 0
        sample_header_size = 0
        if sample_count:
            if header_length < 243:
                raise XmError("short sampled XM instrument header")
            sample_header_size = u32(start + 29)
            if sample_header_size < 40:
                raise XmError("short XM sample header")
            keymap = tuple(data[start + 33:start + 129])
            point_count = min(data[start + 225], 12)
            volume_points = tuple((u16(start + 129 + i * 4), u16(start + 131 + i * 4))
                                  for i in range(point_count))
            sustain, loop_start, loop_end = data[start + 227:start + 230]
            volume_type = data[start + 233]

        sample_headers = start + header_length
        sample_data = sample_headers + sample_count * sample_header_size
        if sample_data > len(data):
            raise XmError("truncated XM sample headers")
        samples: list[XmSample] = []
        raw_cursor = sample_data
        for index in range(sample_count):
            pos = sample_headers + index * sample_header_size
            length, raw_loop_start, raw_loop_length = u32(pos), u32(pos + 4), u32(pos + 8)
            sample_type = data[pos + 14]
            if raw_cursor + length > len(data):
                raise XmError("truncated XM sample data")
            sixteen_bit = bool(sample_type & 0x10)
            values = _decode_sample(data[raw_cursor:raw_cursor + length], sixteen_bit)
            divisor = 2 if sixteen_bit else 1
            samples.append(XmSample(
                _text(data[pos + 18:pos + 40]), values,
                raw_loop_start // divisor, raw_loop_length // divisor,
                min(data[pos + 12], 64), struct.unpack_from("b", data, pos + 13)[0],
                sample_type & 3, struct.unpack_from("b", data, pos + 16)[0],
            ))
            raw_cursor += length
        cursor = raw_cursor
        instruments.append(XmInstrument(
            _text(data[start + 4:start + 26]), keymap, tuple(samples), volume_points,
            sustain, loop_start, loop_end, volume_type,
        ))
    if cursor != len(data):
        raise XmError("trailing data after XM instruments")
    return XmModule(_text(data[17:37]), orders, min(restart, song_length - 1),
                    channel_count, speed, bpm, tuple(patterns), tuple(instruments))


_WAVES = (
    (96,) * 8 + (-96,) * 8,
    (112,) * 4 + (-48,) * 12,
    (120,) * 2 + (-24,) * 14,
    (-112, -96, -80, -64, -48, -32, -16, 0, 16, 32, 48, 64, 80, 96, 112, 0),
    (-120, -104, -88, -72, -56, -40, -24, -8, 8, 24, 40, 56, 72, 88, 104, 120),
    (0, 45, 83, 108, 117, 108, 83, 45, 0, -45, -83, -108, -117, -108, -83, -45),
)


def _resample(values: tuple[int, ...], start: int, length: int) -> tuple[float, ...]:
    if not values or length <= 0:
        return (0.0,) * 16
    end = min(len(values), start + length)
    source = values[max(0, start):end]
    if not source:
        source = values
    return tuple(float(source[min(len(source) - 1, i * len(source) // 16)]) for i in range(16))


def _match_wave(sample: XmSample) -> int:
    start = sample.loop_start if sample.loop_type else 0
    length = sample.loop_length if sample.loop_type and sample.loop_length else len(sample.values)
    source = _resample(sample.values, start, length)
    source_mean = sum(source) / 16.0
    source = tuple(x - source_mean for x in source)
    source_norm = math.sqrt(sum(x * x for x in source)) or 1.0
    best_score, best_wave = -2.0, 0
    for wave_index, wave in enumerate(_WAVES):
        norm = math.sqrt(sum(float(x * x) for x in wave)) or 1.0
        for shift in range(16):
            score = abs(sum(source[i] * wave[(i + shift) & 15] for i in range(16)) /
                        (source_norm * norm))
            if score > best_score:
                best_score, best_wave = score, wave_index
    return best_wave


def _sample_envelope(sample: XmSample) -> tuple[int, ...]:
    values = sample.values
    if not values:
        return (32,)
    # One macro step is approximately one 50 Hz tracker tick at the XM base rate.
    step = 167
    count = max(2, min(15, (len(values) + step - 1) // step))
    levels = []
    for index in range(count):
        block = values[index * len(values) // count:(index + 1) * len(values) // count]
        rms = math.sqrt(sum(x * x for x in block) / max(1, len(block)))
        levels.append(rms)
    peak = max(levels) or 1.0
    result = tuple(max(1, min(32, round(x * 32.0 / peak))) for x in levels) + (0,)
    return result[:16]


def _xm_envelope(instrument: XmInstrument) -> tuple[tuple[int, ...], int]:
    if not instrument.volume_type & 1 or not instrument.volume_points:
        return (32,), 0
    points = instrument.volume_points
    last_tick = min(15, points[-1][0])
    values = []
    for tick in range(last_tick + 1):
        left = points[0]
        right = points[-1]
        for candidate in points[1:]:
            if tick <= candidate[0]:
                right = candidate
                break
            left = candidate
        if right[0] == left[0]:
            level = left[1]
        else:
            level = left[1] + (right[1] - left[1]) * (tick - left[0]) / (right[0] - left[0])
        values.append(max(0, min(32, round(level / 2))))
    loop = 0xFF
    if instrument.volume_type & 4 and instrument.volume_loop_start < len(points):
        loop = min(15, points[instrument.volume_loop_start][0])
    elif instrument.volume_type & 2 and instrument.volume_sustain < len(points):
        loop = min(15, points[instrument.volume_sustain][0])
    return tuple(values) or (32,), loop


def _compile_instrument(instrument: XmInstrument) -> Instrument:
    if not instrument.samples:
        return Instrument(gain=0)
    sample_index = instrument.keymap[47] if instrument.keymap else 0
    sample = instrument.samples[min(sample_index, len(instrument.samples) - 1)]
    # relative_note and finetune calibrate the source sample's recorded pitch.
    # T10 replaces that sample with a normalized one-cycle oscillator, so carrying
    # the calibration across would transpose the musical note a second time.
    relative_pitch = 0
    gain = max(1, min(31, round(sample.volume * 31 / 64)))
    if sample.loop_type and sample.loop_length >= 8:
        volume, volume_loop = _xm_envelope(instrument)
        return Instrument(_match_wave(sample), gain, relative_pitch, volume, volume_loop, (0,), 0)

    if len(sample.values) > 256:
        rms = math.sqrt(sum(x * x for x in sample.values) / len(sample.values)) or 1.0
        derivative = math.sqrt(sum((b - a) ** 2 for a, b in zip(sample.values, sample.values[1:])) /
                               max(1, len(sample.values) - 1)) / rms
        if derivative > 0.42:
            mode = 6
        elif derivative > 0.27:
            mode = 7
        else:
            mode = 3
        volume = _sample_envelope(sample)
        pitch = (96, 64, 40, 24, 12, 4, 0) if mode == 3 else (0,)
        return Instrument(mode, gain, relative_pitch, volume, 0xFF, pitch, 0xFF)
    return Instrument(_match_wave(sample), gain, relative_pitch, (32,), 0, (0,), 0)


def _normalize_cell(cell: Cell, pattern: int, row: int, channel: int) -> Cell:
    volume = 0
    if cell.volume:
        if 0x10 <= cell.volume <= 0x50:
            volume = cell.volume - 0x10 + 1
        elif 0xC0 <= cell.volume <= 0xCF:
            volume = 0  # Mono output deliberately discards volume-column panning.
        else:
            raise XmError(f"unsupported volume-column command {cell.volume:#x} at {pattern}:{row}:{channel}")
    effect, parameter = cell.effect, cell.parameter
    if effect == 8:
        effect = parameter = 0  # Mono output deliberately discards panning.
    elif effect == 0x0E:
        if parameter >> 4 != 9:
            raise XmError(f"unsupported extended effect E{parameter:02X} at {pattern}:{row}:{channel}")
    elif effect not in (0, 1, 2, 3, 4, 0x0A, 0x0F):
        raise XmError(f"unsupported effect {effect:X}{parameter:02X} at {pattern}:{row}:{channel}")
    return Cell(cell.note, cell.instrument, volume, effect, parameter)


def compile_xm(module: XmModule, max_orders: int | None = None) -> tuple[bytes, dict]:
    if module.channels > 10:
        raise XmError(f"XM has {module.channels} channels; maximum is 10")
    order_count = len(module.orders) if max_orders is None else min(max_orders, len(module.orders))
    selected_orders = module.orders[:order_count]
    used_patterns: list[int] = []
    for pattern in selected_orders:
        if pattern not in used_patterns:
            used_patterns.append(pattern)
    pattern_map = {source: target for target, source in enumerate(used_patterns)}
    patterns = []
    for source_index in used_patterns:
        normalized_rows = []
        for row_index, source_row in enumerate(module.patterns[source_index]):
            row = [_normalize_cell(cell, source_index, row_index, channel)
                   for channel, cell in enumerate(source_row)]
            row.extend(Cell() for _ in range(10 - len(row)))
            normalized_rows.append(tuple(row))
        patterns.append(tuple(normalized_rows))
    restart = 0
    if max_orders is None or max_orders > module.restart:
        restart = min(module.restart, order_count - 1)
    song = Song(tuple(pattern_map[x] for x in selected_orders), restart, module.speed, module.bpm,
                tuple(patterns), tuple(_compile_instrument(x) for x in module.instruments))
    track = encode_track(song, loop=max_orders is None)
    cells = sum(1 for pattern in patterns for row in pattern for cell in row if cell != Cell())
    return track, {
        "title": module.title, "channels": module.channels, "orders": order_count,
        "patterns": len(patterns), "instruments": len(song.instruments),
        "semantic_cells": cells, "track_bytes": len(track),
    }
