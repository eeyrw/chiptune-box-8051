from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass

from .format import (Cell, Instrument, MAX_WAVES, MODE_PCM_BASE, PcmSample, Song,
                     encode_track, optimize_song)
from .effects import (DEFAULT_MULTI_NOTE_PCM, DEFAULT_RESOURCE_POLICY, DEVICE_EXX,
                      MAX_WAVE_LOOP, RESOURCE_POLICIES, RESOURCE_POLICY_PCM,
                      RESOURCE_POLICY_WAVE, scale_sample_offset)
from .xm import XmSample, _pcm_sample, _wave_table


class ModError(ValueError):
    pass

# libxmp PERIOD_BASE (C0 period). note = round(12 * log2(BASE / period)) + 1
# maps ProTracker period 856 -> note 49 (C-4 in FT2 numbering).
PERIOD_BASE = 13696.0

# Fixed signatures from libxmp mod_magic plus the generic ?CHN / ??CH families.
_FIXED_SIGNATURES = {
    b"M.K.": 4,
    b"M!K!": 4,
    b"M&K!": 4,
    b"N.T.": 4,
    b"4CHN": 4,
    b"6CHN": 6,
    b"8CHN": 8,
    b"CD61": 6,
    b"CD81": 8,
    b"FLT4": 4,
    b"FLT8": 8,
    b"TDZ1": 1,
    b"TDZ2": 2,
    b"TDZ3": 3,
    b"TDZ4": 4,
    b"FA04": 4,
    b"FA06": 6,
    b"FA08": 8,
    b"NSMS": 4,
    b"LARD": 4,
    b"OKTA": 8,
    b"OCTA": 8,
    b"2CHN": 2,
}


@dataclass(frozen=True)
class ModSample:
    name: str
    values: tuple[int, ...]
    loop_start: int
    loop_length: int
    volume: int
    finetune: int  # signed nibble -8..7
    loop_type: int


@dataclass(frozen=True)
class ModModule:
    title: str
    signature: bytes
    orders: tuple[int, ...]
    restart: int
    channels: int
    patterns: tuple[tuple[tuple[Cell, ...], ...], ...]
    samples: tuple[ModSample, ...]
    speed: int = 6
    bpm: int = 125
    clamped_periods: int = 0


def _text(value: bytes) -> str:
    return value.rstrip(b" \0").decode("cp437", errors="replace")


def _u16be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def detect_mod_signature(data: bytes) -> tuple[bytes, int] | None:
    """Return (signature, channel_count) for a 31-sample MOD, else None."""
    if len(data) < 1084:
        return None
    magic = bytes(data[1080:1084])
    if magic in _FIXED_SIGNATURES:
        return magic, _FIXED_SIGNATURES[magic]
    if (len(magic) == 4 and magic[1:] == b"CHN" and magic[:1].isdigit()
            and magic[0] != ord("0")):
        return magic, magic[0] - ord("0")
    if (len(magic) == 4 and magic[2:] == b"CH" and magic[0:1].isdigit()
            and magic[1:2].isdigit()):
        channels = (magic[0] - ord("0")) * 10 + (magic[1] - ord("0"))
        if 1 <= channels <= 32:
            return magic, channels
    return None


def is_mod(data: bytes) -> bool:
    return detect_mod_signature(data) is not None


def period_to_note(period: int, *, strict: bool = True) -> int:
    """Convert an Amiga period to the FT2/XM note index used by T10."""
    if period <= 0:
        return 0
    note = int(round(12.0 * math.log(PERIOD_BASE / period) / math.log(2.0))) + 1
    if 1 <= note <= 96:
        return note
    if strict:
        raise ModError(f"MOD period {period} maps outside notes 1..96")
    return 1 if note < 1 else 96


def _signed_nibble(value: int) -> int:
    nibble = value & 0x0F
    return nibble - 16 if nibble >= 8 else nibble


def _decode_cell(raw: bytes) -> tuple[int, int, int, int]:
    sample = (raw[0] & 0xF0) | (raw[2] >> 4)
    period = ((raw[0] & 0x0F) << 8) | raw[1]
    effect = raw[2] & 0x0F
    parameter = raw[3]
    return sample, period, effect, parameter


def parse_mod(data: bytes) -> ModModule:
    detected = detect_mod_signature(data)
    if detected is None:
        raise ModError("not a recognized 31-sample ProTracker-compatible MOD")
    signature, channels = detected
    if channels > 10:
        raise ModError(f"MOD has {channels} channels; maximum is 10")
    if channels < 1:
        raise ModError("MOD channel count is invalid")

    title = _text(data[0:20])
    samples_meta: list[tuple[str, int, int, int, int, int]] = []
    cursor = 20
    for index in range(31):
        name = _text(data[cursor:cursor + 22])
        length_words = _u16be(data, cursor + 22)
        finetune_raw = data[cursor + 24]
        volume = data[cursor + 25]
        loop_start_words = _u16be(data, cursor + 26)
        loop_length_words = _u16be(data, cursor + 28)
        # libxmp rejects non-zero high finetune nibbles except the rare 0x20 case.
        if finetune_raw & 0xF0 and finetune_raw != 0x20:
            raise ModError(f"sample {index + 1} has invalid finetune {finetune_raw:#x}")
        if volume > 64:
            raise ModError(f"sample {index + 1} has invalid volume {volume}")
        samples_meta.append((name, length_words, _signed_nibble(finetune_raw),
                             volume, loop_start_words, loop_length_words))
        cursor += 30

    song_length = data[950]
    restart_byte = data[951]
    if not 1 <= song_length <= 128:
        raise ModError(f"invalid MOD song length {song_length}")
    order_bytes = data[952:1080]
    orders = tuple(order_bytes[:song_length])
    if any(order > 0x7F for order in orders):
        raise ModError("MOD order list contains an invalid pattern index")
    pattern_count = max(orders) + 1 if orders else 1
    if pattern_count > 128:
        raise ModError("MOD declares more than 128 patterns")

    # Noisetracker-style restart when in range; classic ProTracker 0x7F means 0.
    if restart_byte < 0x7F and restart_byte != 0x78 and restart_byte < song_length:
        restart = restart_byte
    else:
        restart = 0

    pattern_bytes = 64 * channels * 4
    patterns_offset = 1084
    samples_offset = patterns_offset + pattern_count * pattern_bytes
    if samples_offset > len(data):
        raise ModError("truncated MOD pattern data")

    patterns: list[tuple[tuple[Cell, ...], ...]] = []
    clamped_periods = 0
    for pattern_index in range(pattern_count):
        base = patterns_offset + pattern_index * pattern_bytes
        rows: list[tuple[Cell, ...]] = []
        for row_index in range(64):
            row: list[Cell] = []
            for channel in range(channels):
                pos = base + (row_index * channels + channel) * 4
                sample, period, effect, parameter = _decode_cell(data[pos:pos + 4])
                if sample > 31:
                    raise ModError(
                        f"sample {sample} out of range at "
                        f"{pattern_index}:{row_index}:{channel}")
                if period:
                    raw_note = int(round(12.0 * math.log(PERIOD_BASE / period) / math.log(2.0))) + 1
                    if not 1 <= raw_note <= 96:
                        clamped_periods += 1
                    note = period_to_note(period, strict=False)
                else:
                    note = 0
                row.append(Cell(note, sample, 0, effect, parameter))
            rows.append(tuple(row))
        patterns.append(tuple(rows))

    samples: list[ModSample] = []
    cursor = samples_offset
    for index, (name, length_words, finetune, volume,
                loop_start_words, loop_length_words) in enumerate(samples_meta):
        # Length 0 or 1 word is an empty instrument slot in ProTracker.
        byte_length = 0 if length_words <= 1 else length_words * 2
        if cursor + byte_length > len(data):
            raise ModError(f"truncated MOD sample data for sample {index + 1}")
        raw = data[cursor:cursor + byte_length]
        cursor += byte_length
        values = tuple(struct.unpack(f"{len(raw)}b", raw)) if raw else ()
        loop_start = loop_start_words * 2
        loop_length = loop_length_words * 2
        # Match libxmp: loop when loop size > 1 word and end is at least 4 bytes.
        loop_end = loop_start + loop_length
        if loop_length_words > 1 and loop_end >= 4 and values:
            if loop_start >= len(values):
                raise ModError(f"sample {index + 1} has a loop start past the end")
            if loop_end > len(values):
                loop_length = len(values) - loop_start
            if loop_length < 4:
                loop_type = 0
                loop_start = loop_length = 0
            else:
                loop_type = 1
        else:
            loop_type = 0
            loop_start = loop_length = 0
        samples.append(ModSample(name, values, loop_start, loop_length, volume,
                                 finetune, loop_type))

    # Trailing bytes are common (pad, credits); do not require an exact end.

    return ModModule(title, signature, orders, restart, channels,
                     tuple(patterns), tuple(samples), clamped_periods=clamped_periods)


def _to_xm_sample(sample: ModSample) -> XmSample:
    # MOD finetune is 1/8 semitone; XM finetune is 1/128 semitone.
    return XmSample(sample.name, sample.values, sample.loop_start, sample.loop_length,
                    sample.volume, sample.finetune * 16, sample.loop_type, 0)


def _sample_resource_kind(sample: ModSample,
                          resource_policy: str = DEFAULT_RESOURCE_POLICY) -> str:
    if not sample.values:
        return "empty"
    # MOD: non-looped samples are almost always drums/one-shots -> PCM.
    # Loops only become 16-pt waves when length is in 8..MAX_WAVE_LOOP.
    # (resource_policy=pcm uses the same cap; wave policy does not promote long loops.)
    if sample.loop_type and 8 <= sample.loop_length <= MAX_WAVE_LOOP:
        return "wave"
    if sample.loop_type and sample.loop_length > MAX_WAVE_LOOP:
        return "long_loop_pcm"
    return "oneshot_pcm"


def _compile_sample(sample: ModSample, note: int, force_pcm: bool = False,
                    resource_policy: str = DEFAULT_RESOURCE_POLICY
                    ) -> tuple[Instrument, tuple[int, ...] | None, PcmSample | None]:
    if not sample.values:
        return Instrument(gain=0), None, None
    xm_sample = _to_xm_sample(sample)
    gain = 31
    if not force_pcm and _sample_resource_kind(sample, resource_policy) == "wave":
        return (Instrument(gain=gain, relative_pitch=0),
                _wave_table(xm_sample), None)
    pcm = _pcm_sample(xm_sample, note)
    if len(pcm.data) > 0xFFFF:
        pcm = type(pcm)(pcm.data[:0xFFFF])
    return Instrument(gain=gain), None, pcm


def _resource_warnings(module: ModModule, note_counts: list[Counter] | None = None,
                       resource_policy: str = DEFAULT_RESOURCE_POLICY) -> list[str]:
    kinds = [_sample_resource_kind(sample, resource_policy) for sample in module.samples]
    warnings = []
    wave = sum(kind == "wave" for kind in kinds)
    long_loop = sum(kind == "long_loop_pcm" for kind in kinds)
    oneshot = sum(kind == "oneshot_pcm" for kind in kinds)
    if long_loop:
        warnings.append(
            f"{long_loop} long sample loops become one-shot PCM "
            f"(loop length > {MAX_WAVE_LOOP}); sustain loops are not preserved")
    if oneshot:
        warnings.append(
            f"{oneshot} non-looped samples become 16 kHz PCM one-shots")
    if wave:
        if resource_policy == RESOURCE_POLICY_WAVE:
            warnings.append(
                f"{wave} looped samples become 16-point wavetables and sustain until cut")
        else:
            warnings.append(
                f"{wave} short loops become 16-point wavetables and sustain until cut")
    if note_counts is None:
        note_counts = _sample_note_counts(module, module.orders)
    multi = sum(
        1 for kind, counts in zip(kinds, note_counts)
        if kind in ("long_loop_pcm", "oneshot_pcm") and len(counts) > 1)
    if multi:
        warnings.append(
            f"{multi} multi-note PCM instruments use the most-common note rate only")
    if module.clamped_periods:
        warnings.append(
            f"{module.clamped_periods} periods were clamped into notes 1..96")
    return warnings



def _scale_pattern_offsets(patterns, instruments, pcm_samples, pcm_source_lens):
    if not pcm_source_lens:
        return patterns
    out = []
    for pattern in patterns:
        rows = []
        active = [0] * 10
        for row in pattern:
            cells = []
            for ch, cell in enumerate(row):
                if cell.instrument:
                    active[ch] = cell.instrument
                if cell.effect == 9 and cell.parameter and active[ch]:
                    ins = instruments[active[ch] - 1]
                    if MODE_PCM_BASE <= ins.mode < MODE_PCM_BASE + len(pcm_samples):
                        pidx = ins.mode - MODE_PCM_BASE
                        scaled = scale_sample_offset(
                            cell.parameter, pcm_source_lens[pidx],
                            len(pcm_samples[pidx].data))
                        if scaled != cell.parameter:
                            cell = Cell(cell.note, cell.instrument, cell.volume,
                                        cell.effect, scaled)
                cells.append(cell)
            rows.append(tuple(cells))
        out.append(tuple(rows))
    return out

def _normalize_cell(cell: Cell, pattern: int, row: int, channel: int) -> Cell:
    effect, parameter = cell.effect, cell.parameter
    volume = 0
    if effect == 0x0C:
        # Some converted MODs store volumes above 64; clamp like common PC players.
        volume = min(64, parameter) + 1
        effect = parameter = 0
    elif effect == 0x08:
        effect = parameter = 0  # mono hardware discards panning
    elif effect == 0x09:
        pass  # applied on PCM triggers in the device VM
    elif effect in (0x05, 0x06, 0x07):
        pass  # combined porta/vib+volslide and tremolo run on the device
    elif effect == 0x0E:
        sub = parameter >> 4
        if parameter == 0:
            effect = parameter = 0  # empty E00
        elif sub not in DEVICE_EXX:
            raise ModError(
                f"unsupported extended effect E{parameter:02X} at "
                f"{pattern}:{row}:{channel}")
    elif effect == 0x0F:
        if parameter == 0:
            parameter = 1  # practical ProTracker treatment of F00
    elif effect not in (0, 1, 2, 3, 4, 5, 6, 7, 0x0A, 0x0B, 0x0D):
        raise ModError(
            f"unsupported effect {effect:X}{parameter:02X} at "
            f"{pattern}:{row}:{channel}")
    if cell.note and not 1 <= cell.note <= 96:
        raise ModError(f"invalid note {cell.note} at {pattern}:{row}:{channel}")
    if cell.instrument > 31:
        raise ModError(
            f"unknown instrument {cell.instrument} at {pattern}:{row}:{channel}")
    return Cell(cell.note, cell.instrument, volume, effect, parameter)


def _sample_note_counts(module: ModModule, orders: tuple[int, ...]) -> list[Counter]:
    counts = [Counter() for _ in module.samples]
    active = [0] * module.channels
    for pattern_index in orders:
        for row in module.patterns[pattern_index]:
            for channel, cell in enumerate(row):
                if cell.instrument:
                    active[channel] = cell.instrument
                if 1 <= cell.note <= 96 and active[channel]:
                    counts[active[channel] - 1][cell.note] += 1
    return counts


def analyze_mod(module: ModModule) -> dict:
    effects: Counter[str] = Counter()
    for pattern_index in module.orders:
        for row in module.patterns[pattern_index]:
            for cell in row:
                if cell.effect or cell.parameter:
                    effects[f"{cell.effect:X}xx"] += 1

    warnings = []
    if module.channels > 10:
        warnings.append(f"module has {module.channels} channels; Tracker10 accepts at most 10")
    if effects["8xx"]:
        warnings.append("panning commands are discarded because hardware output is mono")
    # 5xx/6xx/7xx are implemented in the device VM.
    if effects["9xx"]:
        warnings.append(
            f"{effects['9xx']} sample-offset commands apply to PCM voices "
            f"(sources used with 9xx are compiled as PCM)")
    def count_e(sub: int) -> int:
        return sum(
            1 for pattern_index in module.orders
            for row in module.patterns[pattern_index]
            for cell in row
            if cell.effect == 0x0E and cell.parameter >> 4 == sub)

    unsupported_e = sum(
        1 for pattern_index in module.orders
        for row in module.patterns[pattern_index]
        for cell in row
        if cell.effect == 0x0E and cell.parameter
        and (cell.parameter >> 4) not in DEVICE_EXX)
    if unsupported_e:
        warnings.append(
            f"{unsupported_e} unsupported Exx commands will fail compile "
            f"(E0/E3/E4/E5/E7/EF etc.)")
    over_vol = sum(
        1 for pattern_index in module.orders
        for row in module.patterns[pattern_index]
        for cell in row
        if cell.effect == 0x0C and cell.parameter > 64)
    if over_vol:
        warnings.append(f"{over_vol} set-volume commands above 64 are clamped")
    warnings.extend(_resource_warnings(module))
    return {
        "title": module.title,
        "format": "mod",
        "signature": module.signature.decode("ascii", errors="replace"),
        "channels": module.channels,
        "orders": len(module.orders),
        "patterns": len(module.patterns),
        "instruments": len(module.samples),
        "samples": sum(1 for sample in module.samples if sample.values),
        "speed": module.speed,
        "bpm": module.bpm,
        "frequency_mode": "amiga",
        "effects": dict(sorted(effects.items())),
        "warnings": warnings,
    }


def compile_mod(module: ModModule, max_orders: int | None = None,
                resource_policy: str = DEFAULT_RESOURCE_POLICY,
                multi_note_pcm: bool = DEFAULT_MULTI_NOTE_PCM) -> tuple[bytes, dict]:
    if module.channels > 10:
        raise ModError(f"MOD has {module.channels} channels; maximum is 10")
    if max_orders is not None and max_orders < 1:
        raise ModError("max-orders must be at least one")
    if resource_policy not in RESOURCE_POLICIES:
        raise ModError(f"unknown resource policy {resource_policy!r}")
    order_count = len(module.orders) if max_orders is None else min(max_orders, len(module.orders))
    selected_orders = module.orders[:order_count]
    note_counts = _sample_note_counts(module, selected_orders)
    default_volumes = [sample.volume for sample in module.samples]

    used_patterns: list[int] = []
    for pattern in selected_orders:
        if pattern not in used_patterns:
            used_patterns.append(pattern)
    pattern_map = {source: target for target, source in enumerate(used_patterns)}

    patterns = []
    for source_index in used_patterns:
        normalized_rows = []
        for row_index, source_row in enumerate(module.patterns[source_index]):
            row = []
            for channel, cell in enumerate(source_row):
                normalized = _normalize_cell(cell, source_index, row_index, channel)
                if (normalized.instrument and not normalized.volume
                        and (not normalized.note
                             or default_volumes[normalized.instrument - 1] != 64)):
                    volume = default_volumes[normalized.instrument - 1] + 1
                    normalized = Cell(normalized.note, normalized.instrument, volume,
                                      normalized.effect, normalized.parameter)
                if normalized.effect == 0x0B and normalized.parameter >= order_count:
                    raise ModError(
                        f"position jump B{normalized.parameter:02X} exceeds "
                        f"{order_count} orders at {source_index}:{row_index}:{channel}")
                if normalized.effect == 0x0D:
                    break_row = (normalized.parameter >> 4) * 10 + (normalized.parameter & 15)
                    if normalized.parameter & 15 > 9:
                        raise ModError(
                            f"invalid pattern break D{normalized.parameter:02X} at "
                            f"{source_index}:{row_index}:{channel}")
                    if break_row > 255:
                        raise ModError(
                            f"pattern break D{normalized.parameter:02X} exceeds row 255 at "
                            f"{source_index}:{row_index}:{channel}")
                row.append(normalized)
            row.extend(Cell() for _ in range(10 - len(row)))
            normalized_rows.append(tuple(row))
        patterns.append(tuple(normalized_rows))

    restart = 0
    if max_orders is None or max_orders > module.restart:
        restart = min(module.restart, order_count - 1)

    waves: list[tuple[int, ...]] = []
    pcm_samples: list[PcmSample] = []
    pcm_source_lens: list[int] = []
    force_pcm = [False] * len(module.samples)
    active = [0] * module.channels
    for pattern_index in selected_orders:
        for row in module.patterns[pattern_index]:
            for channel, cell in enumerate(row):
                if cell.instrument and cell.instrument <= len(force_pcm):
                    active[channel] = cell.instrument
                if cell.effect == 9 and cell.parameter and active[channel]:
                    force_pcm[active[channel] - 1] = True

    instruments: list[Instrument] = []
    pcm_note_map: dict[tuple[int, int], int] = {}
    source_to_ins: dict[int, int] = {}
    multi_pcm_sources: set[int] = set()

    def _append_pcm(pcm: PcmSample, source_len: int) -> int:
        pidx = next(
            (i for i, (blob, length) in enumerate(zip(pcm_samples, pcm_source_lens))
             if blob == pcm and length == source_len),
            None)
        if pidx is None:
            pcm_samples.append(pcm)
            pcm_source_lens.append(source_len)
            pidx = len(pcm_samples) - 1
        if len(pcm_samples) > 0x7E:
            raise ModError("song needs more than 126 PCM samples")
        return pidx

    for index, source in enumerate(module.samples):
        counts = note_counts[index]
        default_note = counts.most_common(1)[0][0] if counts else 49
        _p, _w, probe_pcm = _compile_sample(
            source, default_note, force_pcm=force_pcm[index],
            resource_policy=resource_policy)
        is_multi = bool(multi_note_pcm and probe_pcm is not None and len(counts) > 1)
        notes = sorted(counts) if is_multi else [default_note]
        if is_multi:
            multi_pcm_sources.add(index + 1)
        first_id = None
        for note in notes:
            instrument, wave, pcm = _compile_sample(
                source, note, force_pcm=force_pcm[index],
                resource_policy=resource_policy)
            if wave is not None:
                if wave not in waves:
                    if len(waves) >= MAX_WAVES:
                        raise ModError("song needs more than sixteen distinct wavetables")
                    waves.append(wave)
                instrument = Instrument(**{**instrument.__dict__, "mode": waves.index(wave)})
            elif pcm is not None:
                pidx = _append_pcm(pcm, len(source.values))
                instrument = Instrument(**{**instrument.__dict__,
                                           "mode": MODE_PCM_BASE + pidx})
            if len(instruments) >= 255:
                raise ModError("song needs more than 255 instruments after PCM note split")
            instruments.append(instrument)
            new_id = len(instruments)
            if first_id is None:
                first_id = new_id
            if is_multi:
                pcm_note_map[(index + 1, note)] = new_id
            else:
                source_to_ins[index + 1] = new_id
        if not is_multi and first_id is not None:
            source_to_ins[index + 1] = first_id

    if multi_pcm_sources or any(source_to_ins.get(i, i) != i for i in source_to_ins):
        rewritten = []
        for pattern in patterns:
            new_rows = []
            active_src = [0] * 10
            for row in pattern:
                new_cells = []
                for ch, cell in enumerate(row):
                    if cell.instrument:
                        active_src[ch] = cell.instrument
                    src = active_src[ch]
                    new_ins = cell.instrument
                    if src in multi_pcm_sources:
                        if 1 <= cell.note <= 96:
                            new_ins = pcm_note_map.get((src, cell.note))
                            if new_ins is None:
                                common = note_counts[src - 1].most_common(1)[0][0]
                                new_ins = pcm_note_map[(src, common)]
                        elif cell.instrument:
                            common = note_counts[src - 1].most_common(1)[0][0]
                            new_ins = pcm_note_map[(src, common)]
                    elif cell.instrument:
                        new_ins = source_to_ins.get(cell.instrument, cell.instrument)
                    if new_ins and new_ins != cell.instrument:
                        new_cells.append(Cell(cell.note, new_ins, cell.volume,
                                              cell.effect, cell.parameter))
                    else:
                        new_cells.append(cell)
                new_rows.append(tuple(new_cells))
            rewritten.append(tuple(new_rows))
        patterns = rewritten

    if not waves:
        waves.append((96,) * 8 + (-96,) * 8)
    if not instruments:
        raise ModError("MOD has no instruments")
    patterns = _scale_pattern_offsets(patterns, instruments, pcm_samples, pcm_source_lens)

    song = optimize_song(Song(
        tuple(pattern_map[x] for x in selected_orders), restart, module.speed,
        module.bpm, tuple(patterns), tuple(instruments), amiga_effects=True,
        waves=tuple(waves), pcm_samples=tuple(pcm_samples)))
    track = encode_track(song, loop=max_orders is None)
    cells = sum(1 for pattern in song.patterns for row in pattern for cell in row if cell != Cell())
    return track, {
        "title": module.title,
        "format": "mod",
        "signature": module.signature.decode("ascii", errors="replace"),
        "channels": module.channels,
        "orders": order_count,
        "patterns": len(song.patterns),
        "instruments": len(song.instruments),
        "waves": len(song.waves),
        "pcm_samples": len(song.pcm_samples),
        "pcm_bytes": sum(len(sample.data) for sample in song.pcm_samples),
        "loop": max_orders is None,
        "resource_policy": resource_policy,
        "multi_note_pcm": multi_note_pcm,
        "semantic_cells": cells,
        "track_bytes": len(track),
    }
