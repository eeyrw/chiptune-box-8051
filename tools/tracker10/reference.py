from __future__ import annotations

from dataclasses import dataclass, field

import math

from .format import (ENV_ENABLED, ENV_LOOP, ENV_SUSTAIN, MODE_NOISE_LONG,
                     MODE_PCM_BASE, Instrument, Song, VOICES)


@dataclass(frozen=True)
class VoiceFrame:
    pitch: int
    volume: int
    mode: int
    reset: bool


@dataclass(frozen=True)
class TickFrame:
    wait_samples: int
    voices: tuple[VoiceFrame, ...]
    order: int
    row: int
    tick: int
    pcm_triggers: tuple[tuple[int, int], ...] = ()


@dataclass
class Channel:
    note: int = 0
    target: int = 0
    instrument: Instrument = field(default_factory=Instrument)
    volume: int = 64
    effect: int = 0
    parameter: int = 0
    slide_up: int = 0
    slide_down: int = 0
    porta: int = 0
    vibrato_speed: int = 0
    vibrato_depth: int = 0
    volume_slide: int = 0
    vibrato_phase: int = 0
    volume_position: int = 0
    pitch_position: int = 0
    volume_tick: int = 0
    fadeout: int = 0xFFFF
    key_on: bool = False
    gate: bool = False
    reset: bool = False


SINE = (0, 49, 90, 117, 127, 117, 90, 49, 0, -49, -90, -117, -127, -117, -90, -49)
AMIGA_SLIDE_SCALE = tuple(min(255, max(1, round(
    48 * 256 / (math.log(2) * (1712.0 * 2 ** ((60 - note) / 12)))
))) for note in range(129))


class ReferencePlayer:
    def __init__(self, song: Song):
        self.song = song
        self.channels = [Channel() for _ in range(VOICES)]
        self.order = 0
        self.row = 0
        self.tick = 0
        self.speed = song.speed
        self.bpm = song.bpm
        self.remainder = 0

    def _remember(self, channel: Channel) -> None:
        parameter = channel.parameter
        if channel.effect == 1:
            channel.slide_up = parameter or channel.slide_up
            channel.parameter = channel.slide_up
        elif channel.effect == 2:
            channel.slide_down = parameter or channel.slide_down
            channel.parameter = channel.slide_down
        elif channel.effect == 3:
            channel.porta = parameter or channel.porta
            channel.parameter = channel.porta
        elif channel.effect == 4:
            channel.vibrato_speed = parameter >> 4 or channel.vibrato_speed
            channel.vibrato_depth = parameter & 15 or channel.vibrato_depth
            channel.parameter = channel.vibrato_speed << 4 | channel.vibrato_depth
        elif channel.effect == 0x0A:
            channel.volume_slide = parameter or channel.volume_slide
            channel.parameter = channel.volume_slide

    def _row(self) -> None:
        cells = self.song.patterns[self.song.orders[self.order]][self.row]
        for channel in self.channels:
            channel.effect = channel.parameter = 0
        for index, cell in enumerate(cells):
            channel = self.channels[index]
            channel.effect, channel.parameter = cell.effect, cell.parameter
            self._remember(channel)
            if cell.instrument:
                channel.instrument = self.song.instruments[cell.instrument - 1]
                channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                channel.fadeout = 0xFFFF
            if cell.volume:
                channel.volume = cell.volume - 1
            if cell.effect == 0x0F and cell.parameter:
                if cell.parameter < 0x20:
                    self.speed = cell.parameter
                else:
                    self.bpm = cell.parameter
            if cell.note == 97:
                channel.key_on = False
                if not channel.instrument.volume_flags & ENV_ENABLED:
                    channel.gate = False
            elif cell.note:
                pitch = (cell.note + 11) << 8
                if cell.effect == 3 and channel.gate:
                    channel.target = pitch
                else:
                    channel.note = channel.target = pitch
                    channel.key_on = channel.gate = True
                    channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                    channel.fadeout = 0xFFFF
                    channel.volume = cell.volume - 1 if cell.volume else 64
                    channel.reset = True

    def _effects(self) -> None:
        if not self.tick:
            return
        for channel in self.channels:
            step = channel.parameter << 4
            if self.song.amiga_effects:
                step = channel.parameter * AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
            if channel.effect == 1:
                channel.note = min(0xFFFF, channel.note + step)
            elif channel.effect == 2:
                channel.note = max(1, channel.note - step)
            elif channel.effect == 3:
                if channel.note < channel.target:
                    channel.note = min(channel.target, channel.note + step)
                elif channel.note > channel.target:
                    channel.note = max(channel.target, channel.note - step)
            elif channel.effect == 4:
                channel.vibrato_phase = (channel.vibrato_phase + channel.vibrato_speed) & 63
            elif channel.effect == 0x0A:
                up, down = channel.parameter >> 4, channel.parameter & 15
                channel.volume = min(64, channel.volume + up) if up else max(0, channel.volume - down)
            elif channel.effect == 0x0E and channel.parameter >> 4 == 9:
                interval = channel.parameter & 15
                if interval and self.tick % interval == 0:
                    channel.volume_position = channel.pitch_position = 0
                    channel.reset = True

    @staticmethod
    def _advance(position: int, length: int, loop: int) -> int:
        if position + 1 < length:
            return position + 1
        return loop if loop != 0xFF else position

    @staticmethod
    def _advance_volume(channel: Channel) -> None:
        instrument = channel.instrument
        if not instrument.volume_flags & ENV_ENABLED:
            return
        if (channel.key_on and instrument.volume_flags & ENV_SUSTAIN
                and channel.volume_position == instrument.volume_sustain):
            return
        channel.volume_tick += 1
        if channel.volume_tick < instrument.volume_step:
            return
        channel.volume_tick = 0
        position = channel.volume_position + 1
        if (instrument.volume_flags & ENV_LOOP
                and position > instrument.volume_loop_end):
            channel.volume_position = instrument.volume_loop_start
        elif position < len(instrument.volume_macro):
            channel.volume_position = position

    def step(self) -> TickFrame:
        frame_order, frame_row, frame_tick = self.order, self.row, self.tick
        if not self.tick:
            self._row()
        self._effects()
        voices = []
        pcm_triggers = []
        for channel in self.channels:
            instrument = channel.instrument
            pitch = channel.note + instrument.relative_pitch
            pitch += instrument.pitch_macro[channel.pitch_position] * 64
            if channel.effect == 0 and channel.parameter:
                shift = (0, channel.parameter >> 4, channel.parameter & 15)[self.tick % 3]
                pitch += shift << 8
            elif channel.effect == 4 and channel.gate:
                if self.song.amiga_effects:
                    scale = AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
                    amplitude = (scale * channel.vibrato_depth + 1) // 2
                    pitch += SINE[channel.vibrato_phase >> 2] * ((amplitude + 4) >> 3) >> 4
                else:
                    pitch += SINE[channel.vibrato_phase >> 2] * channel.vibrato_depth // 16
            level = (channel.volume * instrument.gain + 32) >> 6
            volume = (level * instrument.volume_macro[channel.volume_position] + 16) >> 5
            if not channel.key_on:
                volume = volume * channel.fadeout >> 16
            output_volume = min(31, volume) if channel.gate else 0
            if MODE_PCM_BASE <= instrument.mode < MODE_NOISE_LONG:
                trigger = (instrument.mode - MODE_PCM_BASE, output_volume)
                if channel.reset and output_volume:
                    pcm_triggers.append(trigger)
                output_volume = 0
            voices.append(VoiceFrame(max(1, min(0xFFFF, pitch)), output_volume,
                                     instrument.mode, channel.reset))
            channel.reset = False
            self._advance_volume(channel)
            channel.pitch_position = self._advance(channel.pitch_position, len(instrument.pitch_macro),
                                                   instrument.pitch_loop)
            if not channel.key_on and instrument.volume_flags & ENV_ENABLED:
                channel.fadeout = max(0, channel.fadeout - instrument.fadeout)
                if not channel.fadeout:
                    channel.gate = False
        numerator = self.remainder + 80000
        wait = numerator // self.bpm
        self.remainder = numerator - wait * self.bpm
        self.tick += 1
        if self.tick >= self.speed:
            self.tick = 0
            self.row += 1
            pattern = self.song.patterns[self.song.orders[self.order]]
            if self.row >= len(pattern):
                self.row = 0
                self.order += 1
                if self.order >= len(self.song.orders):
                    self.order = self.song.restart
        return TickFrame(wait, tuple(voices), frame_order, frame_row, frame_tick,
                         tuple(pcm_triggers))
