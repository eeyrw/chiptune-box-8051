from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass

from .format import (ENV_ENABLED, ENV_LOOP, ENV_SUSTAIN, Cell, Instrument,
                     MAX_WAVES, MODE_PCM_BASE, PcmSample, Song, encode_track)


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
    volume_fadeout: int


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
    linear_frequency: bool


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
    flags, speed, bpm = u16(74), u16(76), u16(78)
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
        sustain = loop_start = loop_end = volume_type = fadeout = 0
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
            fadeout = u16(start + 239)

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
            sustain, loop_start, loop_end, volume_type, fadeout,
        ))
    if cursor != len(data):
        raise XmError("trailing data after XM instruments")
    return XmModule(_text(data[17:37]), orders, min(restart, song_length - 1),
                    channel_count, speed, bpm, tuple(patterns), tuple(instruments), bool(flags & 1))


def _xm_envelope(instrument: XmInstrument) -> tuple[tuple[int, ...], int, int, int, int, int]:
    if not instrument.volume_type & 1 or not instrument.volume_points:
        return (32,), 1, 0, 0xFF, 0xFF, 0xFF
    points = instrument.volume_points
    last_tick = points[-1][0]
    step = max(1, (last_tick + 14) // 15)
    count = min(16, (last_tick + step - 1) // step + 1)
    values = []
    for index in range(count):
        tick = min(last_tick, index * step)
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
    def position(point_index: int) -> int:
        tick = points[min(point_index, len(points) - 1)][0]
        return min(len(values) - 1, (tick + step // 2) // step)
    flags = ENV_ENABLED
    sustain = loop_start = loop_end = 0xFF
    if instrument.volume_type & 2:
        flags |= ENV_SUSTAIN
        sustain = position(instrument.volume_sustain)
    if instrument.volume_type & 4:
        flags |= ENV_LOOP
        loop_start = position(instrument.volume_loop_start)
        loop_end = position(instrument.volume_loop_end)
    return tuple(values) or (32,), step, flags, sustain, loop_start, loop_end


def _wave_table(sample: XmSample) -> tuple[int, ...]:
    start = sample.loop_start if sample.loop_type else 0
    length = sample.loop_length if sample.loop_type and sample.loop_length else len(sample.values)
    source = sample.values[start:min(len(sample.values), start + length)] or sample.values
    if not source:
        return (0,) * 16
    if len(source) > 256:
        limit = min(256, len(source) // 2)
        errors = []
        span = min(len(source), 1024)
        energy = sum(value * value for value in source[:span]) or 1
        for lag in range(8, limit + 1):
            error = sum((source[index] - source[(index + lag) % len(source)]) ** 2
                        for index in range(span)) / energy
            errors.append((error, lag))
        best = min(error for error, _lag in errors)
        period = min(lag for error, lag in errors if error <= best * 1.05 + 1e-9)
        source = source[:period]
    points = [source[min(len(source) - 1, index * len(source) // 16)] for index in range(16)]
    mean = sum(points) / 16.0
    centered = [value - mean for value in points]
    peak = max(abs(value) for value in centered) or 1.0
    return tuple(max(-120, min(120, round(value * 120.0 / peak))) for value in centered)


def _pcm_sample(sample: XmSample, note: int) -> PcmSample:
    semitones = note - 49 + sample.relative_note + sample.finetune / 128.0
    source_step = 8363.0 * (2.0 ** (semitones / 12.0)) / 8000.0
    output = []
    position = 0.0
    while int(position) < len(sample.values):
        left = int(position)
        right = min(len(sample.values) - 1, left + 1)
        fraction = position - left
        value = round(sample.values[left] * (1.0 - fraction) + sample.values[right] * fraction)
        output.append(max(-128, min(127, value)) & 0xFF)
        position += source_step
    return PcmSample(bytes(output or (0,)))


def _compile_instrument(instrument: XmInstrument, note: int
                        ) -> tuple[Instrument, tuple[int, ...] | None, PcmSample | None]:
    if not instrument.samples:
        return Instrument(gain=0), None, None
    sample_index = instrument.keymap[max(0, min(95, note - 1))] if instrument.keymap else 0
    sample = instrument.samples[min(sample_index, len(instrument.samples) - 1)]
    # relative_note and finetune calibrate the source sample's recorded pitch.
    # T10 replaces that sample with a normalized one-cycle oscillator, so carrying
    # the calibration across would transpose the musical note a second time.
    relative_pitch = 0
    gain = max(1, min(31, round(sample.volume * 31 / 64)))
    if sample.loop_type and sample.loop_length >= 8:
        volume, step, flags, sustain, loop_start, loop_end = _xm_envelope(instrument)
        return (Instrument(gain=gain, relative_pitch=relative_pitch,
                           volume_macro=volume, volume_step=step, volume_flags=flags,
                           volume_sustain=sustain, volume_loop_start=loop_start,
                           volume_loop_end=loop_end, fadeout=instrument.volume_fadeout),
                _wave_table(sample), None)

    if len(sample.values) > 256:
        return Instrument(gain=gain), None, _pcm_sample(sample, note)
    return Instrument(gain=gain, relative_pitch=relative_pitch), _wave_table(sample), None


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


def _instrument_note_counts(module: XmModule, orders: tuple[int, ...]) -> list[Counter]:
    counts = [Counter() for _ in module.instruments]
    active = [0] * module.channels
    for pattern_index in orders:
        for row in module.patterns[pattern_index]:
            for channel, cell in enumerate(row):
                if cell.instrument and cell.instrument <= len(counts):
                    active[channel] = cell.instrument
                if 1 <= cell.note <= 96 and active[channel]:
                    counts[active[channel] - 1][cell.note] += 1
    return counts


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
    note_counts = _instrument_note_counts(module, selected_orders)
    waves: list[tuple[int, ...]] = []
    pcm_samples: list[PcmSample] = []
    instruments = []
    for index, source in enumerate(module.instruments):
        note = note_counts[index].most_common(1)[0][0] if note_counts[index] else 49
        instrument, wave, pcm = _compile_instrument(source, note)
        if wave is not None:
            if wave not in waves:
                if len(waves) >= MAX_WAVES:
                    raise XmError("song needs more than sixteen distinct wavetables")
                waves.append(wave)
            instrument = Instrument(**{**instrument.__dict__, "mode": waves.index(wave)})
        elif pcm is not None:
            if pcm not in pcm_samples:
                pcm_samples.append(pcm)
            instrument = Instrument(**{**instrument.__dict__,
                                       "mode": MODE_PCM_BASE + pcm_samples.index(pcm)})
        instruments.append(instrument)
    if not waves:
        waves.append((96,) * 8 + (-96,) * 8)
    song = Song(tuple(pattern_map[x] for x in selected_orders), restart, module.speed, module.bpm,
                tuple(patterns), tuple(instruments), amiga_effects=not module.linear_frequency,
                waves=tuple(waves), pcm_samples=tuple(pcm_samples))
    track = encode_track(song, loop=max_orders is None)
    cells = sum(1 for pattern in patterns for row in pattern for cell in row if cell != Cell())
    return track, {
        "title": module.title, "channels": module.channels, "orders": order_count,
        "patterns": len(patterns), "instruments": len(song.instruments),
        "waves": len(song.waves), "pcm_samples": len(song.pcm_samples),
        "pcm_bytes": sum(len(sample.data) for sample in song.pcm_samples),
        "semantic_cells": cells, "track_bytes": len(track),
    }
