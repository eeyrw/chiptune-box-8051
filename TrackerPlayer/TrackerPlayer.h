#ifndef TRACKER_PLAYER_H
#define TRACKER_PLAYER_H

#include <stdint.h>
#include "Platform.h"
#include "ScoreFlash.h"
#include "WavetableSynth.h"

#define TRACKER_QUEUE_LENGTH 4
#define TRACKER_STOPPED 0
#define TRACKER_PLAYING 1
#define TRACKER_ERROR 2
#define TRACKER_PRIMING 3
#define TRACKER_MODE_ORDER_PLAY 0
#define TRACKER_MODE_LIST_ONCE 1
#define TRACKER_MODE_SINGLE 2
#define TRACKER_MACRO_LENGTH 16
#define TRACKER_ENV_ENABLED 0x01
#define TRACKER_ENV_SUSTAIN 0x02
#define TRACKER_ENV_LOOP 0x04

typedef struct _TrackerVoiceControl {
    uint8_t increment[3];
    uint8_t volume;
    uint8_t waveOffset;
} TrackerVoiceControl;

typedef struct _TrackerControlEvent {
    uint16_t waitSamples;
    uint16_t changedMask;
    uint16_t resetMask;
    uint8_t terminal;
    TrackerVoiceControl voice[WT_VOICE_COUNT];
} TrackerControlEvent;

typedef struct _TrackerControlQueue {
    TrackerControlEvent slots[TRACKER_QUEUE_LENGTH];
    volatile uint8_t head;
    volatile uint8_t tail;
    volatile uint16_t countdown;
    volatile uint8_t underruns;
} TrackerControlQueue;

/* Matches on-disk T10 instrument (48 bytes) so load is a bulk MOVC copy. */
typedef struct _TrackerInstrumentState {
    uint8_t mode;
    uint8_t gain;
    int16_t relativePitch;
    uint8_t volumeLength;
    uint8_t volumeStep;
    uint8_t volumeFlags;
    uint8_t volumeSustain;
    uint8_t volumeLoopStart;
    uint8_t volumeLoopEnd;
    uint8_t pitchLength;
    uint8_t pitchLoop;
    uint16_t fadeout;
    uint16_t reserved;
    uint8_t volumeMacro[TRACKER_MACRO_LENGTH];
    int8_t pitchMacro[TRACKER_MACRO_LENGTH];
} TrackerInstrumentState;

typedef struct _TrackerChannelState {
    uint16_t note;
    uint16_t target;
    uint8_t instrument;
    uint8_t volume;
    uint8_t effect;
    uint8_t parameter;
    uint8_t slideUp;
    uint8_t slideDown;
    uint8_t portaSpeed;
    uint8_t vibratoSpeed;
    uint8_t vibratoDepth;
    uint8_t volumeSlide;
    uint8_t vibratoPhase;
    uint8_t volumePosition;
    uint8_t volumeTick;
    uint8_t pitchPosition;
    uint8_t keyOn;
    uint8_t gate;
    uint16_t fadeout;
    TrackerInstrumentState definition;
} TrackerChannelState;

typedef struct _TrackerVm {
    /* Absolute Code-Flash addresses into Score[]. */
    uint16_t trackBase;
    uint16_t patternPos;
    uint16_t patternEnd;
    TrackerChannelState channel[WT_VOICE_COUNT];
    TrackerVoiceControl output[WT_VOICE_COUNT];
    uint16_t trackSize;
    uint16_t patternDirectoryOffset;
    uint16_t instrumentOffset;
    uint16_t resourceOffset;
    uint16_t pcmDirectoryOffset;
    uint32_t timingRemainder;
    uint16_t orderCount;
    uint16_t restartOrder;
    uint16_t patternRows;
    uint16_t row;
    uint16_t order;
    uint16_t instrumentCount;
    uint16_t pendingReset;
    uint16_t nextOrder;
    uint16_t bpm;
    uint8_t patternCount;
    uint8_t speed;
    uint8_t tick;
    uint8_t loop;
    uint8_t amigaEffects;
    uint8_t waveCount;
    uint8_t pcmCount;
    uint8_t pendingEnd;
    uint8_t nextRow;
    uint8_t flowPending;
    uint8_t status;
    /* Fields after status: keep C and TrackerPlayer.inc offset equal. */
    uint8_t patternDelay;
    uint8_t holdRow;
    uint8_t loopStart;
    uint8_t loopCount;
    uint8_t loopJump;
    uint16_t useSampleOffsetMask;
    uint16_t noteDelayMask;
    uint8_t sampleOffset[WT_VOICE_COUNT];
    uint8_t noteDelayRemain[WT_VOICE_COUNT];
    uint8_t delayedNote[WT_VOICE_COUNT];
    uint8_t delayedInstrument[WT_VOICE_COUNT];
    uint8_t delayedVolume[WT_VOICE_COUNT];
} TrackerVm;

typedef struct _TrackerScheduler {
    int16_t currentTrack;
    int16_t requestedTrack;
    uint16_t playlistSize;
    uint16_t trackCount;
    uint8_t mode;
    uint8_t switchPending;
} TrackerScheduler;

typedef struct _TrackerPlayer {
    TrackerVm vm;
    TrackerScheduler scheduler;
} TrackerPlayer;

extern MEM_XDATA(TrackerPlayer) mainPlayer;
extern MEM_XDATA(TrackerControlQueue) trackerQueue;
extern volatile uint8_t trackerLastError;

void TrackerPlayerInit(MEM_XDATA(TrackerPlayer) *player);
uint8_t TrackerPlayerStart(MEM_XDATA(TrackerPlayer) *player, uint8_t mode);
void TrackerPlayerProcess(MEM_XDATA(TrackerPlayer) *player);
void TrackerPlayerStop(MEM_XDATA(TrackerPlayer) *player);
void TrackerPlayerPlay(MEM_XDATA(TrackerPlayer) *player);
void TrackerPlayerNext(MEM_XDATA(TrackerPlayer) *player);
void TrackerPlayerPrevious(MEM_XDATA(TrackerPlayer) *player);
void TrackerPlayerSelect(MEM_XDATA(TrackerPlayer) *player, int16_t index);
#endif
