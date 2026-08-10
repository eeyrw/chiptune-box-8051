#ifndef WAVETABLE_SYNTH_H
#define WAVETABLE_SYNTH_H

#include <stdint.h>
#include "Platform.h"

#define WT_VOICE_COUNT 10
#define WT_WAVE_COUNT 8
#define WT_OUTPUT_RATE 32000UL

typedef struct _WtVoice {
    uint8_t phase[3];
    uint8_t increment[3];
    uint8_t volume;
    uint8_t waveOffset;
} WtVoice;

typedef struct _WavetableSynthState {
    WtVoice voice[WT_VOICE_COUNT];
    int16_t mixOut;
    uint8_t pwmSample;
    uint16_t muteMask;
    uint16_t noiseLong;
    uint8_t noiseShort;
    int8_t noiseLongSample;
    int8_t noiseShortSample;
} WavetableSynthState;

extern MEM_FAST_DATA(WavetableSynthState) wavetableSynth;
extern MEM_CODE(int8_t) waveTables[WT_WAVE_COUNT * 16];

void WavetableSynthInit(void);
void WavetableSynthSilence(void);
void WavetableSynthSetMuteMask(uint16_t mask);
uint32_t WavetablePitchToIncrement(uint16_t pitch);
void WavetableSynthStep(void) __using(1);

#endif
