#include <string.h>
#include "TrackerPlayer.h"
#include "SpiFlash.h"

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

static uint8_t read8(MEM_XDATA(TrackerVm) *vm, MEM_XDATA(uint8_t) *value)
{
    return stream_read(&vm->patternStream, value);
}

static uint8_t read16(MEM_XDATA(TrackerVm) *vm, MEM_XDATA(uint16_t) *value)
{
    static MEM_XDATA(uint8_t) lo;
    static MEM_XDATA(uint8_t) hi;
    if (!read8(vm, &lo) || !read8(vm, &hi)) return 0;
    *value = (uint16_t)lo | ((uint16_t)hi << 8);
    return 1;
}

static void default_instrument(MEM_XDATA(TrackerInstrumentState) *instrument)
{
    memset(instrument, 0, sizeof(*instrument));
    instrument->gain = 20;
    instrument->volumeLength = 1;
    instrument->volumeStep = 1;
    instrument->volumeSustain = 0xFF;
    instrument->volumeLoopStart = 0xFF;
    instrument->volumeLoopEnd = 0xFF;
    instrument->pitchLength = 1;
    instrument->pitchLoop = 0;
    instrument->volumeMacro[0] = 32;
}

static uint8_t load_instrument(MEM_XDATA(TrackerVm) *vm, uint8_t number,
                               MEM_XDATA(TrackerInstrumentState) *instrument)
{
    static MEM_XDATA(uint32_t) offset;
    static MEM_XDATA(uint8_t) i;
    if (!number) {
        default_instrument(instrument);
        return 1;
    }
    if (number > vm->instrumentCount) return 0;
    offset = vm->instrumentOffset + (uint32_t)(number - 1) * T10_INSTRUMENT_SIZE;
    instrument->mode = stream_u8(&vm->trackStream, offset);
    instrument->gain = stream_u8(&vm->trackStream, offset + 1);
    instrument->relativePitch = (int16_t)stream_u16(&vm->trackStream, offset + 2);
    instrument->volumeLength = stream_u8(&vm->trackStream, offset + 4);
    instrument->volumeStep = stream_u8(&vm->trackStream, offset + 5);
    instrument->volumeFlags = stream_u8(&vm->trackStream, offset + 6);
    instrument->volumeSustain = stream_u8(&vm->trackStream, offset + 7);
    instrument->volumeLoopStart = stream_u8(&vm->trackStream, offset + 8);
    instrument->volumeLoopEnd = stream_u8(&vm->trackStream, offset + 9);
    instrument->pitchLength = stream_u8(&vm->trackStream, offset + 10);
    instrument->pitchLoop = stream_u8(&vm->trackStream, offset + 11);
    instrument->fadeout = stream_u16(&vm->trackStream, offset + 12);
    if (!((instrument->mode < vm->waveCount)
          || instrument->mode == WT_MODE_NOISE_LONG
          || instrument->mode == WT_MODE_NOISE_SHORT
          || (instrument->mode >= WT_MODE_PCM_BASE
              && instrument->mode < (uint8_t)(WT_MODE_PCM_BASE + vm->pcmCount)))
     || instrument->gain > 31
     || !instrument->volumeLength || instrument->volumeLength > TRACKER_MACRO_LENGTH || !instrument->volumeStep
     || !instrument->pitchLength || instrument->pitchLength > TRACKER_MACRO_LENGTH
     || (instrument->volumeFlags & 0xF8)
     || ((instrument->volumeFlags & TRACKER_ENV_SUSTAIN)
         ? instrument->volumeSustain >= instrument->volumeLength : instrument->volumeSustain != 0xFF)
     || ((instrument->volumeFlags & TRACKER_ENV_LOOP)
         ? (instrument->volumeLoopStart > instrument->volumeLoopEnd
            || instrument->volumeLoopEnd >= instrument->volumeLength)
         : (instrument->volumeLoopStart != 0xFF || instrument->volumeLoopEnd != 0xFF))
     || stream_u16(&vm->trackStream, offset + 14)
     || (instrument->pitchLoop != 0xFF && instrument->pitchLoop >= instrument->pitchLength)) return 0;
    for (i = 0; i < TRACKER_MACRO_LENGTH; i++) {
        instrument->volumeMacro[i] = stream_u8(&vm->trackStream, offset + 16 + i);
        instrument->pitchMacro[i] = (int8_t)stream_u8(&vm->trackStream, offset + 32 + i);
        if (instrument->volumeMacro[i] > 32) return 0;
    }
    return 1;
}

static uint8_t load_pattern(MEM_XDATA(TrackerVm) *vm, uint8_t pattern)
{
    uint32_t entry, offset;
    uint16_t size;
    if (pattern >= vm->patternCount) { trackerLastError = 0x91; return 0; }
    entry = vm->patternDirectoryOffset + (uint32_t)pattern * T10_PATTERN_ENTRY_SIZE;
    offset = stream_u32(&vm->trackStream, entry);
    size = stream_u16(&vm->trackStream, entry + 4);
    vm->patternRows = stream_u16(&vm->trackStream, entry + 6);
    if (!vm->patternRows || offset < T10M_HEADER_SIZE || offset > vm->trackSize
     || !size || size > vm->trackSize - offset) { trackerLastError = 0x92; return 0; }
    stream_sub(&vm->patternStream, &vm->trackStream, offset, size);
    vm->row = 0;
    return 1;
}

static uint8_t load_order(MEM_XDATA(TrackerVm) *vm)
{
    uint32_t orderOffset = stream_u32(&vm->trackStream, 12);
    return load_pattern(vm, stream_u8(&vm->trackStream, orderOffset + vm->order));
}

static void remember_effect(MEM_XDATA(TrackerChannelState) *channel)
{
    uint8_t parameter = channel->parameter;
    switch (channel->effect) {
    case 1:
        if (parameter) channel->slideUp = parameter;
        channel->parameter = channel->slideUp;
        break;
    case 2:
        if (parameter) channel->slideDown = parameter;
        channel->parameter = channel->slideDown;
        break;
    case 3:
        if (parameter) channel->portaSpeed = parameter;
        channel->parameter = channel->portaSpeed;
        break;
    case 4:
        if (parameter >> 4) channel->vibratoSpeed = parameter >> 4;
        if (parameter & 15) channel->vibratoDepth = parameter & 15;
        channel->parameter = (uint8_t)((channel->vibratoSpeed << 4) | channel->vibratoDepth);
        break;
    case 0x0A:
        if (parameter) channel->volumeSlide = parameter;
        channel->parameter = channel->volumeSlide;
        break;
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
    static TrackerChannelState __xdata * __xdata channel;
    for (channelIndex = 0; channelIndex < WT_VOICE_COUNT; channelIndex++) {
        vm->channel[channelIndex].effect = 0;
        vm->channel[channelIndex].parameter = 0;
    }
    if (!read16(vm, &changed) || (changed & 0xFC00)) { trackerLastError = 0x81; return 0; }
    bit = 1;
    for (channelIndex = 0; channelIndex < WT_VOICE_COUNT; channelIndex++, bit <<= 1) {
        if (!(changed & bit)) continue;
        if (!read8(vm, &mask) || !mask || (mask & 0xF0)) { trackerLastError = 0x82; return 0; }
        note = instrument = volume = effect = parameter = 0;
        if ((mask & CELL_NOTE) && !read8(vm, &note)) { trackerLastError = 0x83; return 0; }
        if ((mask & CELL_INSTRUMENT) && !read8(vm, &instrument)) { trackerLastError = 0x84; return 0; }
        if ((mask & CELL_VOLUME) && !read8(vm, &volume)) { trackerLastError = 0x85; return 0; }
        if ((mask & CELL_EFFECT) && (!read8(vm, &effect) || !read8(vm, &parameter))) { trackerLastError = 0x86; return 0; }
        if ((note && (note > 97)) || volume > 64) { trackerLastError = 0x87; return 0; }
        channel = &vm->channel[channelIndex];
        channel->effect = effect;
        channel->parameter = parameter;
        remember_effect(channel);
        if (instrument) {
            if (!load_instrument(vm, instrument, &channel->definition)) { trackerLastError = 0x88; return 0; }
            channel->instrument = instrument;
            channel->volumePosition = channel->volumeTick = channel->pitchPosition = 0;
            channel->fadeout = 0x8000;
        }
        if (mask & CELL_VOLUME) channel->volume = volume;
        if (effect == 0x0F && parameter) {
            if (parameter < 0x20) vm->speed = parameter;
            else vm->bpm = parameter;
        }
        if (note == 97) {
            channel->keyOn = 0;
            if (!(channel->definition.volumeFlags & TRACKER_ENV_ENABLED)) channel->gate = 0;
        } else if (note) {
            uint16_t pitch = (uint16_t)(note + 11) << 8;
            if (effect == 3 && channel->gate) {
                channel->target = pitch;
            } else {
                channel->note = channel->target = pitch;
                channel->keyOn = channel->gate = 1;
                channel->volumePosition = channel->volumeTick = channel->pitchPosition = 0;
                channel->fadeout = 0x8000;
                if (!(mask & CELL_VOLUME) && instrument) channel->volume = 64;
                vm->pendingReset |= bit;
            }
        }
    }
    return 1;
}

static void retrigger_channel(MEM_XDATA(TrackerVm) *vm, uint8_t index)
{
    vm->channel[index].volumePosition = 0;
    vm->channel[index].volumeTick = 0;
    vm->channel[index].pitchPosition = 0;
    vm->channel[index].fadeout = 0x8000;
    vm->channel[index].keyOn = 1;
    vm->pendingReset |= (uint16_t)1 << index;
}

static void apply_tick_effects(MEM_XDATA(TrackerVm) *vm)
{
    static MEM_XDATA(uint8_t) i;
    static MEM_XDATA(uint8_t) up;
    static MEM_XDATA(uint8_t) down;
    static MEM_XDATA(uint8_t) interval;
    static MEM_XDATA(uint16_t) step;
    static MEM_XDATA(uint8_t) noteIndex;
    static TrackerChannelState __xdata * __xdata channel;
    if (!vm->tick) return;
    for (i = 0; i < WT_VOICE_COUNT; i++) {
        channel = &vm->channel[i];
        if (channel->effect >= 1 && channel->effect <= 3) {
            step = (uint16_t)channel->parameter << 4;
            if (vm->amigaEffects) {
                noteIndex = (uint8_t)(channel->note >> 8);
                if (noteIndex > 128) noteIndex = 128;
                step = (uint16_t)channel->parameter * amigaSlideScale[noteIndex];
            }
        }
        switch (channel->effect) {
        case 1:
            channel->note = channel->note > 0xFFFFU - step ? 0xFFFFU : channel->note + step;
            break;
        case 2:
            channel->note = channel->note > step ? channel->note - step : 1;
            break;
        case 3:
            if (channel->note < channel->target)
                channel->note = channel->target - channel->note < step ? channel->target : channel->note + step;
            else if (channel->note > channel->target)
                channel->note = channel->note - channel->target < step ? channel->target : channel->note - step;
            break;
        case 4:
            channel->vibratoPhase = (uint8_t)((channel->vibratoPhase + channel->vibratoSpeed) & 63);
            break;
        case 0x0A:
            up = channel->parameter >> 4;
            down = channel->parameter & 15;
            if (up) channel->volume = channel->volume + up > 64 ? 64 : channel->volume + up;
            else channel->volume = channel->volume > down ? channel->volume - down : 0;
            break;
        case 0x0E:
            interval = channel->parameter & 15;
            if ((channel->parameter >> 4) == 9 && interval && vm->tick % interval == 0)
                retrigger_channel(vm, i);
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
        uint8_t shift = vm->tick % 3 == 1 ? channel->parameter >> 4
                      : vm->tick % 3 == 2 ? channel->parameter & 15 : 0;
        pitch += (uint16_t)shift << 8;
    } else if (channel->effect == 4 && channel->gate) {
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
    vm->row++;
    if (vm->row < vm->patternRows) return 1;
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
    static TrackerChannelState __xdata * __xdata channel;
    static MEM_XDATA(TrackerVoiceControl) control;
    memset(event, 0, sizeof(*event));
    if (vm->pendingEnd) {
        event->terminal = 1;
        event->waitSamples = 1;
        return 1;
    }
    if (!vm->tick && !decode_row(vm)) return 0;
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
        if (channel->definition.mode >= WT_MODE_PCM_BASE
         && channel->definition.mode < WT_MODE_NOISE_LONG) {
            control.volume = (control.volume + 2U) >> 2;
            if (control.volume > 31) control.volume = 31;
            if ((vm->pendingReset & bit) && control.volume) {
                pcmEntry = vm->pcmDirectoryOffset
                         + (uint32_t)(channel->definition.mode - WT_MODE_PCM_BASE)
                         * T10_PCM_ENTRY_SIZE;
                pcmAddress = vm->trackStream.base + stream_u32(&vm->trackStream, pcmEntry);
                pcmLength = stream_u16(&vm->trackStream, pcmEntry + 4);
                if (pcmAddress > 0xFFFFUL || !pcmLength) {
                    trackerLastError = 0x89;
                    return 0;
                }
                control.increment[0] = (uint8_t)pcmAddress;
                control.increment[1] = (uint8_t)(pcmAddress >> 8);
                control.increment[2] = (uint8_t)pcmLength;
                control.waveOffset = (uint8_t)(pcmLength >> 8);
                /* Bit 7 selects PCM; bit 6 asks the ISR to prime its sample cache. */
                control.volume |= 0xC0;
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
    uint32_t entry = T10P_HEADER_SIZE + (uint32_t)index * T10P_ENTRY_SIZE;
    uint32_t offset = stream_u32(&player->scheduler.playlistStream, entry);
    uint32_t size = stream_u32(&player->scheduler.playlistStream, entry + 4);
    uint32_t totalSize;
    uint32_t waveAddress;
    uint8_t i;
    PlatformIrqState irq;
    trackerLastError = 0x20;
    if (offset < T10P_HEADER_SIZE + (uint32_t)player->scheduler.trackCount * T10P_ENTRY_SIZE
     || offset > player->scheduler.playlistSize || size > player->scheduler.playlistSize - offset
     || size < T10M_HEADER_SIZE) return 0;
    memset(vm, 0, sizeof(*vm));
    stream_sub(&vm->trackStream, &player->scheduler.playlistStream, offset, size);
    vm->trackSize = size;
    if (stream_u8(&vm->trackStream, 0) != 'T' || stream_u8(&vm->trackStream, 1) != '1'
     || stream_u8(&vm->trackStream, 2) != '0' || stream_u8(&vm->trackStream, 3) != 'M'
     || stream_u8(&vm->trackStream, 4) != 4 || stream_u8(&vm->trackStream, 5) != WT_VOICE_COUNT
     || stream_u32(&vm->trackStream, 8) != T10_SAMPLE_RATE) return 0;
    vm->loop = stream_u8(&vm->trackStream, 6) & T10_TRACK_LOOP;
    vm->amigaEffects = !!(stream_u8(&vm->trackStream, 6) & T10_TRACK_AMIGA_EFFECTS);
    vm->orderCount = stream_u16(&vm->trackStream, 16);
    vm->restartOrder = stream_u16(&vm->trackStream, 18);
    vm->patternDirectoryOffset = stream_u32(&vm->trackStream, 20);
    {
        uint16_t patternCount = stream_u16(&vm->trackStream, 24);
        uint16_t initialSpeed = stream_u16(&vm->trackStream, 40);
        if (!patternCount || patternCount > 255 || !initialSpeed || initialSpeed > 255) return 0;
        vm->patternCount = (uint8_t)patternCount;
        vm->speed = (uint8_t)initialSpeed;
    }
    vm->instrumentCount = stream_u16(&vm->trackStream, 26);
    vm->instrumentOffset = stream_u32(&vm->trackStream, 28);
    vm->resourceOffset = stream_u32(&vm->trackStream, 44);
    totalSize = stream_u32(&vm->trackStream, 32);
    vm->bpm = stream_u16(&vm->trackStream, 42);
    {
        uint32_t orderOffset = stream_u32(&vm->trackStream, 12);
        uint32_t patternBytes = (uint32_t)vm->patternCount * T10_PATTERN_ENTRY_SIZE;
        uint32_t instrumentBytes = (uint32_t)vm->instrumentCount * T10_INSTRUMENT_SIZE;
        if (totalSize != size || !vm->orderCount || vm->orderCount > 256
         || vm->restartOrder >= vm->orderCount || !vm->instrumentCount
         || vm->instrumentCount > 255 || vm->bpm < 32 || vm->bpm > 999
         || (stream_u8(&vm->trackStream, 6) & 0xFC) || stream_u8(&vm->trackStream, 7)
         || orderOffset > size || vm->orderCount > size - orderOffset
         || vm->patternDirectoryOffset > size || patternBytes > size - vm->patternDirectoryOffset
         || vm->instrumentOffset > size || instrumentBytes > size - vm->instrumentOffset
         || vm->resourceOffset < vm->instrumentOffset + instrumentBytes
         || vm->resourceOffset > size
         || T10_RESOURCE_HEADER_SIZE > size - vm->resourceOffset) return 0;
    }
    if (storage_get_backend() != STORAGE_BACKEND_INTERNAL
     || stream_u8(&vm->trackStream, vm->resourceOffset) != 'T'
     || stream_u8(&vm->trackStream, vm->resourceOffset + 1) != '1'
     || stream_u8(&vm->trackStream, vm->resourceOffset + 2) != '0'
     || stream_u8(&vm->trackStream, vm->resourceOffset + 3) != 'R') return 0;
    vm->waveCount = stream_u8(&vm->trackStream, vm->resourceOffset + 4);
    vm->pcmCount = stream_u8(&vm->trackStream, vm->resourceOffset + 5);
    vm->pcmDirectoryOffset = vm->resourceOffset + T10_RESOURCE_HEADER_SIZE
                           + (uint32_t)vm->waveCount * T10_WAVE_SIZE;
    if (!vm->waveCount || vm->waveCount > WT_WAVE_COUNT
     || vm->pcmCount > WT_MODE_NOISE_LONG - WT_MODE_PCM_BASE
     || stream_u16(&vm->trackStream, vm->resourceOffset + 6) != T10_PCM_RATE
     || vm->pcmDirectoryOffset > size
     || (uint32_t)vm->pcmCount * T10_PCM_ENTRY_SIZE > size - vm->pcmDirectoryOffset) return 0;
    waveAddress = vm->trackStream.base + vm->resourceOffset + T10_RESOURCE_HEADER_SIZE;
    if (waveAddress > 0xFFFFUL
     || (uint32_t)vm->waveCount * T10_WAVE_SIZE > 0x10000UL - waveAddress) return 0;
    for (i = 0; i < vm->pcmCount; i++) {
        uint32_t entry = vm->pcmDirectoryOffset + (uint32_t)i * T10_PCM_ENTRY_SIZE;
        uint32_t pcmOffset = stream_u32(&vm->trackStream, entry);
        uint16_t pcmLength = stream_u16(&vm->trackStream, entry + 4);
        uint32_t pcmAddress = vm->trackStream.base + pcmOffset;
        if (!pcmLength || stream_u16(&vm->trackStream, entry + 6)
         || pcmOffset > size || pcmLength > size - pcmOffset
         || pcmAddress > 0xFFFFUL || pcmLength > 0x10000UL - pcmAddress) return 0;
    }
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
    wavetableCodeBase = (uint16_t)waveAddress;
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
    uint32_t size = storage_get_backend() == STORAGE_BACKEND_SPI ? SPI_FLASH_SIZE : ScoreSize;
    storage_init();
    stream_init(&player->scheduler.playlistStream, storage_get_base_addr(), size);
    if (size < T10P_HEADER_SIZE || stream_u8(&player->scheduler.playlistStream, 0) != 'T'
     || stream_u8(&player->scheduler.playlistStream, 1) != '1'
     || stream_u8(&player->scheduler.playlistStream, 2) != '0'
     || stream_u8(&player->scheduler.playlistStream, 3) != 'P'
     || stream_u8(&player->scheduler.playlistStream, 4) != 4
     || stream_u8(&player->scheduler.playlistStream, 5)
     || stream_u32(&player->scheduler.playlistStream, 12)) {
        trackerLastError = 0x10;
        player->vm.status = TRACKER_ERROR;
        return 0;
    }
    player->scheduler.trackCount = stream_u16(&player->scheduler.playlistStream, 6);
    player->scheduler.playlistSize = stream_u32(&player->scheduler.playlistStream, 8);
    if (!player->scheduler.trackCount || player->scheduler.playlistSize > size
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

void TrackerPlayerSampleTick(void) __using(1)
{
    uint8_t i, head;
    uint16_t bit;
    MEM_XDATA(TrackerControlEvent) *event;
    if (trackerQueue.countdown) { trackerQueue.countdown--; return; }
    if (trackerQueue.head == trackerQueue.tail) {
        if (mainPlayer.vm.status == TRACKER_PLAYING) trackerQueue.underruns++;
        return;
    }
    head = trackerQueue.head;
    event = &trackerQueue.slots[head & (TRACKER_QUEUE_LENGTH - 1)];
    bit = 1;
    for (i = 0; i < WT_VOICE_COUNT; i++, bit <<= 1) if (event->changedMask & bit) {
        if (event->voice[i].volume & 0x80) {
            wavetableSynth.voice[i].phase[0] = event->voice[i].increment[0];
            wavetableSynth.voice[i].phase[1] = event->voice[i].increment[1];
            wavetableSynth.voice[i].phase[2] = event->voice[i].increment[2];
            wavetableSynth.voice[i].increment[0] = event->voice[i].waveOffset;
            wavetableSynth.voice[i].increment[2] = i & 1;
            wavetableSynth.voice[i].volume = event->voice[i].volume;
            continue;
        }
        if (event->resetMask & bit)
            wavetableSynth.voice[i].phase[0] = wavetableSynth.voice[i].phase[1] = wavetableSynth.voice[i].phase[2] = 0;
        wavetableSynth.voice[i].increment[0] = event->voice[i].increment[0];
        wavetableSynth.voice[i].increment[1] = event->voice[i].increment[1];
        wavetableSynth.voice[i].increment[2] = event->voice[i].increment[2];
        wavetableSynth.voice[i].volume = event->voice[i].volume;
        wavetableSynth.voice[i].waveOffset = event->voice[i].waveOffset;
    }
    trackerQueue.countdown = event->waitSamples ? event->waitSamples - 1 : 0;
    trackerQueue.head = head + 1;
    if (event->terminal) {
        mainPlayer.vm.status = TRACKER_STOPPED;
        WavetableSynthSilence();
    }
}

void TrackerPlayerStop(MEM_XDATA(TrackerPlayer) *player) { player->vm.status = TRACKER_STOPPED; queue_reset(); WavetableSynthSilence(); AudioBufferInit(); }
void TrackerPlayerPlay(MEM_XDATA(TrackerPlayer) *player) { if (player->vm.status == TRACKER_STOPPED) { player->scheduler.requestedTrack = player->scheduler.currentTrack < 0 ? 0 : player->scheduler.currentTrack; player->scheduler.switchPending = 1; } }
void TrackerPlayerNext(MEM_XDATA(TrackerPlayer) *player) { player->scheduler.requestedTrack = player->scheduler.currentTrack + 1; player->scheduler.switchPending = 1; }
void TrackerPlayerPrevious(MEM_XDATA(TrackerPlayer) *player) { player->scheduler.requestedTrack = player->scheduler.currentTrack - 1; player->scheduler.switchPending = 1; }
void TrackerPlayerSelect(MEM_XDATA(TrackerPlayer) *player, int16_t index) { if (index >= 0 && index < player->scheduler.trackCount) { player->scheduler.requestedTrack = index; player->scheduler.switchPending = 1; } }
