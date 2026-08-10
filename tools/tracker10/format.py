from __future__ import annotations

import struct
import math
from dataclasses import dataclass

RATE = 32000
VOICES = 10


@dataclass(frozen=True)
class Voice:
    increment: int = 0
    volume: int = 0
    waveform: int = 0


@dataclass(frozen=True)
class Frame:
    sample: int
    voices: tuple[Voice, ...]
    reset_mask: int = 0


def _wait(value: int, previous: int | None) -> bytes:
    if value <= 0:
        return b""
    if previous is not None:
        delta=value-previous
        if delta==0:
            return b"\x04"
        if -128<=delta<=127:
            return bytes((5,delta&0xff))
    if value <= 255:
        return bytes((1, value))
    if value <= 65535:
        return b"\x02" + struct.pack("<H", value)
    if value <= 0xFFFFFF:
        return b"\x03" + value.to_bytes(3, "little")
    raise ValueError("wait exceeds T10M limit")


def _pitch(increment: int) -> int:
    if increment <= 0:
        return 0
    frequency=increment*RATE/(1<<24)
    midi=69.0+12.0*math.log2(frequency/440.0)
    return max(1,min(0xffff,round(midi*256)))


def encode_track(frames: list[Frame], total_samples: int, loop_sample: int | None = None) -> bytes:
    if not frames or frames[0].sample != 0:
        raise ValueError("first frame must be at sample zero")
    out = bytearray()
    previous = [Voice() for _ in range(VOICES)]
    previous_pitch = [0 for _ in range(VOICES)]
    previous_wait = None
    loop_offset = None
    for index, frame in enumerate(frames):
        if len(frame.voices) != VOICES:
            raise ValueError("frame must contain ten voices")
        if loop_sample is not None and frame.sample == loop_sample:
            loop_offset = len(out)
            previous = [Voice(-1, -1, -1) for _ in range(VOICES)]
            previous_pitch = [-1 for _ in range(VOICES)]
            previous_wait = None
        for channel, voice in enumerate(frame.voices):
            old = previous[channel]
            mask = 0
            if voice.increment != old.increment:
                if not 0 <= voice.increment <= 0xFFFFFF:
                    raise ValueError("increment out of range")
                mask |= 1
            if voice.volume != old.volume:
                if not 0 <= voice.volume <= 31:
                    raise ValueError("volume out of range")
                mask |= 2
            if voice.waveform != old.waveform:
                if not 0 <= voice.waveform < 8:
                    raise ValueError("waveform out of range")
                mask |= 4
            if frame.reset_mask & (1 << channel):
                mask |= 8
            if mask:
                reset = bool(mask & 8)
                fields = mask & 7
                if fields == 1:
                    pitch=_pitch(voice.increment)
                    delta=pitch-previous_pitch[channel]
                    if not reset and -128<=delta<=127:
                        out += bytes((0x70+channel,delta&0xff))
                    else:
                        out.append((0x60 if reset else 0x30) + channel)
                        out += pitch.to_bytes(2, "little")
                elif fields == 2 and not reset:
                    out += bytes((0x50 + channel, voice.volume))
                elif fields == 7 and reset:
                    out.append(0x40 + channel)
                    out += _pitch(voice.increment).to_bytes(2, "little")
                    out.append(voice.volume | (voice.waveform << 5))
                else:
                    out += bytes((0x20 + channel, mask))
                    if fields & 1: out += voice.increment.to_bytes(3, "little")
                    if fields & 2: out.append(voice.volume)
                    if fields & 4: out.append(voice.waveform)
            previous[channel] = voice
            previous_pitch[channel] = _pitch(voice.increment)
        next_sample = frames[index + 1].sample if index + 1 < len(frames) else total_samples
        wait=next_sample-frame.sample
        out += _wait(wait,previous_wait)
        previous_wait=wait
    out.append(0)
    flags = 1 if loop_sample is not None else 0
    if flags and loop_offset is None:
        raise ValueError("loop sample must coincide with a frame")
    header = struct.pack("<4sBBBBIIIIII", b"T10M", 1, VOICES, flags, 0, RATE,
                         32, len(out), loop_offset or 0, total_samples, 0)
    return header + out


def pack_playlist(tracks: list[bytes]) -> bytes:
    if not tracks:
        raise ValueError("playlist is empty")
    offset = 16 + 8 * len(tracks)
    entries = bytearray()
    for track in tracks:
        entries += struct.pack("<II", offset, len(track))
        offset += len(track)
    return struct.pack("<4sBBHII", b"T10P", 1, 0, len(tracks), offset, 0) + entries + b"".join(tracks)


def emit_c(image: bytes) -> str:
    lines = ["/* Generated T10P image. */", "#include <stdint.h>", "",
             f"__code const unsigned char Score[{len(image)}] = {{"]
    for start in range(0, len(image), 12):
        lines.append("    " + ", ".join(f"0x{x:02X}" for x in image[start:start+12]) + ",")
    lines += ["};", f"__code const uint32_t ScoreSize = {len(image)}UL;", ""]
    return "\n".join(lines)


def inspect_track(track: bytes) -> dict[str, int]:
    if len(track)<32 or track[:4]!=b"T10M" or track[4]!=1 or track[5]!=VOICES:
        raise ValueError("invalid T10M header")
    event_offset,event_size,loop_offset,total=struct.unpack_from("<IIII",track,12)
    if event_offset+event_size>len(track):
        raise ValueError("event range exceeds track")
    events=track[event_offset:event_offset+event_size]
    pos=samples=count=0
    last_wait=0
    ended=False
    while pos<len(events):
        op=events[pos];pos+=1;count+=1
        if op==0:
            ended=True;break
        if op==1:
            wait=events[pos];pos+=1
        elif op==2:
            wait=int.from_bytes(events[pos:pos+2],"little");pos+=2
        elif op==3:
            wait=int.from_bytes(events[pos:pos+3],"little");pos+=3
        elif op==4:
            if not last_wait: raise ValueError("repeat wait without history")
            wait=last_wait
        elif op==5:
            if not last_wait: raise ValueError("delta wait without history")
            delta=events[pos];pos+=1
            if delta&0x80: delta-=256
            wait=last_wait+delta
        elif 0x20<=op<=0x29:
            mask=events[pos];pos+=1
            if mask&0xf0: raise ValueError("invalid general voice mask")
            pos+=(3 if mask&1 else 0)+(1 if mask&2 else 0)+(1 if mask&4 else 0)
            continue
        elif 0x30<=op<=0x39 or 0x60<=op<=0x69:
            pos+=2;continue
        elif 0x40<=op<=0x49:
            pos+=3;continue
        elif 0x50<=op<=0x59 or 0x70<=op<=0x79:
            pos+=1;continue
        else:
            raise ValueError(f"unknown opcode {op:#x}")
        if wait<=0: raise ValueError("non-positive wait")
        samples+=wait;last_wait=wait
    if not ended or pos!=len(events) or samples!=total:
        raise ValueError("event end or total sample mismatch")
    if track[6]&1:
        if loop_offset>=event_size: raise ValueError("loop offset out of range")
        probe=loop_offset
        while probe<len(events) and not events[probe] in (0,1,2,3,4,5):
            op=events[probe];probe+=1
            if 0x40<=op<=0x49: probe+=3
            elif 0x20<=op<=0x29:
                mask=events[probe];probe+=1
                probe+=(3 if mask&1 else 0)+(1 if mask&2 else 0)+(1 if mask&4 else 0)
            else: raise ValueError("loop snapshot is not complete-state encoded")
        if probe>=len(events) or events[probe] not in (1,2,3):
            raise ValueError("loop begins with history-dependent wait")
    return {"samples":samples,"events":count,"event_bytes":event_size}
