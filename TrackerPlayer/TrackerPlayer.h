#ifndef TRACKER_PLAYER_H
#define TRACKER_PLAYER_H

#include <stdint.h>
#include "Platform.h"
#include "Storage.h"
#include "WavetableSynth.h"

#define TRACKER_QUEUE_LENGTH 4
#define TRACKER_STOPPED 0
#define TRACKER_PLAYING 1
#define TRACKER_ERROR 2
#define TRACKER_PRIMING 3
#define TRACKER_MODE_ORDER_PLAY 0
#define TRACKER_MODE_LIST_ONCE 1
#define TRACKER_MODE_SINGLE 2

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

typedef struct _TrackerDecoder {
    ScoreStream trackStream;
    ScoreStream eventStream;
    ScoreStream loopStream;
    TrackerControlEvent shadow;
    uint16_t pitch[WT_VOICE_COUNT];
    uint32_t eventSize;
    uint32_t loopEventOffset;
    uint32_t remainingWait;
    uint16_t lastWait;
    uint8_t loop;
    uint8_t pendingEnd;
    uint8_t status;
} TrackerDecoder;

typedef struct _TrackerScheduler {
    ScoreStream playlistStream;
    int16_t currentTrack;
    int16_t requestedTrack;
    uint32_t playlistSize;
    uint16_t trackCount;
    uint8_t mode;
    uint8_t switchPending;
} TrackerScheduler;

typedef struct _TrackerPlayer {
    TrackerDecoder decoder;
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
void TrackerPlayerSampleTick(void) __using(1);

#endif
