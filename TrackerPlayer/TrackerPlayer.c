#include <stddef.h>
#include <string.h>
#include "TrackerPlayer.h"
#include "ScoreFlash.h"

#define T10P_HEADER_SIZE 16UL
#define T10P_ENTRY_SIZE 8UL
#define T10M_HEADER_SIZE 48UL
#define T10_PATTERN_ENTRY_SIZE 8UL
#define T10_INSTRUMENT_SIZE 48UL
#define T10_RESOURCE_HEADER_SIZE 8UL
#define T10_PCM_ENTRY_SIZE 8UL
#define T10_WAVE_SIZE 16UL
#define T10_PCM_RATE 16000U
#define T10_SAMPLE_RATE 32000UL

#define CELL_NOTE 0x01
#define CELL_INSTRUMENT 0x02
#define CELL_VOLUME 0x04
#define CELL_EFFECT 0x08

#define T10_TRACK_LOOP 0x01
#define T10_TRACK_AMIGA_EFFECTS 0x02

MEM_XDATA(TrackerControlQueue) trackerQueue;
volatile uint8_t trackerLastError;
extern MEM_CODE(unsigned char) Score[];
extern MEM_CODE(uint32_t) ScoreSize;

static MEM_XDATA(TrackerControlEvent) decodeEvent;

typedef char TrackerEventSizeMustMatchAsm[(sizeof(TrackerControlEvent) == 57) ? 1 : -1];
typedef char TrackerQueueHeadMustMatchAsm[(offsetof(TrackerControlQueue, head) == 228) ? 1 : -1];
typedef char TrackerInstrumentSizeMustMatchDisk[(sizeof(TrackerInstrumentState) == 48) ? 1 : -1];
/* +20 B from 10x 48-byte instruments (was 46). Keep AudioRender.inc in sync. */
typedef char TrackerVmStatusMustMatchAsm[(offsetof(TrackerPlayer, vm)
                                        + offsetof(TrackerVm, status) == 0x31e) ? 1 : -1];
static MEM_CODE(int8_t) vibratoSine[16] = {
      0,  49,  90, 117, 127, 117,  90,  49,
      0, -49, -90,-117,-127,-117, -90, -49
};
static MEM_CODE(uint8_t) amigaSlideScale[129] = {
      1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,
      1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  1,  2,  2,  2,  2,  2,
      2,  2,  2,  2,  3,  3,  3,  3,  3,  3,  4,  4,  4,  4,  5,  5,
      5,  5,  6,  6,  7,  7,  7,  8,  8,  9,  9, 10, 10, 11, 12, 12,
     13, 14, 15, 16, 16, 17, 18, 20, 21, 22, 23, 25, 26, 28, 29, 31,
     33, 35, 37, 39, 41, 44, 46, 49, 52, 55, 59, 62, 66, 70, 74, 78,
     83, 88, 93, 99,104,111,117,124,132,139,148,156,166,176,186,197,
    209,221,234,248,255,255,255,255,255,255,255,255,255,255,255,255,
    255
};

static uint8_t queue_full(void)
{
    return (uint8_t)(trackerQueue.tail - trackerQueue.head) >= TRACKER_QUEUE_LENGTH;
}

static void queue_reset(void)
{
    PlatformIrqState irq;
    Platform_IrqSave(irq);
    trackerQueue.head = trackerQueue.tail = 0;
    trackerQueue.countdown = 0;
    trackerQueue.underruns = 0;
    Platform_IrqRestore(irq);
}

static uint8_t queue_commit(MEM_XDATA(TrackerControlEvent) *event)
{
    uint8_t tail;
    if (queue_full()) return 0;
    tail = trackerQueue.tail;
    memcpy(&trackerQueue.slots[tail & (TRACKER_QUEUE_LENGTH - 1)], event, sizeof(*event));
    trackerQueue.tail = tail + 1;
    return 1;
}

/* Pattern stream is host-bounded; only fail past patternEnd. */
static uint8_t read8(MEM_XDATA(TrackerVm) *vm, uint8_t *value)
{
    if (vm->patternPos >= vm->patternEnd) return 0;
    *value = score_u8(vm->patternPos++);
    return 1;
}

static uint8_t read16(MEM_XDATA(TrackerVm) *vm, uint16_t *value)
{
    uint8_t lo;
    if (!read8(vm, &lo) || vm->patternPos >= vm->patternEnd) return 0;
    *value = (uint16_t)lo | ((uint16_t)score_u8(vm->patternPos++) << 8);
    return 1;
}

#define track_u8(vm, off)  score_u8((uint16_t)((vm)->trackBase + (uint16_t)(off)))
#define track_u16(vm, off) score_u16((uint16_t)((vm)->trackBase + (uint16_t)(off)))
#define track_u32(vm, off) score_u32((uint16_t)((vm)->trackBase + (uint16_t)(off)))

static void default_instrument(MEM_XDATA(TrackerInstrumentState) *instrument)
{
    memset(instrument, 0, sizeof(*instrument));
    instrument->gain = 20;
    instrument->volumeLength = instrument->volumeStep = instrument->pitchLength = 1;
    instrument->volumeSustain = instrument->volumeLoopStart = instrument->volumeLoopEnd = 0xFF;
    instrument->volumeMacro[0] = 32;
}

/* Host compile validates instrument records; runtime only bounds the index. */
static uint8_t load_instrument(MEM_XDATA(TrackerVm) *vm, uint8_t number,
                               MEM_XDATA(TrackerInstrumentState) *instrument)
{
    uint16_t addr;
    uint8_t n;
    if (!number) {
        default_instrument(instrument);
        return 1;
    }
    if (number > vm->instrumentCount) return 0;
    n = (uint8_t)(number - 1);
    /* 48 = 32+16: avoid SDCC __mulint. */
    addr = (uint16_t)(vm->trackBase + vm->instrumentOffset + ((uint16_t)n << 5)
                    + ((uint16_t)n << 4));
    score_copy48_xdata(addr, (uint8_t __xdata *)instrument);
    return 1;
}

static uint8_t load_pattern(MEM_XDATA(TrackerVm) *vm, uint8_t pattern)
{
    uint32_t entry, offset;
    uint16_t size;
    if (pattern >= vm->patternCount) { trackerLastError = 0x91; return 0; }
    entry = vm->patternDirectoryOffset + ((uint32_t)pattern << 3);
    offset = track_u32(vm, (uint16_t)(entry));
    size = track_u16(vm, (uint16_t)(entry + 4));
    vm->patternRows = track_u16(vm, (uint16_t)(entry + 6));
    if (!vm->patternRows || !size) { trackerLastError = 0x92; return 0; }
    vm->patternPos = (uint16_t)(vm->trackBase + offset);
    vm->patternEnd = (uint16_t)(vm->patternPos + size);
    vm->row = 0;
    return 1;
}

static uint8_t load_order(MEM_XDATA(TrackerVm) *vm)
{
    uint32_t orderOffset = track_u32(vm, (uint16_t)(12));
    return load_pattern(vm, track_u8(vm, (uint16_t)(orderOffset + vm->order)));
}

static uint8_t seek_pattern_row(MEM_XDATA(TrackerVm) *vm, uint8_t target)
{
    static MEM_XDATA(uint8_t) mask;
    static MEM_XDATA(uint8_t) channel;
    static MEM_XDATA(uint16_t) changed;
    static MEM_XDATA(uint8_t) fields;
    while (vm->row < target) {
        if (!read16(vm, &changed)) { trackerLastError = 0x81; return 0; }
        for (channel = 0; channel < WT_VOICE_COUNT; channel++) {
            if (!(changed & ((uint16_t)1 << channel))) continue;
            if (!read8(vm, &mask)) { trackerLastError = 0x82; return 0; }
            fields = ((mask & CELL_NOTE) ? 1 : 0) + ((mask & CELL_INSTRUMENT) ? 1 : 0)
                   + ((mask & CELL_VOLUME) ? 1 : 0) + ((mask & CELL_EFFECT) ? 2 : 0);
            while (fields--) {
                if (!read8(vm, &mask)) { trackerLastError = 0x86; return 0; }
            }
        }
        vm->row++;
    }
    return 1;
}

static void remember_effect(MEM_XDATA(TrackerChannelState) *channel)
{
    uint8_t parameter = channel->parameter;
    uint8_t effect = channel->effect;
    if (effect == 1) {
        if (parameter) channel->slideUp = parameter;
        channel->parameter = channel->slideUp;
    } else if (effect == 2) {
        if (parameter) channel->slideDown = parameter;
        channel->parameter = channel->slideDown;
    } else if (effect == 3) {
        if (parameter) channel->portaSpeed = parameter;
        channel->parameter = channel->portaSpeed;
    } else if (effect == 4 || effect == 7) {
        if (parameter >> 4) channel->vibratoSpeed = parameter >> 4;
        if (parameter & 15) channel->vibratoDepth = parameter & 15;
        channel->parameter = (uint8_t)((channel->vibratoSpeed << 4) | channel->vibratoDepth);
    } else if (effect == 5 || effect == 6 || effect == 0x0A) {
        if (parameter) channel->volumeSlide = parameter;
        channel->parameter = channel->volumeSlide;
    }
}

static void reset_pattern_loop(MEM_XDATA(TrackerVm) *vm)
{
    vm->loopStart = 0;
    vm->loopCount = 0;
    vm->loopJump = 0;
}

static uint16_t pitch_step(MEM_XDATA(TrackerVm) *vm, uint8_t index, uint8_t amount)
{
    static MEM_XDATA(uint8_t) noteIndex;
    if (!amount) return 0;
    if (!vm->amigaEffects) return (uint16_t)amount << 4;
    noteIndex = (uint8_t)(vm->channel[index].note >> 8);
    if (noteIndex > 128) noteIndex = 128;
    return (uint16_t)amount * amigaSlideScale[noteIndex];
}

static void note_add(MEM_XDATA(TrackerChannelState) *channel, uint16_t step)
{
    channel->note = channel->note > 0xFFFFU - step ? 0xFFFFU : channel->note + step;
}

static void note_sub(MEM_XDATA(TrackerChannelState) *channel, uint16_t step)
{
    channel->note = channel->note > step ? channel->note - step : 1;
}

static void tone_porta(MEM_XDATA(TrackerChannelState) *channel, uint16_t step)
{
    if (channel->note < channel->target)
        channel->note = channel->target - channel->note < step ? channel->target : channel->note + step;
    else if (channel->note > channel->target)
        channel->note = channel->note - channel->target < step ? channel->target : channel->note - step;
}

static void volume_slide(MEM_XDATA(TrackerChannelState) *channel, uint8_t parameter)
{
    uint8_t up = parameter >> 4;
    uint8_t down = parameter & 15;
    if (up) channel->volume = channel->volume + up > 64 ? 64 : channel->volume + up;
    else channel->volume = channel->volume > down ? channel->volume - down : 0;
}

static void start_note(MEM_XDATA(TrackerChannelState) *channel, uint16_t pitch, uint8_t resetVol)
{
    channel->note = pitch;
    channel->target = pitch;
    channel->keyOn = 1;
    channel->gate = 1;
    channel->volumePosition = 0;
    channel->volumeTick = 0;
    channel->pitchPosition = 0;
    channel->fadeout = 0x8000;
    if (resetVol) channel->volume = 64;
}

static void service_note_delays(MEM_XDATA(TrackerVm) *vm)
{
    static MEM_XDATA(uint8_t) delayIndex;
    static MEM_XDATA(uint16_t) delayBit;
    static TrackerChannelState __xdata * __xdata delayChannel;
    if (!vm->noteDelayMask) return;
    for (delayIndex = 0; delayIndex < WT_VOICE_COUNT; delayIndex++) {
        delayBit = (uint16_t)1 << delayIndex;
        if (!(vm->noteDelayMask & delayBit)) continue;
        if (vm->noteDelayRemain[delayIndex]) {
            vm->noteDelayRemain[delayIndex]--;
            continue;
        }
        vm->noteDelayMask &= (uint16_t)~delayBit;
        delayChannel = &vm->channel[delayIndex];
        if (vm->delayedInstrument[delayIndex]) {
            if (!load_instrument(vm, vm->delayedInstrument[delayIndex],
                                 &delayChannel->definition)) {
                trackerLastError = 0x88;
                return;
            }
            delayChannel->instrument = vm->delayedInstrument[delayIndex];
            delayChannel->volumePosition = 0;
            delayChannel->volumeTick = 0;
            delayChannel->pitchPosition = 0;
            delayChannel->fadeout = 0x8000;
        }
        if (vm->delayedVolume[delayIndex] != 0xFF)
            delayChannel->volume = vm->delayedVolume[delayIndex];
        if (vm->delayedNote[delayIndex] == 97) {
            delayChannel->keyOn = 0;
            if (!(delayChannel->definition.volumeFlags & TRACKER_ENV_ENABLED))
                delayChannel->gate = 0;
            continue;
        }
        start_note(delayChannel, (uint16_t)(vm->delayedNote[delayIndex] + 11) << 8,
                   (uint8_t)(vm->delayedVolume[delayIndex] == 0xFF
                             && vm->delayedInstrument[delayIndex]));
        vm->pendingReset |= delayBit;
    }
}

static uint8_t decode_row(MEM_XDATA(TrackerVm) *vm)
{
    static MEM_XDATA(uint16_t) changed;
    static MEM_XDATA(uint16_t) bit;
    static MEM_XDATA(uint8_t) channelIndex;
    static MEM_XDATA(uint8_t) mask;
    static MEM_XDATA(uint8_t) note;
    static MEM_XDATA(uint8_t) instrument;
    static MEM_XDATA(uint8_t) volume;
    static MEM_XDATA(uint8_t) effect;
    static MEM_XDATA(uint8_t) parameter;
    static MEM_XDATA(uint8_t) sub;
    static MEM_XDATA(uint8_t) arg;
    static MEM_XDATA(uint8_t) noteDelay;
    static MEM_XDATA(uint8_t) hasVolume;
    static MEM_XDATA(uint8_t) porta;
    static MEM_XDATA(uint16_t) pitch;
    static TrackerChannelState __xdata * __xdata channel;

    vm->nextOrder = vm->order + 1;
    vm->nextRow = 0;
    vm->flowPending = 0;
    vm->loopJump = 0;
    vm->patternDelay = 0;
    vm->noteDelayMask = 0;
    for (channelIndex = 0; channelIndex < WT_VOICE_COUNT; channelIndex++) {
        vm->channel[channelIndex].effect = 0;
        vm->channel[channelIndex].parameter = 0;
    }
    if (!read16(vm, &changed)) { trackerLastError = 0x81; return 0; }
    bit = 1;
    for (channelIndex = 0; channelIndex < WT_VOICE_COUNT; channelIndex++, bit <<= 1) {
        if (!(changed & bit)) continue;
        if (!read8(vm, &mask)) { trackerLastError = 0x82; return 0; }
        note = instrument = volume = effect = parameter = 0;
        if ((mask & CELL_NOTE) && !read8(vm, &note)) { trackerLastError = 0x83; return 0; }
        if ((mask & CELL_INSTRUMENT) && !read8(vm, &instrument)) { trackerLastError = 0x84; return 0; }
        if ((mask & CELL_VOLUME) && !read8(vm, &volume)) { trackerLastError = 0x85; return 0; }
        if ((mask & CELL_EFFECT) && (!read8(vm, &effect) || !read8(vm, &parameter))) {
            trackerLastError = 0x86;
            return 0;
        }

        channel = &vm->channel[channelIndex];
        channel->effect = effect;
        channel->parameter = parameter;
        remember_effect(channel);
        hasVolume = (mask & CELL_VOLUME) ? 1 : 0;
        porta = (effect == 3 || effect == 5) && channel->gate;
        noteDelay = (effect == 0x0E && (parameter >> 4) == 0x0D) ? (uint8_t)(parameter & 15) : 0;

        if (effect == 0x0F && parameter) {
            if (parameter < 0x20) vm->speed = parameter;
            else {
                vm->bpm = parameter;
                if (vm->bpm < 32) vm->bpm = 32;
                if (vm->bpm > 999) vm->bpm = 999;
            }
        }
        if (effect == 0x09) {
            if (parameter) vm->sampleOffset[channelIndex] = parameter;
            vm->useSampleOffsetMask |= bit;
        } else if (note && note != 97 && !porta) {
            vm->useSampleOffsetMask &= (uint16_t)~bit;
        }
        if (effect == 0x0B) {
            vm->nextOrder = parameter;
            vm->flowPending = 1;
        } else if (effect == 0x0D) {
            vm->nextRow = (uint8_t)((parameter >> 4) * 10 + (parameter & 15));
            vm->flowPending = 1;
        } else if (effect == 0x0E) {
            sub = (uint8_t)(parameter >> 4);
            arg = (uint8_t)(parameter & 15);
            if (sub == 0x0E) vm->patternDelay = arg;
            else if (sub == 0x06) {
                if (!arg) {
                    if (vm->row > 255) { trackerLastError = 0x95; return 0; }
                    vm->loopStart = (uint8_t)vm->row;
                } else if (!vm->loopCount) {
                    vm->loopCount = arg;
                    vm->loopJump = 1;
                } else if (--vm->loopCount) {
                    vm->loopJump = 1;
                }
            }
        }

        if (note && note != 97 && noteDelay && !porta) {
            vm->noteDelayMask |= bit;
            vm->noteDelayRemain[channelIndex] = noteDelay;
            vm->delayedNote[channelIndex] = note;
            vm->delayedInstrument[channelIndex] = instrument;
            vm->delayedVolume[channelIndex] = hasVolume ? volume : 0xFF;
        } else {
            if (instrument) {
                if (!load_instrument(vm, instrument, &channel->definition)) {
                    trackerLastError = 0x88;
                    return 0;
                }
                channel->instrument = instrument;
                channel->volumePosition = channel->volumeTick = channel->pitchPosition = 0;
                channel->fadeout = 0x8000;
            }
            if (hasVolume) channel->volume = volume;
            if (note == 97) {
                if (noteDelay) {
                    vm->noteDelayMask |= bit;
                    vm->noteDelayRemain[channelIndex] = noteDelay;
                    vm->delayedNote[channelIndex] = 97;
                    vm->delayedInstrument[channelIndex] = 0;
                    vm->delayedVolume[channelIndex] = 0xFF;
                } else {
                    channel->keyOn = 0;
                    if (!(channel->definition.volumeFlags & TRACKER_ENV_ENABLED)) channel->gate = 0;
                }
            } else if (note) {
                pitch = (uint16_t)(note + 11) << 8;
                if (porta) channel->target = pitch;
                else {
                    start_note(channel, pitch, (uint8_t)(!hasVolume && instrument));
                    vm->pendingReset |= bit;
                }
            }
        }

        if (effect == 0x0E) {
            sub = (uint8_t)(parameter >> 4);
            arg = (uint8_t)(parameter & 15);
            if (sub == 1) {
                if (arg) channel->slideUp = arg;
                pitch = pitch_step(vm, channelIndex, channel->slideUp);
                if (pitch) note_add(channel, pitch);
            } else if (sub == 2) {
                if (arg) channel->slideDown = arg;
                pitch = pitch_step(vm, channelIndex, channel->slideDown);
                if (pitch) note_sub(channel, pitch);
            } else if (sub == 0x0A) {
                if (arg) channel->volumeSlide = (uint8_t)((arg << 4)
                    | (channel->volumeSlide & 15));
                volume_slide(channel, (uint8_t)(channel->volumeSlide & 0xF0));
            } else if (sub == 0x0B) {
                if (arg) channel->volumeSlide = (uint8_t)((channel->volumeSlide & 0xF0) | arg);
                volume_slide(channel, (uint8_t)(channel->volumeSlide & 15));
            } else if (parameter == 0xC0) {
                channel->keyOn = 0;
                channel->gate = 0;
            }
        }
    }
    return 1;
}

static void apply_tick_effects(MEM_XDATA(TrackerVm) *vm)
{
    static MEM_XDATA(uint8_t) i;
    static MEM_XDATA(uint8_t) interval;
    static MEM_XDATA(uint16_t) step;
    static TrackerChannelState __xdata * __xdata channel;
    if (!vm->tick) return;
    for (i = 0; i < WT_VOICE_COUNT; i++) {
        channel = &vm->channel[i];
        switch (channel->effect) {
        case 1:
            step = pitch_step(vm, i, channel->parameter);
            if (step) note_add(channel, step);
            break;
        case 2:
            step = pitch_step(vm, i, channel->parameter);
            if (step) note_sub(channel, step);
            break;
        case 3:
            step = pitch_step(vm, i, channel->portaSpeed);
            if (step) tone_porta(channel, step);
            break;
        case 5:
            step = pitch_step(vm, i, channel->portaSpeed);
            if (step) tone_porta(channel, step);
            volume_slide(channel, channel->parameter);
            break;
        case 4:
        case 7:
            channel->vibratoPhase = (uint8_t)((channel->vibratoPhase + channel->vibratoSpeed) & 63);
            break;
        case 6:
            channel->vibratoPhase = (uint8_t)((channel->vibratoPhase + channel->vibratoSpeed) & 63);
            volume_slide(channel, channel->parameter);
            break;
        case 0x0A:
            volume_slide(channel, channel->parameter);
            break;
        case 0x0E:
            interval = channel->parameter & 15;
            if ((channel->parameter >> 4) == 9 && interval && vm->tick % interval == 0) {
                channel->volumePosition = 0;
                channel->volumeTick = 0;
                channel->pitchPosition = 0;
                channel->fadeout = 0x8000;
                channel->keyOn = 1;
                channel->gate = 1;
                vm->pendingReset |= (uint16_t)1 << i;
            } else if ((channel->parameter >> 4) == 0x0C && vm->tick == interval) {
                channel->keyOn = 0;
                channel->gate = 0;
            }
            break;
        }
    }
}

static uint16_t output_pitch(MEM_XDATA(TrackerVm) *vm, uint8_t index)
{
    static TrackerChannelState __xdata * __xdata channel;
    static MEM_XDATA(int32_t) pitch;
    static MEM_XDATA(int16_t) vibrato;
    static MEM_XDATA(uint16_t) amplitude;
    static MEM_XDATA(uint8_t) position;
    static MEM_XDATA(uint8_t) noteIndex;
    channel = &vm->channel[index];
    pitch = (int32_t)channel->note + channel->definition.relativePitch;
    position = channel->pitchPosition;
    if (position >= channel->definition.pitchLength) position = channel->definition.pitchLength - 1;
    pitch += (int16_t)channel->definition.pitchMacro[position] << 6;
    if (channel->effect == 0 && channel->parameter) {
        if (vm->tick % 3 == 1) pitch += (uint16_t)(channel->parameter >> 4) << 8;
        else if (vm->tick % 3 == 2) pitch += (uint16_t)(channel->parameter & 15) << 8;
    } else if ((channel->effect == 4 || channel->effect == 6) && channel->gate) {
        if (vm->amigaEffects) {
            noteIndex = (uint8_t)(channel->note >> 8);
            if (noteIndex > 128) noteIndex = 128;
            amplitude = ((uint16_t)amigaSlideScale[noteIndex] * channel->vibratoDepth + 1) >> 1;
            vibrato = (int16_t)vibratoSine[channel->vibratoPhase >> 2]
                    * (int16_t)((amplitude + 4) >> 3);
            pitch += vibrato >> 4;
        } else {
            vibrato = (int16_t)vibratoSine[channel->vibratoPhase >> 2] * channel->vibratoDepth;
            pitch += vibrato >> 4;
        }
    }
    if (pitch < 1) return 1;
    if (pitch > 0xFFFFL) return 0xFFFF;
    return (uint16_t)pitch;
}

static void advance_macro(uint8_t *position, uint8_t length, uint8_t loop)
{
    if ((uint8_t)(*position + 1) < length) (*position)++;
    else if (loop != 0xFF) *position = loop;
}

static void advance_volume_envelope(MEM_XDATA(TrackerChannelState) *channel)
{
    MEM_XDATA(TrackerInstrumentState) *instrument = &channel->definition;
    uint8_t next;
    if (!(instrument->volumeFlags & TRACKER_ENV_ENABLED)) return;
    if (channel->keyOn && (instrument->volumeFlags & TRACKER_ENV_SUSTAIN)
     && channel->volumePosition == instrument->volumeSustain) return;
    channel->volumeTick++;
    if (channel->volumeTick < instrument->volumeStep) return;
    channel->volumeTick = 0;
    next = channel->volumePosition + 1;
    if ((instrument->volumeFlags & TRACKER_ENV_LOOP) && next > instrument->volumeLoopEnd)
        channel->volumePosition = instrument->volumeLoopStart;
    else if (next < instrument->volumeLength)
        channel->volumePosition = next;
}

static uint16_t next_tick_samples(MEM_XDATA(TrackerVm) *vm)
{
    uint32_t numerator = vm->timingRemainder + 80000UL;
    uint16_t result = (uint16_t)(numerator / vm->bpm);
    vm->timingRemainder = numerator - (uint32_t)result * vm->bpm;
    return result ? result : 1;
}

static uint8_t advance_song(MEM_XDATA(TrackerVm) *vm)
{
    vm->tick++;
    if (vm->tick < vm->speed) return 1;
    vm->tick = 0;
    if (vm->patternDelay) {
        vm->patternDelay--;
        vm->holdRow = 1;
        return 1;
    }
    if (vm->loopJump) {
        vm->loopJump = 0;
        vm->flowPending = 0;
        vm->holdRow = 0;
        if (!load_order(vm)) return 0;
        if (vm->loopStart >= vm->patternRows) { trackerLastError = 0x94; return 0; }
        return seek_pattern_row(vm, vm->loopStart);
    }
    if (vm->flowPending) {
        if (vm->nextOrder >= vm->orderCount) {
            if (!vm->loop) {
                vm->pendingEnd = 1;
                vm->flowPending = 0;
                return 1;
            }
            vm->nextOrder = vm->restartOrder;
        }
        vm->order = vm->nextOrder;
        reset_pattern_loop(vm);
        if (!load_order(vm)) return 0;
        if (vm->nextRow >= vm->patternRows) { trackerLastError = 0x94; return 0; }
        vm->flowPending = 0;
        return seek_pattern_row(vm, vm->nextRow);
    }
    vm->row++;
    if (vm->row < vm->patternRows) return 1;
    reset_pattern_loop(vm);
    vm->order++;
    if (vm->order >= vm->orderCount) {
        if (!vm->loop) {
            vm->pendingEnd = 1;
            return 1;
        }
        vm->order = vm->restartOrder;
    }
    return load_order(vm);
}

static uint8_t vm_next_event(MEM_XDATA(TrackerVm) *vm, MEM_XDATA(TrackerControlEvent) *event)
{
    static MEM_XDATA(uint8_t) i;
    static MEM_XDATA(uint8_t) volumePosition;
    static MEM_XDATA(uint16_t) bit;
    static MEM_XDATA(uint16_t) pitch;
    static MEM_XDATA(uint32_t) increment;
    static MEM_XDATA(uint32_t) pcmEntry;
    static MEM_XDATA(uint32_t) pcmAddress;
    static MEM_XDATA(uint16_t) pcmLength;
    static MEM_XDATA(uint16_t) pcmSkip;
    static TrackerChannelState __xdata * __xdata channel;
    static MEM_XDATA(TrackerVoiceControl) control;
    memset(event, 0, sizeof(*event));
    if (vm->pendingEnd) {
        event->terminal = 1;
        event->waitSamples = 1;
        return 1;
    }
    if (!vm->tick) {
        if (vm->holdRow) {
            vm->holdRow = 0;
        } else if (!decode_row(vm)) {
            return 0;
        }
    }
    service_note_delays(vm);
    if (trackerLastError) return 0;
    apply_tick_effects(vm);
    bit = 1;
    for (i = 0; i < WT_VOICE_COUNT; i++, bit <<= 1) {
        channel = &vm->channel[i];
        pitch = output_pitch(vm, i);
        increment = WavetablePitchToIncrement(pitch);
        control.increment[0] = (uint8_t)increment;
        control.increment[1] = (uint8_t)(increment >> 8);
        control.increment[2] = (uint8_t)(increment >> 16);
        volumePosition = channel->volumePosition;
        if (volumePosition >= channel->definition.volumeLength)
            volumePosition = channel->definition.volumeLength - 1;
        if (channel->gate) {
            /* Keep two fractional mixer bits so quiet XM voices survive. */
            uint16_t level = (uint16_t)channel->volume * channel->definition.gain
                           * channel->definition.volumeMacro[volumePosition];
            control.volume = (uint8_t)((level + 256U) >> 9);
            if (!channel->keyOn)
                control.volume = (uint8_t)(((uint16_t)control.volume
                                  * (channel->fadeout >> 7)) >> 8);
        } else control.volume = 0;
        if (control.volume > 127) control.volume = 127;
        if (channel->effect == 7 && channel->gate && control.volume) {
            int16_t trem = ((int16_t)vibratoSine[channel->vibratoPhase >> 2]
                            * channel->vibratoDepth) >> 4;
            if (trem >= 0)
                control.volume = control.volume + (uint8_t)trem > 127
                    ? 127 : (uint8_t)(control.volume + trem);
            else {
                trem = -trem;
                control.volume = control.volume > (uint8_t)trem
                    ? (uint8_t)(control.volume - trem) : 0;
            }
        }
        if (channel->definition.mode >= WT_MODE_PCM_BASE
         && channel->definition.mode < WT_MODE_NOISE_LONG) {
            control.volume = (control.volume + 2U) >> 2;
            if (control.volume > 31) control.volume = 31;
            if ((vm->pendingReset & bit) && control.volume) {
                pcmEntry = vm->pcmDirectoryOffset
                         + ((uint32_t)(channel->definition.mode - WT_MODE_PCM_BASE) << 3);
                pcmAddress = (uint32_t)vm->trackBase + track_u32(vm, (uint16_t)(pcmEntry));
                pcmLength = track_u16(vm, (uint16_t)(pcmEntry + 4));
                if (pcmAddress > 0xFFFFUL || !pcmLength) {
                    trackerLastError = 0x89;
                    return 0;
                }
                pcmSkip = (vm->useSampleOffsetMask & bit) ? ((uint16_t)vm->sampleOffset[i] << 8) : 0;
                if (pcmSkip >= pcmLength) {
                    control.volume = 0;
                } else {
                    pcmAddress += pcmSkip;
                    pcmLength = (uint16_t)(pcmLength - pcmSkip);
                    if (pcmAddress > 0xFFFFUL) {
                        trackerLastError = 0x89;
                        return 0;
                    }
                    control.increment[0] = (uint8_t)pcmAddress;
                    control.increment[1] = (uint8_t)(pcmAddress >> 8);
                    control.increment[2] = (uint8_t)pcmLength;
                    control.waveOffset = (uint8_t)(pcmLength >> 8);
                    control.volume |= 0xC0;
                }
            } else control = vm->output[i];
        } else if (channel->definition.mode == WT_MODE_NOISE_LONG
                || channel->definition.mode == WT_MODE_NOISE_SHORT) {
            control.waveOffset = channel->definition.mode;
        } else control.waveOffset = channel->definition.mode << 4;
        if (memcmp(&control, &vm->output[i], sizeof(control)) || (vm->pendingReset & bit)) {
            vm->output[i] = control;
            event->voice[i] = control;
            event->changedMask |= bit;
        }
        advance_volume_envelope(channel);
        advance_macro(&channel->pitchPosition, channel->definition.pitchLength,
                      channel->definition.pitchLoop);
        if (!channel->keyOn && (channel->definition.volumeFlags & TRACKER_ENV_ENABLED)
         && channel->definition.fadeout) {
            if (channel->fadeout <= channel->definition.fadeout) {
                channel->fadeout = 0;
                channel->gate = 0;
            } else channel->fadeout -= channel->definition.fadeout;
        }
    }
    event->resetMask = vm->pendingReset;
    vm->pendingReset = 0;
    event->waitSamples = next_tick_samples(vm);
    return advance_song(vm);
}

static uint8_t open_track(MEM_XDATA(TrackerPlayer) *player, uint16_t index)
{
    MEM_XDATA(TrackerVm) *vm = &player->vm;
    uint16_t entry = (uint16_t)(T10P_HEADER_SIZE + ((uint32_t)index << 3));
    uint16_t scoreBase = (uint16_t)(uint16_t)Score;
    uint32_t offset = score_u32((uint16_t)(scoreBase + entry));
    uint32_t size = score_u32((uint16_t)(scoreBase + entry + 4));
    uint16_t waveAddress;
    uint8_t flags;
    uint8_t i;
    PlatformIrqState irq;
    trackerLastError = 0x20;
    if (offset < T10P_HEADER_SIZE + ((uint32_t)player->scheduler.trackCount << 3)
     || offset > player->scheduler.playlistSize
     || size > player->scheduler.playlistSize - offset
     || size < T10M_HEADER_SIZE || offset + size > 0x10000UL) return 0;
    memset(vm, 0, sizeof(*vm));
    vm->trackBase = (uint16_t)(scoreBase + offset);
    vm->trackSize = (uint16_t)size;
    /* Host-validated T10M: only essential fields, no magic/rate re-check tree. */
    if (track_u8(vm, 4) != 4 || track_u8(vm, 5) != WT_VOICE_COUNT) return 0;
    flags = track_u8(vm, 6);
    if (flags & 0xFC) return 0;
    vm->loop = flags & T10_TRACK_LOOP;
    vm->amigaEffects = !!(flags & T10_TRACK_AMIGA_EFFECTS);
    vm->orderCount = track_u16(vm, 16);
    vm->restartOrder = track_u16(vm, 18);
    vm->patternDirectoryOffset = (uint16_t)track_u32(vm, 20);
    {
        uint16_t patternCount = track_u16(vm, 24);
        uint16_t initialSpeed = track_u16(vm, 40);
        if (!patternCount || patternCount > 255 || !initialSpeed || initialSpeed > 255
         || !vm->orderCount || vm->orderCount > 256
         || vm->restartOrder >= vm->orderCount) return 0;
        vm->patternCount = (uint8_t)patternCount;
        vm->speed = (uint8_t)initialSpeed;
    }
    vm->instrumentCount = track_u16(vm, 26);
    vm->instrumentOffset = (uint16_t)track_u32(vm, 28);
    vm->resourceOffset = (uint16_t)track_u32(vm, 44);
    vm->bpm = track_u16(vm, 42);
    if (!vm->instrumentCount || vm->instrumentCount > 255
     || vm->bpm < 32 || vm->bpm > 999
     || (uint32_t)vm->resourceOffset + T10_RESOURCE_HEADER_SIZE > size) return 0;
    vm->waveCount = track_u8(vm, (uint16_t)(vm->resourceOffset + 4));
    vm->pcmCount = track_u8(vm, (uint16_t)(vm->resourceOffset + 5));
    vm->pcmDirectoryOffset = (uint16_t)(vm->resourceOffset + T10_RESOURCE_HEADER_SIZE
                           + ((uint16_t)vm->waveCount << 4));
    if (!vm->waveCount || vm->waveCount > WT_WAVE_COUNT
     || vm->pcmCount > WT_MODE_NOISE_LONG - WT_MODE_PCM_BASE) return 0;
    waveAddress = (uint16_t)(vm->trackBase + vm->resourceOffset + T10_RESOURCE_HEADER_SIZE);
    if (((uint32_t)vm->waveCount << 4) > 0x10000UL - waveAddress) return 0;
    for (i = 0; i < WT_VOICE_COUNT; i++) {
        vm->channel[i].volume = 64;
        default_instrument(&vm->channel[i].definition);
    }
    vm->pendingReset = 0x03FF;
    vm->status = TRACKER_PRIMING;
    if (!load_order(vm)) return 0;
    queue_reset();
    WavetableSynthSilence();
    AudioBufferInit();
    Platform_IrqSave(irq);
    wavetableCodeBase = waveAddress;
    Platform_IrqRestore(irq);
    trackerLastError = 0;
    return 1;
}

void TrackerPlayerInit(MEM_XDATA(TrackerPlayer) *player)
{
    memset(player, 0, sizeof(*player));
    player->scheduler.currentTrack = -1;
    player->scheduler.requestedTrack = -1;
    player->vm.status = TRACKER_STOPPED;
}

uint8_t TrackerPlayerStart(MEM_XDATA(TrackerPlayer) *player, uint8_t mode)
{
    uint16_t scoreBase = (uint16_t)(uint16_t)Score;
    uint32_t size = ScoreSize;
    if (size < T10P_HEADER_SIZE || size > 0x10000UL
     || score_u32(scoreBase) != 0x50303154UL
     || score_u8((uint16_t)(scoreBase + 4)) != 4
     || score_u8((uint16_t)(scoreBase + 5))
     || score_u32((uint16_t)(scoreBase + 12))) {
        trackerLastError = 0x10;
        player->vm.status = TRACKER_ERROR;
        return 0;
    }
    player->scheduler.trackCount = score_u16((uint16_t)(scoreBase + 6));
    player->scheduler.playlistSize = (uint16_t)score_u32((uint16_t)(scoreBase + 8));
    if (!player->scheduler.trackCount
     || player->scheduler.playlistSize > size
     || player->scheduler.playlistSize < T10P_HEADER_SIZE
        + (uint32_t)player->scheduler.trackCount * T10P_ENTRY_SIZE) {
        trackerLastError = 0x11;
        return 0;
    }
    player->scheduler.mode = mode;
    player->scheduler.currentTrack = 0;
    return open_track(player, 0);
}

void TrackerPlayerProcess(MEM_XDATA(TrackerPlayer) *player)
{
    MEM_XDATA(TrackerVm) *vm = &player->vm;
    if (player->scheduler.switchPending) {
        int16_t index = player->scheduler.requestedTrack;
        player->scheduler.switchPending = 0;
        if (index < 0) index = player->scheduler.trackCount - 1;
        if (index >= player->scheduler.trackCount) index = 0;
        player->scheduler.currentTrack = index;
        if (!open_track(player, (uint16_t)index)) vm->status = TRACKER_ERROR;
    }
    if (vm->status != TRACKER_PRIMING && vm->status != TRACKER_PLAYING) return;
    while (!queue_full()) {
        if (!vm_next_event(vm, &decodeEvent)) {
            if (!trackerLastError) trackerLastError = 0x80;
            vm->status = TRACKER_ERROR;
            return;
        }
        if (!queue_commit(&decodeEvent)) return;
        if (vm->status == TRACKER_PRIMING) vm->status = TRACKER_PLAYING;
        if (decodeEvent.terminal) return;
    }
}

void TrackerPlayerStop(MEM_XDATA(TrackerPlayer) *player) { player->vm.status = TRACKER_STOPPED; queue_reset(); WavetableSynthSilence(); AudioBufferInit(); }
void TrackerPlayerPlay(MEM_XDATA(TrackerPlayer) *player) { if (player->vm.status == TRACKER_STOPPED) { player->scheduler.requestedTrack = player->scheduler.currentTrack < 0 ? 0 : player->scheduler.currentTrack; player->scheduler.switchPending = 1; } }
void TrackerPlayerNext(MEM_XDATA(TrackerPlayer) *player) { player->scheduler.requestedTrack = player->scheduler.currentTrack + 1; player->scheduler.switchPending = 1; }
void TrackerPlayerPrevious(MEM_XDATA(TrackerPlayer) *player) { player->scheduler.requestedTrack = player->scheduler.currentTrack - 1; player->scheduler.switchPending = 1; }
void TrackerPlayerSelect(MEM_XDATA(TrackerPlayer) *player, int16_t index) { if (index >= 0 && index < player->scheduler.trackCount) { player->scheduler.requestedTrack = index; player->scheduler.switchPending = 1; } }
