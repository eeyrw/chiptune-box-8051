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
    pcm_triggers: tuple[tuple[int, int, int], ...] = ()


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
    fadeout: int = 0x8000
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
        self.next_order: int | None = None
        self.next_row = 0
        self.pattern_delay = 0
        self.hold_row = False
        self.loop_start = 0
        self.loop_count = 0
        self.loop_jump = False
        self.note_delay_pending = [False] * VOICES
        self.note_delay_remain = [0] * VOICES
        self.delayed_note = [0] * VOICES
        self.delayed_instrument = [0] * VOICES
        self.delayed_volume = [None] * VOICES
        self.sample_offset = [0] * VOICES
        self.use_sample_offset = [False] * VOICES

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
        elif channel.effect in (4, 7):
            channel.vibrato_speed = parameter >> 4 or channel.vibrato_speed
            channel.vibrato_depth = parameter & 15 or channel.vibrato_depth
            channel.parameter = channel.vibrato_speed << 4 | channel.vibrato_depth
        elif channel.effect in (5, 6, 0x0A):
            channel.volume_slide = parameter or channel.volume_slide
            channel.parameter = channel.volume_slide

    def _row(self) -> None:
        cells = self.song.patterns[self.song.orders[self.order]][self.row]
        self.next_order = None
        self.next_row = 0
        self.loop_jump = False
        self.pattern_delay = 0
        for channel in self.channels:
            channel.effect = channel.parameter = 0
        for index, cell in enumerate(cells):
            channel = self.channels[index]
            channel.effect, channel.parameter = cell.effect, cell.parameter
            self._remember(channel)
            note_delay_preview = (
                (cell.parameter & 15)
                if (cell.effect == 0x0E and cell.parameter >> 4 == 0x0D) else 0)
            delay_note_preview = bool(
                cell.note and cell.note != 97
                and not (cell.effect in (3, 5) and channel.gate)
                and note_delay_preview > 0)
            if cell.instrument and not delay_note_preview:
                channel.instrument = self.song.instruments[cell.instrument - 1]
                channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                channel.fadeout = 0x8000
            if cell.volume and not delay_note_preview:
                channel.volume = cell.volume - 1
            if cell.effect == 0x0F and cell.parameter:
                if cell.parameter < 0x20:
                    self.speed = cell.parameter
                else:
                    self.bpm = max(32, min(999, cell.parameter))
            if cell.effect == 0x0B:
                self.next_order = cell.parameter
            elif cell.effect == 0x0D:
                self.next_row = (cell.parameter >> 4) * 10 + (cell.parameter & 15)
                if self.next_order is None:
                    self.next_order = self.order + 1
            elif cell.effect == 0x0E:
                sub, arg = cell.parameter >> 4, cell.parameter & 15
                if sub == 0x0E:
                    self.pattern_delay = arg
                elif sub == 0x06:
                    if arg == 0:
                        self.loop_start = self.row
                    elif self.loop_count == 0:
                        self.loop_count = arg
                        self.loop_jump = True
                    else:
                        self.loop_count -= 1
                        if self.loop_count:
                            self.loop_jump = True
            self.note_delay_pending[index] = False
            if cell.effect == 9:
                if cell.parameter:
                    self.sample_offset[index] = cell.parameter
                self.use_sample_offset[index] = True
            elif cell.note and cell.note != 97 and not (cell.effect in (3, 5) and channel.gate):
                self.use_sample_offset[index] = False
            note_delay = (cell.parameter & 15) if (cell.effect == 0x0E and cell.parameter >> 4 == 0x0D) else 0
            delay_note = bool(cell.note and cell.note != 97
                              and not (cell.effect in (3, 5) and channel.gate)
                              and note_delay > 0)
            if cell.note == 97:
                if note_delay > 0:
                    self.note_delay_pending[index] = True
                    self.note_delay_remain[index] = note_delay
                    self.delayed_note[index] = 97
                    self.delayed_instrument[index] = 0
                    self.delayed_volume[index] = None
                else:
                    channel.key_on = False
                    if not channel.instrument.volume_flags & ENV_ENABLED:
                        channel.gate = False
            elif cell.note:
                pitch = (cell.note + 11) << 8
                if cell.effect in (3, 5) and channel.gate:
                    channel.target = pitch
                elif delay_note:
                    self.note_delay_pending[index] = True
                    self.note_delay_remain[index] = note_delay
                    self.delayed_note[index] = cell.note
                    self.delayed_instrument[index] = cell.instrument
                    self.delayed_volume[index] = (cell.volume - 1) if cell.volume else None
                    # undo eager instrument/volume if we applied above for delayed notes
                else:
                    channel.note = channel.target = pitch
                    channel.key_on = channel.gate = True
                    channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                    channel.fadeout = 0x8000
                    if not cell.volume and cell.instrument:
                        channel.volume = 64
                    channel.reset = True
            if cell.effect == 0x0E:
                sub, arg = cell.parameter >> 4, cell.parameter & 15
                # Device reuses slide_up/slide_down/volume_slide for fine memory.
                if sub == 0x01:
                    if arg:
                        channel.slide_up = arg
                    amount = channel.slide_up
                    if amount:
                        step = amount << 4
                        if self.song.amiga_effects:
                            step = amount * AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
                        channel.note = min(0xFFFF, channel.note + step)
                elif sub == 0x02:
                    if arg:
                        channel.slide_down = arg
                    amount = channel.slide_down
                    if amount:
                        step = amount << 4
                        if self.song.amiga_effects:
                            step = amount * AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
                        channel.note = max(1, channel.note - step)
                elif sub == 0x0A:
                    if arg:
                        channel.volume_slide = (arg << 4) | (channel.volume_slide & 15)
                    amount = channel.volume_slide >> 4
                    if amount:
                        channel.volume = min(64, channel.volume + amount)
                elif sub == 0x0B:
                    if arg:
                        channel.volume_slide = (channel.volume_slide & 0xF0) | arg
                    amount = channel.volume_slide & 15
                    if amount:
                        channel.volume = max(0, channel.volume - amount)
            if cell.effect == 0x0E and cell.parameter == 0xC0:
                channel.key_on = channel.gate = False


    def _service_note_delays(self) -> None:
        """Match device: remain counts down each tick; fire when remain hits 0."""
        for index, channel in enumerate(self.channels):
            if not self.note_delay_pending[index]:
                continue
            if self.note_delay_remain[index] != 0:
                self.note_delay_remain[index] -= 1
                continue
            self.note_delay_pending[index] = False
            note = self.delayed_note[index]
            instrument = self.delayed_instrument[index]
            volume = self.delayed_volume[index]
            if instrument:
                channel.instrument = self.song.instruments[instrument - 1]
                channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                channel.fadeout = 0x8000
            if volume is not None:
                channel.volume = volume
            if note == 97:
                channel.key_on = False
                if not channel.instrument.volume_flags & ENV_ENABLED:
                    channel.gate = False
            else:
                pitch = (note + 11) << 8
                channel.note = channel.target = pitch
                channel.key_on = channel.gate = True
                channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                channel.fadeout = 0x8000
                if volume is None and instrument:
                    channel.volume = 64
                channel.reset = True

    def _effects(self) -> None:
        if not self.tick:
            return
        for channel in self.channels:
            def pitch_step(amount: int) -> int:
                step = amount << 4
                if self.song.amiga_effects:
                    step = amount * AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
                return step
            if channel.effect == 1:
                channel.note = min(0xFFFF, channel.note + pitch_step(channel.parameter))
            elif channel.effect == 2:
                channel.note = max(1, channel.note - pitch_step(channel.parameter))
            elif channel.effect in (3, 5):
                step = pitch_step(channel.porta)
                if channel.note < channel.target:
                    channel.note = min(channel.target, channel.note + step)
                elif channel.note > channel.target:
                    channel.note = max(channel.target, channel.note - step)
                if channel.effect == 5:
                    up, down = channel.parameter >> 4, channel.parameter & 15
                    channel.volume = min(64, channel.volume + up) if up else max(0, channel.volume - down)
            elif channel.effect == 4:
                channel.vibrato_phase = (channel.vibrato_phase + channel.vibrato_speed) & 63
            elif channel.effect == 6:
                channel.vibrato_phase = (channel.vibrato_phase + channel.vibrato_speed) & 63
                up, down = channel.parameter >> 4, channel.parameter & 15
                channel.volume = min(64, channel.volume + up) if up else max(0, channel.volume - down)
            elif channel.effect == 7:
                channel.vibrato_phase = (channel.vibrato_phase + channel.vibrato_speed) & 63
            elif channel.effect == 0x0A:
                up, down = channel.parameter >> 4, channel.parameter & 15
                channel.volume = min(64, channel.volume + up) if up else max(0, channel.volume - down)
            elif channel.effect == 0x0E and channel.parameter >> 4 == 9:
                interval = channel.parameter & 15
                if interval and self.tick % interval == 0:
                    channel.volume_position = channel.pitch_position = channel.volume_tick = 0
                    channel.fadeout = 0x8000
                    channel.key_on = channel.gate = True
                    channel.reset = True
            elif channel.effect == 0x0E and channel.parameter >> 4 == 0x0C:
                if self.tick == channel.parameter & 15:
                    channel.key_on = channel.gate = False

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
            if self.hold_row:
                self.hold_row = False
            else:
                self._row()
        self._service_note_delays()
        self._effects()
        voices = []
        pcm_triggers = []
        for channel_index, channel in enumerate(self.channels):
            instrument = channel.instrument
            pitch = channel.note + instrument.relative_pitch
            pitch += instrument.pitch_macro[channel.pitch_position] * 64
            if channel.effect == 0 and channel.parameter:
                shift = (0, channel.parameter >> 4, channel.parameter & 15)[self.tick % 3]
                pitch += shift << 8
            elif channel.effect in (4, 6) and channel.gate:
                if self.song.amiga_effects:
                    scale = AMIGA_SLIDE_SCALE[min(128, channel.note >> 8)]
                    amplitude = (scale * channel.vibrato_depth + 1) // 2
                    pitch += SINE[channel.vibrato_phase >> 2] * ((amplitude + 4) >> 3) >> 4
                else:
                    pitch += SINE[channel.vibrato_phase >> 2] * channel.vibrato_depth // 16
            level = channel.volume * instrument.gain * instrument.volume_macro[channel.volume_position]
            volume = (level + 256) >> 9
            if not channel.key_on:
                volume = volume * channel.fadeout >> 15
            output_volume = min(127, volume) if channel.gate else 0
            if channel.effect == 7 and channel.gate and output_volume:
                trem = SINE[channel.vibrato_phase >> 2] * channel.vibrato_depth // 16
                output_volume = max(0, min(127, output_volume + trem))
            if MODE_PCM_BASE <= instrument.mode < MODE_NOISE_LONG:
                pcm_volume = min(31, (output_volume + 2) >> 2)
                skip = (self.sample_offset[channel_index] << 8
                        if self.use_sample_offset[channel_index] else 0)
                trigger = (instrument.mode - MODE_PCM_BASE, pcm_volume, skip)
                if channel.reset and pcm_volume:
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
            if self.pattern_delay:
                self.pattern_delay -= 1
                self.hold_row = True
            elif self.loop_jump:
                self.loop_jump = False
                self.next_order = None
                self.next_row = 0
                self.row = self.loop_start
            elif self.next_order is not None:
                self.order = (self.next_order if self.next_order < len(self.song.orders)
                              else self.song.restart)
                self.row = self.next_row
                self.next_order = None
                self.next_row = 0
                self.loop_start = 0
                self.loop_count = 0
                self.loop_jump = False
            else:
                self.row += 1
                pattern = self.song.patterns[self.song.orders[self.order]]
                if self.row >= len(pattern):
                    self.row = 0
                    self.order += 1
                    if self.order >= len(self.song.orders):
                        self.order = self.song.restart
                    self.loop_start = 0
                    self.loop_count = 0
                    self.loop_jump = False
        return TickFrame(wait, tuple(voices), frame_order, frame_row, frame_tick,
                         tuple(pcm_triggers))
