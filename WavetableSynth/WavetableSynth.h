#ifndef WAVETABLE_SYNTH_H
#define WAVETABLE_SYNTH_H

#include <stdint.h>
#include "Platform.h"

#define WT_VOICE_COUNT 10
#define WT_WAVE_COUNT 16
#define WT_WAVE_SIZE 16
#define WT_MODE_PCM_BASE 0x80
#define WT_MODE_NOISE_LONG 0xFE
#define WT_MODE_NOISE_SHORT 0xFF
#define WT_OUTPUT_RATE 32000UL
#define WT_AUDIO_BUFFER_SIZE 256U

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
extern MEM_XDATA(uint16_t) wavetableCodeBase;
extern MEM_CODE(int8_t) waveTables[8 * WT_WAVE_SIZE];
extern MEM_XDATA(uint8_t) audioBuffer[WT_AUDIO_BUFFER_SIZE];
extern volatile MEM_XDATA(uint8_t) audioRead;
extern volatile MEM_XDATA(uint8_t) audioWrite;
extern volatile MEM_XDATA(uint8_t) audioUnderruns;

void WavetableSynthInit(void);
void WavetableSynthSilence(void);
void WavetableSynthSetMuteMask(uint16_t mask);
void AudioBufferInit(void);
uint8_t AudioRenderProcess(void);
uint8_t AudioBufferLevel(void);
void AudioRenderOne(void) __using(1);
uint32_t WavetablePitchToIncrement(uint16_t pitch);
void WavetableSynthStep(void) __using(1);

#endif
