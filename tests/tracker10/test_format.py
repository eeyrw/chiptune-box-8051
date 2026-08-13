import math
import struct

import pytest

from tools.tracker10.format import (ENV_ENABLED, ENV_SUSTAIN, MODE_PCM_BASE, PCM_RATE,
                                    Cell, Instrument, PcmSample, Song, decode_track, encode_track,
                                    inspect_playlist, inspect_track, pack_playlist)
from tools.tracker10.reference import ReferencePlayer
from tools.tracker10.xm import (XmError, XmSample, _instrument_note_counts, _normalize_cell,
                               _wave_table, analyze_xm, _pcm_sample, compile_xm, parse_xm)


def simple_song() -> Song:
    empty = (Cell(),) * 10
    first = list(empty)
    first[0] = Cell(note=49, instrument=1, volume=65, effect=4, parameter=0x47)
    second = list(empty)
    second[0] = Cell(effect=4, parameter=0)
    pattern = (tuple(first), tuple(second), empty)
    instrument = Instrument(mode=0, gain=24, relative_pitch=-32,
                            volume_macro=(32, 20, 0), volume_flags=ENV_ENABLED,
                            pitch_macro=(8, 0), pitch_loop=0xFF)
    return Song((0, 0, 0), 1, 3, 125, (pattern,), (instrument,))


def test_semantic_track_playlist_and_crc():
    track = encode_track(simple_song())
    image = pack_playlist([track])
    info = inspect_track(track)
    assert track[:6] == b"T10M\x04\x0a"
    assert image[:5] == b"T10P\x04"
    assert info == {"orders": 3, "patterns": 1, "instruments": 1,
                    "waves": 1, "pcm_samples": 0, "pcm_bytes": 0,
                    "cells": 2, "nonempty_rows": 2, "track_bytes": len(track)}
    assert decode_track(track) == simple_song()
    assert inspect_playlist(image)["tracks"] == [info]


def test_playlist_rejects_bad_track_range():
    image = bytearray(pack_playlist([encode_track(simple_song())]))
    struct.pack_into("<I", image, 16, 25)
    with pytest.raises(ValueError, match="track 0 range"):
        inspect_playlist(bytes(image))


def test_playlist_rejects_overlapping_tracks():
    track = encode_track(simple_song())
    image = bytearray(pack_playlist([track, track]))
    first_offset, first_length = struct.unpack_from("<II", image, 16)
    struct.pack_into("<II", image, 24, first_offset, first_length)
    with pytest.raises(ValueError, match="overlap"):
        inspect_playlist(bytes(image))


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


def test_song_waves_and_pcm_are_self_contained():
    song = simple_song()
    pcm = PcmSample(bytes((0x80, 0x00, 0x7F)))
    instruments = song.instruments + (Instrument(mode=MODE_PCM_BASE, gain=31),)
    enriched = Song(song.orders, song.restart, song.speed, song.bpm, song.patterns,
                    instruments, song.amiga_effects, song.waves, (pcm,))
    decoded = decode_track(encode_track(enriched))
    assert decoded.waves == song.waves
    assert decoded.pcm_samples == (pcm,)
    assert decoded.instruments[-1].mode == MODE_PCM_BASE


def test_reference_emits_pcm_trigger_without_tonal_voice():
    empty = (Cell(),) * 10
    row = list(empty)
    row[0] = Cell(note=49, instrument=1, volume=65)
    song = Song((0,), 0, 1, 125, ((tuple(row),),),
                (Instrument(mode=MODE_PCM_BASE, gain=31),),
                pcm_samples=(PcmSample(bytes((0, 1, 2))),))
    frame = ReferencePlayer(song).step()
    assert frame.pcm_triggers == ((0, 31),)
    assert frame.voices[0].volume == 0


def test_reference_preserves_same_sample_simultaneous_pcm_triggers():
    row = [Cell() for _ in range(10)]
    row[0] = row[1] = Cell(note=49, instrument=1, volume=65)
    song = Song((0,), 0, 1, 125, ((tuple(row),),),
                (Instrument(mode=MODE_PCM_BASE, gain=31),),
                pcm_samples=(PcmSample(bytes((0, 1, 2))),))
    assert ReferencePlayer(song).step().pcm_triggers == ((0, 31), (0, 31))


def test_quiet_tonal_voice_retains_fractional_mixer_gain():
    empty = (Cell(),) * 10
    row = list(empty)
    row[0] = Cell(note=33, instrument=1, volume=5)
    song = Song((0,), 0, 1, 125, ((tuple(row),),),
                (Instrument(mode=0, gain=23),))
    frame = ReferencePlayer(song).step()
    assert frame.voices[0].volume == 6


def test_pcm_one_shot_fades_to_zero_without_changing_length():
    sample = XmSample("tail", tuple(range(-64, 64)), 0, 0, 64, 0, 0, 0)
    pcm = _pcm_sample(sample, 49).data
    signed = tuple(value - 256 if value & 0x80 else value for value in pcm)
    assert len(pcm) == math.ceil(128 * PCM_RATE / 8363)
    assert signed[-1] == 0
    assert abs(signed[-2]) <= abs(signed[-PCM_RATE // 250])


def test_pcm_one_shot_preserves_source_level_and_quiet_dynamics():
    loud = XmSample("loud", tuple(([100] * 8 + [-20] * 8) * 16), 0, 0, 64, 0, 0, 0)
    quiet = XmSample("quiet", tuple(([12] * 8 + [-12] * 8) * 16), 0, 0, 64, 0, 0, 0)
    loud_values = tuple(value - 256 if value & 0x80 else value
                        for value in _pcm_sample(loud, 49).data[:-(PCM_RATE // 250)])
    quiet_values = tuple(value - 256 if value & 0x80 else value
                         for value in _pcm_sample(quiet, 49).data[:-(PCM_RATE // 250)])
    assert max(loud_values) >= 95 and min(loud_values) <= -15
    assert sum(loud_values) / len(loud_values) > 30
    assert 12 in quiet_values and -12 in quiet_values
    assert max(map(abs, quiet_values)) <= 16


def test_pcm_resampler_attenuates_source_nyquist_energy():
    sample = XmSample("nyquist", tuple((64, -64) * 128), 0, 0, 64, 0, 0, 0)
    values = tuple(value - 256 if value & 0x80 else value
                   for value in _pcm_sample(sample, 49).data[:-(PCM_RATE // 250)])
    assert max(map(abs, values)) < 56


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
    struct.pack_into("<HHHHHH", instrument, 129, 0, 64, 6, 64, 30, 0)
    instrument[225] = 3
    instrument[227] = 1
    instrument[233] = 3
    struct.pack_into("<H", instrument, 239, 256)
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
    compiled = decode_track(track).instruments[0]
    assert compiled.relative_pitch == 0
    assert compiled.volume_flags == ENV_ENABLED | ENV_SUSTAIN
    assert compiled.volume_step == 2
    assert compiled.volume_sustain == 3
    assert compiled.fadeout == 256
    assert info["orders"] == 1
    assert info["patterns"] == 1
    assert inspect_track(track)["cells"] == 1


def test_xm_sample_default_volume_initializes_channel_linearly():
    module = parse_xm(minimal_xm())
    source_instrument = module.instruments[0]
    source_sample = source_instrument.samples[0]
    source_instrument = type(source_instrument)(
        **{**source_instrument.__dict__,
           "samples": (type(source_sample)(**{**source_sample.__dict__, "volume": 32}),)})
    row = list(module.patterns[0][0])
    row[0] = Cell(note=49, instrument=1)
    module = type(module)(**{**module.__dict__, "patterns": ((tuple(row),),),
                             "instruments": (source_instrument,)})

    track, _info = compile_xm(module)
    decoded = decode_track(track)

    assert decoded.patterns[0][0][0].volume == 33  # encoded 32 plus the T10 presence tag
    assert decoded.instruments[0].gain == 31


def test_xm_explicit_volume_overrides_sample_default_volume():
    decoded = decode_track(compile_xm(parse_xm(minimal_xm()))[0])
    assert decoded.patterns[0][0][0].volume == 65


def test_full_default_volume_uses_runtime_note_instrument_default():
    module = parse_xm(minimal_xm())
    source_instrument = module.instruments[0]
    source_sample = source_instrument.samples[0]
    source_instrument = type(source_instrument)(
        **{**source_instrument.__dict__,
           "samples": (type(source_sample)(**{**source_sample.__dict__, "volume": 64}),)})
    row = list(module.patterns[0][0])
    row[0] = Cell(note=49, instrument=1)
    module = type(module)(**{**module.__dict__, "patterns": ((tuple(row),),),
                             "instruments": (source_instrument,)})
    decoded = decode_track(compile_xm(module)[0])
    assert decoded.patterns[0][0][0].volume == 0


def test_xm_wavetable_preserves_quiet_source_amplitude():
    sample = XmSample("quiet", tuple((12, -12) * 8), 0, 16, 64, 0, 1, 0)
    wave = _wave_table(sample)
    assert max(wave) == 12
    assert min(wave) == -12


def test_xm_wavetable_preserves_source_dc_offset():
    sample = XmSample("offset", (10,) * 8 + (30,) * 8, 0, 16, 64, 0, 1, 0)
    assert _wave_table(sample) == sample.values


def test_xm_analysis_reports_unsupported_instrument_automation():
    module = parse_xm(minimal_xm())
    instrument = module.instruments[0]
    instrument = type(instrument)(**{**instrument.__dict__, "panning_type": 1,
                                    "vibrato_depth": 2, "vibrato_rate": 3})
    module = type(module)(**{**module.__dict__, "instruments": (instrument,)})
    warnings = analyze_xm(module)["warnings"]
    assert "enabled panning envelopes are not rendered" in warnings
    assert "instrument automatic vibrato is not rendered" in warnings


def test_zero_order_preview_is_rejected():
    with pytest.raises(XmError, match="at least one"):
        compile_xm(parse_xm(minimal_xm()), 0)


def test_empty_final_restart_pattern_keys_off_and_ends_song():
    module = parse_xm(minimal_xm())
    empty = (Cell(),) * module.channels
    terminal = list(empty)
    terminal[0] = Cell(effect=0x0F, parameter=6)
    module = type(module)(**{**module.__dict__, "orders": (0, 1), "restart": 1,
                             "patterns": (module.patterns[0], (tuple(terminal),))})

    track, info = compile_xm(module)
    decoded = decode_track(track)

    assert not track[6] & 1
    assert info["loop"] is False
    assert all(cell.note == 97 for cell in decoded.patterns[1][0])
    assert "empty final restart pattern is treated as a one-shot ending" in analyze_xm(module)["warnings"]


def test_musical_final_restart_pattern_remains_a_loop():
    module = parse_xm(minimal_xm())
    module = type(module)(**{**module.__dict__, "orders": (0, 0), "restart": 1})
    track, info = compile_xm(module)
    assert track[6] & 1
    assert info["loop"] is True


def test_arpeggio_only_final_restart_pattern_remains_a_loop():
    module = parse_xm(minimal_xm())
    arpeggio = list((Cell(),) * module.channels)
    arpeggio[0] = Cell(effect=0, parameter=0x37)
    module = type(module)(**{**module.__dict__, "orders": (0, 1), "restart": 1,
                             "patterns": (module.patterns[0], (tuple(arpeggio),))})
    track, info = compile_xm(module)
    assert track[6] & 1
    assert info["loop"] is True


@pytest.mark.parametrize(("offset", "value", "message"), (
    (58, b"\x03\x01", "unsupported XM version"),
    (625, b"\x41", "invalid volume"),
    (627, b"\x03", "unsupported type flags"),
))
def test_malformed_xm_sample_metadata_is_rejected(offset, value, message):
    data = bytearray(minimal_xm())
    data[offset:offset + len(value)] = value
    with pytest.raises(XmError, match=message):
        parse_xm(bytes(data))


def test_xm_keymap_cannot_reference_missing_sample():
    data = bytearray(minimal_xm())
    data[383] = 1
    with pytest.raises(XmError, match="keymap"):
        parse_xm(bytes(data))


def test_xm_cell_cannot_reference_missing_instrument():
    module = parse_xm(minimal_xm())
    row = list(module.patterns[0][0])
    row[0] = Cell(note=49, instrument=2)
    module = type(module)(**{**module.__dict__, "patterns": ((tuple(row),),)})
    with pytest.raises(XmError, match="unknown instrument 2"):
        compile_xm(module)


def test_unsupported_effect_fails_instead_of_sounding_wrong():
    module = parse_xm(minimal_xm(5, 1))
    with pytest.raises(XmError, match="unsupported effect"):
        compile_xm(module)


def test_xm_set_volume_effect_lowers_to_absolute_volume():
    assert _normalize_cell(Cell(effect=0x0C, parameter=0), 1, 2, 3) == Cell(volume=1)
    assert _normalize_cell(Cell(effect=0x0C, parameter=64), 1, 2, 3) == Cell(volume=65)
    with pytest.raises(XmError, match="invalid set-volume"):
        _normalize_cell(Cell(effect=0x0C, parameter=65), 1, 2, 3)
    with pytest.raises(XmError, match="conflicting volume-column"):
        _normalize_cell(Cell(volume=0x20, effect=0x0C, parameter=32), 1, 2, 3)


def test_empty_extended_effect_is_a_noop_but_nonzero_e0x_is_rejected():
    assert _normalize_cell(Cell(effect=0x0E, parameter=0), 1, 2, 3) == Cell()
    with pytest.raises(XmError, match="unsupported extended effect E01"):
        _normalize_cell(Cell(effect=0x0E, parameter=1), 1, 2, 3)


def test_volume_column_slides_reuse_main_volume_slide_effect():
    assert _normalize_cell(Cell(volume=0x63), 1, 2, 3) == Cell(effect=0x0A, parameter=3)
    assert _normalize_cell(Cell(volume=0x74), 1, 2, 3) == Cell(effect=0x0A, parameter=0x40)
    assert _normalize_cell(Cell(volume=0x8F), 1, 2, 3) == Cell()
    assert _normalize_cell(Cell(volume=0x63, effect=1, parameter=2), 1, 2, 3) == Cell(
        effect=1, parameter=2)


def test_lossy_effect_lowering_is_explicit_and_note_cut_is_preserved():
    assert _normalize_cell(Cell(effect=6, parameter=0x03), 1, 2, 3) == Cell(
        effect=0x0A, parameter=3)
    assert _normalize_cell(Cell(effect=9, parameter=0x20), 1, 2, 3) == Cell()
    assert _normalize_cell(Cell(note=49, effect=0x0E, parameter=0xD2), 1, 2, 3) == Cell(note=49)
    assert _normalize_cell(Cell(effect=0x0E, parameter=0xC2), 1, 2, 3) == Cell(
        effect=0x0E, parameter=0xC2)


def test_reference_vm_cuts_note_on_requested_tick():
    row = [Cell() for _ in range(10)]
    row[0] = Cell(note=49, instrument=1, effect=0x0E, parameter=0xC2)
    song = Song((0,), 0, 4, 125, ((tuple(row),),), (Instrument(),))
    player = ReferencePlayer(song)
    frames = [player.step() for _ in range(4)]
    assert [frame.voices[0].volume > 0 for frame in frames] == [True, True, False, False]


def test_reference_vm_pattern_break_and_position_jump():
    empty = (Cell(),) * 10

    def marked(note, effect=0, parameter=0):
        row = list(empty)
        row[0] = Cell(note=note, instrument=1, effect=effect, parameter=parameter)
        return tuple(row)

    patterns = (
        (marked(49, 0x0B, 2),),
        (marked(50),),
        (marked(51), marked(52), marked(53, 0x0D, 1)),
        (marked(54), marked(55)),
    )
    song = Song((0, 1, 2, 3), 0, 1, 125, patterns, (Instrument(),))
    player = ReferencePlayer(song)
    frames = [player.step() for _ in range(4)]
    assert [(frame.order, frame.row) for frame in frames] == [(0, 0), (2, 0), (2, 1), (2, 2)]
    assert (player.order, player.row) == (3, 1)


def test_xm_rejects_invalid_flow_control_before_encoding():
    module = parse_xm(minimal_xm(0x0B, 1))
    with pytest.raises(XmError, match="position jump"):
        compile_xm(module)
    module = parse_xm(minimal_xm(0x0D, 0x1A))
    with pytest.raises(XmError, match="invalid pattern break"):
        compile_xm(module)


def test_pattern_break_on_last_order_uses_restart_order():
    empty = (Cell(),) * 10
    first = list(empty)
    first[0] = Cell(note=49, instrument=1, effect=0x0D)
    song = Song((0,), 0, 1, 125, ((tuple(first),),), (Instrument(),))
    player = ReferencePlayer(song)
    frame = player.step()
    assert (frame.order, frame.row) == (0, 0)
    assert (player.order, player.row) == (0, 0)


def test_xm_note_statistics_follow_instrument_memory_and_order_reuse():
    module = parse_xm(minimal_xm())
    first = list(module.patterns[0][0])
    first[0] = Cell(note=49, instrument=1)
    continuation = list(first)
    continuation[0] = Cell(note=61)
    pattern = (tuple(first), tuple(continuation))
    module = type(module)(module.title, (0, 0), module.restart, module.channels,
                          module.speed, module.bpm, (pattern,), module.instruments,
                          module.linear_frequency)
    counts = _instrument_note_counts(module, module.orders)
    assert counts[0][49] == 2
    assert counts[0][61] == 2


def test_reference_vm_keeps_vibrato_parameter_memory():
    player = ReferencePlayer(decode_track(encode_track(simple_song())))
    frames = [player.step() for _ in range(6)]
    assert [x.tick for x in frames] == [0, 1, 2, 0, 1, 2]
    assert frames[0].voices[0].reset
    assert not frames[3].voices[0].reset
    assert frames[4].voices[0].pitch != frames[3].voices[0].pitch
    assert sum(x.wait_samples for x in frames) == 3840


def test_keyoff_releases_envelope_instead_of_cutting_immediately():
    empty = (Cell(),) * 10
    rows = []
    for cell in (Cell(note=49, instrument=1), Cell(note=97), Cell(), Cell()):
        row = list(empty)
        row[0] = cell
        rows.append(tuple(row))
    instrument = Instrument(gain=20, volume_flags=ENV_ENABLED | ENV_SUSTAIN,
                            volume_sustain=0, fadeout=16384)
    song = Song((0,), 0, 1, 125, (tuple(rows),), (instrument,))
    player = ReferencePlayer(song)
    frames = [player.step() for _ in range(4)]
    assert frames[0].voices[0].volume > 0
    assert frames[1].voices[0].volume > 0
    assert frames[2].voices[0].volume > 0
    assert frames[3].voices[0].volume == 0


def test_new_note_without_instrument_keeps_current_channel_volume():
    empty = (Cell(),) * 10
    rows = []
    for cell in (Cell(note=49, instrument=1, volume=17), Cell(note=50)):
        row = list(empty)
        row[0] = cell
        rows.append(tuple(row))
    song = Song((0,), 0, 1, 125, (tuple(rows),), (Instrument(gain=31),))
    player = ReferencePlayer(song)
    first, second = player.step(), player.step()
    assert first.voices[0].volume == second.voices[0].volume


def test_amiga_effect_scaling_is_note_dependent():
    empty = (Cell(),) * 10
    row = list(empty)
    row[0] = Cell(note=49, instrument=1, effect=2, parameter=1)
    song = Song((0,), 0, 2, 125, ((tuple(row),),), (Instrument(),), True)
    player = ReferencePlayer(song)
    first, second = player.step(), player.step()
    assert first.voices[0].pitch - second.voices[0].pitch == 10
