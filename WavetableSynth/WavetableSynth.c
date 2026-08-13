#include <string.h>
#include "WavetableSynth.h"

MEM_XDATA(uint16_t) wavetableCodeBase;
MEM_XDATA(uint8_t) audioBuffer[WT_AUDIO_BUFFER_SIZE];
volatile MEM_XDATA(uint8_t) audioRead;
volatile MEM_XDATA(uint8_t) audioWrite;
volatile MEM_XDATA(uint8_t) audioUnderruns;

MEM_CODE(int8_t) waveTables[8 * WT_WAVE_SIZE] = {
    /* square 50%, pulse 25%, pulse 12.5%, triangle */
     96, 96, 96, 96, 96, 96, 96, 96,-96,-96,-96,-96,-96,-96,-96,-96,
    112,112,112,112,-48,-48,-48,-48,-48,-48,-48,-48,-48,-48,-48,-48,
    120,120,-24,-24,-24,-24,-24,-24,-24,-24,-24,-24,-24,-24,-24,-24,
   -112,-96,-80,-64,-48,-32,-16,  0, 16, 32, 48, 64, 80, 96,112,  0,
    /* saw, sine-like, hollow, deterministic noise */
   -120,-104,-88,-72,-56,-40,-24, -8,  8, 24, 40, 56, 72, 88,104,120,
      0, 45, 83,108,117,108, 83, 45,  0,-45,-83,-108,-117,-108,-83,-45,
     88, 64, 24,-24,-64,-88,-64,-24, 24, 64, 88, 64, 24,-24,-64,-88,
    105,-73, 39,118,-95, 62,-15,-112, 81,-44, 12, 97,-120, 54,-66, 28
};

void WavetableSynthInit(void)
{
    memset(&wavetableSynth, 0, sizeof(wavetableSynth));
    wavetableCodeBase = (uint16_t)waveTables;
    wavetableSynth.pwmSample = 128;
    wavetableSynth.noiseLong = 0xACE1;
    wavetableSynth.noiseShort = 0x5D;
    wavetableSynth.noiseLongSample = 96;
    wavetableSynth.noiseShortSample = 80;
}

void WavetableSynthSilence(void)
{
    uint8_t i;
    for (i = 0; i < WT_VOICE_COUNT; i++)
        wavetableSynth.voice[i].volume = 0;
    wavetableSynth.mixOut = 0;
    wavetableSynth.pwmSample = 128;
}

void WavetableSynthSetMuteMask(uint16_t mask)
{
    wavetableSynth.muteMask = (wavetableSynth.muteMask & 0xC000) | (mask & 0x07FF);
}

void AudioBufferInit(void)
{
    PlatformIrqState irq;
    Platform_IrqSave(irq);
    audioRead = audioWrite = audioUnderruns = 0;
    Platform_IrqRestore(irq);
}

uint8_t AudioBufferLevel(void)
{
    return (uint8_t)(audioWrite - audioRead);
}

void AudioRenderProcess(void)
{
    uint8_t budget = 16;
    uint8_t write = audioWrite;
    while (budget-- && (uint8_t)(write - audioRead) != 0xFF) {
        AudioRenderOne();
        audioBuffer[write++] = wavetableSynth.pwmSample;
        audioWrite = write;
    }
}
