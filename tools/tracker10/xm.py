from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from tracker10.format import Frame, Voice, RATE, VOICES, encode_track


class XmError(ValueError):
    pass


@dataclass(frozen=True)
class Cell:
    note: int=0
    instrument: int=0
    volume: int=0
    effect: int=0
    parameter: int=0


@dataclass
class XmModule:
    title: str
    orders: list[int]
    restart: int
    channels: int
    speed: int
    bpm: int
    patterns: list[list[list[Cell]]]


def parse_xm(data: bytes) -> XmModule:
    if len(data)<80 or data[:17]!=b"Extended Module: " or data[37]!=0x1a:
        raise XmError("not a FastTracker II XM module")
    u16=lambda p: struct.unpack_from("<H",data,p)[0]
    u32=lambda p: struct.unpack_from("<I",data,p)[0]
    title=data[17:37].rstrip(b" \0").decode("cp437",errors="replace")
    header_size=u32(60); song_length=u16(64); restart=u16(66)
    channel_count=u16(68); pattern_count=u16(70); speed=u16(76); bpm=u16(78)
    if not 1<=song_length<=256 or not 1<=channel_count<=64 or not speed or not bpm:
        raise XmError("invalid XM header")
    orders=list(data[80:80+song_length])
    if len(orders)!=song_length or any(x>=pattern_count for x in orders):
        raise XmError("invalid XM order list")
    cursor=60+header_size
    patterns=[]
    for _ in range(pattern_count):
        header_length=u32(cursor); rows=u16(cursor+5); packed_size=u16(cursor+7)
        pos=cursor+header_length; end=pos+packed_size
        if header_length<9 or not 1<=rows<=256 or end>len(data):
            raise XmError("invalid XM pattern")
        pattern=[]
        for _row in range(rows):
            row=[]
            for _channel in range(channel_count):
                values=[0,0,0,0,0]
                if pos<end:
                    marker=data[pos];pos+=1
                    if marker&0x80:
                        for index,bit in enumerate((1,2,4,8,16)):
                            if marker&bit:
                                if pos>=end: raise XmError("truncated XM cell")
                                values[index]=data[pos];pos+=1
                    else:
                        if pos+4>end: raise XmError("truncated XM cell")
                        values[0]=marker;values[1:]=data[pos:pos+4];pos+=4
                row.append(Cell(*values))
            pattern.append(row)
        if pos!=end: raise XmError("trailing XM pattern data")
        patterns.append(pattern);cursor=end
    return XmModule(title,orders,min(restart,song_length-1),channel_count,speed,bpm,patterns)


@dataclass
class Channel:
    note: float = 0.0
    target: float = 0.0
    instrument: int = 0
    volume: int = 48
    effect: int = 0
    parameter: int = 0
    vibrato_phase: float = 0.0


def _wave(channel: int, instrument: int) -> int:
    if channel in (6, 7) and instrument <= 6:
        return 7
    if channel == 5:
        return 3
    return (0, 1, 2, 4, 5, 6)[(instrument + channel) % 6]


def _level(channel: int, instrument: int, volume: int) -> int:
    peak = 15 if channel in (0, 9) else 12
    if channel in (6, 7): peak = 11
    if channel == 8: peak = 7
    return max(0, min(31, round(volume * peak / 64)))


def _increment(xm_note: float) -> int:
    if xm_note <= 0:
        return 0
    midi = xm_note + 11.0
    frequency = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
    return max(1, min(0xFFFFFF, round(frequency * (1 << 24) / RATE)))


def compile_xm(module: XmModule, max_orders: int | None = None) -> tuple[bytes, dict]:
    if module.channels > VOICES:
        raise ValueError(f"XM has {module.channels} channels; maximum is {VOICES}")
    channels = [Channel() for _ in range(VOICES)]
    speed, bpm = module.speed, module.bpm
    sample_position = 0.0
    frames: list[Frame] = []
    previous: tuple[Voice, ...] | None = None
    loop_sample = None
    order_count = len(module.orders) if max_orders is None else min(max_orders, len(module.orders))

    for order_index, pattern_index in enumerate(module.orders[:order_count]):
        if order_index == module.restart:
            loop_sample = round(sample_position)
        for row in module.patterns[pattern_index]:
            retrigger = 0
            for index, cell in enumerate(row[:VOICES]):
                ch = channels[index]
                if cell.instrument:
                    ch.instrument = cell.instrument
                if 0x10 <= cell.volume <= 0x50:
                    ch.volume = cell.volume - 0x10
                ch.effect, ch.parameter = cell.effect, cell.parameter
                if cell.effect == 0x0F and cell.parameter:
                    if cell.parameter < 0x20: speed = cell.parameter
                    else: bpm = cell.parameter
                if cell.note == 97:
                    ch.note = ch.target = 0
                    ch.volume = 0
                elif 1 <= cell.note <= 96:
                    if cell.effect == 3 and ch.note:
                        ch.target = float(cell.note)
                    else:
                        ch.note = ch.target = float(cell.note)
                        if ch.volume == 0: ch.volume = 48
                        retrigger |= 1 << index

            for tracker_tick in range(speed):
                voices = []
                for index, ch in enumerate(channels):
                    note = ch.note
                    if ch.effect == 0 and ch.parameter and note:
                        shift = (0, ch.parameter >> 4, ch.parameter & 15)[tracker_tick % 3]
                        note += shift
                    elif ch.effect == 3 and ch.target and ch.note != ch.target:
                        step = max(0.05, ch.parameter / 64.0)
                        ch.note += max(-step, min(step, ch.target - ch.note))
                        note = ch.note
                    elif ch.effect == 4 and note:
                        speed_v = ch.parameter >> 4
                        depth = ch.parameter & 15
                        ch.vibrato_phase += speed_v * math.pi / 16.0
                        note += math.sin(ch.vibrato_phase) * depth / 16.0
                    if ch.effect == 0x0A and tracker_tick:
                        up, down = ch.parameter >> 4, ch.parameter & 15
                        ch.volume = max(0, min(64, ch.volume + up - down))
                    voices.append(Voice(_increment(note), _level(index,ch.instrument,ch.volume) if note else 0,
                                        _wave(index,ch.instrument)))
                state = tuple(voices)
                at = round(sample_position)
                force_loop = loop_sample is not None and at == loop_sample
                if state != previous or (tracker_tick == 0 and retrigger) or force_loop:
                    reset = retrigger if tracker_tick == 0 else 0
                    if force_loop: reset = 0x03FF
                    frames.append(Frame(at,state,reset))
                    previous = state
                sample_position += 80000.0 / bpm

    total = round(sample_position)
    if max_orders is not None and max_orders <= module.restart:
        loop_sample = None
    track = encode_track(frames,total,loop_sample)
    return track, {"title":module.title,"channels":module.channels,"orders":order_count,
                   "duration_seconds":round(total/RATE,3),"track_bytes":len(track),
                   "loop_seconds":None if loop_sample is None else round(loop_sample/RATE,3)}
