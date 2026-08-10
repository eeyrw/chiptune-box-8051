#include <string.h>
#include "TrackerPlayer.h"
#include "SpiFlash.h"

#define T10P_HEADER_SIZE 16UL
#define T10P_ENTRY_SIZE 8UL
#define T10M_HEADER_SIZE 32UL
#define T10_SAMPLE_RATE 32000UL

MEM_XDATA(TrackerControlQueue) trackerQueue;
volatile uint8_t trackerLastError;
extern MEM_CODE(unsigned char) Score[];
extern MEM_CODE(uint32_t) ScoreSize;

static MEM_XDATA(TrackerControlEvent) decodeEvent;

static uint8_t read8(MEM_XDATA(TrackerDecoder) *d, uint8_t *v)
{
    return stream_read(&d->eventStream, v);
}

static uint8_t read16(MEM_XDATA(TrackerDecoder) *d, uint16_t *v)
{
    uint8_t lo, hi;
    if (!read8(d, &lo) || !read8(d, &hi)) return 0;
    *v = (uint16_t)lo | ((uint16_t)hi << 8);
    return 1;
}

static uint8_t queue_full(void)
{
    return (uint8_t)((trackerQueue.tail + 1) & (TRACKER_QUEUE_LENGTH - 1)) == trackerQueue.head;
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
    memcpy(&trackerQueue.slots[tail], event, sizeof(*event));
    trackerQueue.tail = (uint8_t)((tail + 1) & (TRACKER_QUEUE_LENGTH - 1));
    return 1;
}

static uint8_t reset_loop(MEM_XDATA(TrackerDecoder) *d)
{
    uint8_t i;
    if (!d->loop) return 0;
    for (i = 0; i < STREAM_IMPL_SIZE; i++) d->eventStream._[i] = d->loopStream._[i];
    d->lastWait=0;
    return 1;
}

static uint8_t open_track(MEM_XDATA(TrackerPlayer) *p, uint16_t index)
{
    MEM_XDATA(TrackerDecoder) *d = &p->decoder;
    uint32_t entry = T10P_HEADER_SIZE + (uint32_t)index * T10P_ENTRY_SIZE;
    uint32_t off = stream_u32(&p->scheduler.playlistStream, entry);
    uint32_t size = stream_u32(&p->scheduler.playlistStream, entry + 4);
    uint32_t eventOff, eventSize;
    uint8_t flags;
    trackerLastError = 0x20;
    if (off < T10P_HEADER_SIZE+(uint32_t)p->scheduler.trackCount*T10P_ENTRY_SIZE
     || off > p->scheduler.playlistSize || size > p->scheduler.playlistSize-off
     || size < T10M_HEADER_SIZE) return 0;
    stream_sub(&d->trackStream, &p->scheduler.playlistStream, off, size);
    if (stream_u8(&d->trackStream,0)!='T' || stream_u8(&d->trackStream,1)!='1'
     || stream_u8(&d->trackStream,2)!='0' || stream_u8(&d->trackStream,3)!='M'
     || stream_u8(&d->trackStream,4)!=1 || stream_u8(&d->trackStream,5)!=WT_VOICE_COUNT
     || stream_u32(&d->trackStream,8)!=T10_SAMPLE_RATE) return 0;
    flags = stream_u8(&d->trackStream,6);
    eventOff = stream_u32(&d->trackStream,12);
    eventSize = stream_u32(&d->trackStream,16);
    if (eventOff < T10M_HEADER_SIZE || eventOff > size || !eventSize || eventSize > size-eventOff) return 0;
    d->loop = flags & 1;
    d->loopEventOffset = stream_u32(&d->trackStream,20);
    if ((d->loop && d->loopEventOffset >= eventSize) || (!d->loop && d->loopEventOffset)) return 0;
    d->eventSize = eventSize;
    stream_sub(&d->eventStream, &d->trackStream, eventOff, eventSize);
    if (d->loop) stream_sub(&d->loopStream, &d->trackStream, eventOff+d->loopEventOffset, eventSize-d->loopEventOffset);
    memset(&d->shadow, 0, sizeof(d->shadow));
    d->remainingWait = 0;
    d->lastWait = 0;
    memset(d->pitch,0,sizeof(d->pitch));
    d->pendingEnd = 0;
    d->status = TRACKER_PRIMING;
    queue_reset();
    WavetableSynthSilence();
    trackerLastError = 0;
    return 1;
}

static uint8_t decode_control(MEM_XDATA(TrackerDecoder) *d, uint8_t opcode,
                              MEM_XDATA(TrackerControlEvent) *event)
{
    uint8_t channel = opcode - 0x20;
    uint8_t mask, value;
    MEM_XDATA(TrackerVoiceControl) *v;
    if (channel >= WT_VOICE_COUNT || !read8(d, &mask) || (mask & 0xF0)) return 0;
    v = &d->shadow.voice[channel];
    if (mask & 1) {
        if (!read8(d,&v->increment[0]) || !read8(d,&v->increment[1]) || !read8(d,&v->increment[2])) return 0;
    }
    if (mask & 2) {
        if (!read8(d,&value) || value > 31) return 0;
        v->volume = value;
    }
    if (mask & 4) {
        if (!read8(d,&value) || value >= WT_WAVE_COUNT) return 0;
        v->waveOffset = value << 4;
    }
    event->voice[channel] = *v;
    event->changedMask |= (uint16_t)1 << channel;
    if (mask & 8) event->resetMask |= (uint16_t)1 << channel;
    return 1;
}

static uint8_t decode_compact(MEM_XDATA(TrackerDecoder) *d, uint8_t opcode,
                              MEM_XDATA(TrackerControlEvent) *event)
{
    uint8_t channel=opcode%0x10;
    uint8_t kind=opcode&0xF0;
    uint8_t packed, pitchHi;
    uint16_t pitch;
    uint32_t increment;
    MEM_XDATA(TrackerVoiceControl) *v;
    if(channel>=WT_VOICE_COUNT) return 0;
    v=&d->shadow.voice[channel];
    if(kind==0x50) {
        if(!read8(d,&packed) || packed>31) return 0;
        v->volume=packed;
    } else if(kind==0x70) {
        if(!read8(d,&packed)) return 0;
        pitch=(uint16_t)((int16_t)d->pitch[channel]+(int8_t)packed);
        d->pitch[channel]=pitch;
        increment=WavetablePitchToIncrement(pitch);
        v->increment[0]=(uint8_t)increment;
        v->increment[1]=(uint8_t)(increment>>8);
        v->increment[2]=(uint8_t)(increment>>16);
    } else {
        if(!read8(d,&packed)||!read8(d,&pitchHi)) return 0;
        pitch=(uint16_t)packed|((uint16_t)pitchHi<<8);
        d->pitch[channel]=pitch;
        increment=WavetablePitchToIncrement(pitch);
        v->increment[0]=(uint8_t)increment;
        v->increment[1]=(uint8_t)(increment>>8);
        v->increment[2]=(uint8_t)(increment>>16);
        if(kind==0x40) {
            if(!read8(d,&packed)) return 0;
            v->volume=packed&31;
            v->waveOffset=(packed>>5)<<4;
        }
        if(kind==0x40 || kind==0x60) event->resetMask|=(uint16_t)1<<channel;
    }
    event->voice[channel]=*v;
    event->changedMask|=(uint16_t)1<<channel;
    return 1;
}

static uint8_t decode_one(MEM_XDATA(TrackerDecoder) *d, MEM_XDATA(TrackerControlEvent) *event)
{
    uint8_t op, b0, b1, b2;
    uint16_t wait;
    memset(event, 0, sizeof(*event));
    for (;;) {
        if (!read8(d, &op)) { trackerLastError=0x80; return 0; }
        if (op >= 0x20 && op <= 0x29) {
            if (!decode_control(d, op, event)) { trackerLastError=0x81; return 0; }
            continue;
        }
        if ((op>=0x30&&op<=0x39)||(op>=0x40&&op<=0x49)
         || (op>=0x50&&op<=0x59)||(op>=0x60&&op<=0x69)) {
            if(!decode_compact(d,op,event)) { trackerLastError=0x83; return 0; }
            continue;
        }
        if(op>=0x70&&op<=0x79) {
            if(!decode_compact(d,op,event)) { trackerLastError=0x84; return 0; }
            continue;
        }
        if (op == 1) {
            if (!read8(d,&b0) || !b0) return 0;
            wait=b0;
        } else if (op == 2) {
            if (!read16(d,&wait) || !wait) return 0;
        } else if (op == 3) {
            if (!read8(d,&b0)||!read8(d,&b1)||!read8(d,&b2)) return 0;
            d->remainingWait=(uint32_t)b0|((uint32_t)b1<<8)|((uint32_t)b2<<16);
            if (!d->remainingWait) return 0;
            wait = d->remainingWait > 65535UL ? 65535 : (uint16_t)d->remainingWait;
            d->remainingWait -= wait;
        } else if (op == 4) {
            if(!d->lastWait) return 0;
            wait=d->lastWait;
        } else if (op == 5) {
            if(!d->lastWait || !read8(d,&b0)) return 0;
            wait=(uint16_t)((int16_t)d->lastWait+(int8_t)b0);
            if(!wait) return 0;
        } else if (op == 0) {
            event->terminal = 1;
            event->waitSamples = 1;
            return 1;
        } else { trackerLastError=0x82; return 0; }
        event->waitSamples = wait;
        d->lastWait=wait;
        return 1;
    }
}

void TrackerPlayerInit(MEM_XDATA(TrackerPlayer) *p)
{
    memset(p,0,sizeof(*p));
    p->scheduler.currentTrack=-1;
    p->scheduler.requestedTrack=-1;
    p->decoder.status=TRACKER_STOPPED;
}

uint8_t TrackerPlayerStart(MEM_XDATA(TrackerPlayer) *p, uint8_t mode)
{
    uint32_t size=storage_get_backend()==STORAGE_BACKEND_SPI ? SPI_FLASH_SIZE : ScoreSize;
    storage_init();
    stream_init(&p->scheduler.playlistStream,storage_get_base_addr(),size);
    if (size<T10P_HEADER_SIZE || stream_u8(&p->scheduler.playlistStream,0)!='T'
     || stream_u8(&p->scheduler.playlistStream,1)!='1' || stream_u8(&p->scheduler.playlistStream,2)!='0'
     || stream_u8(&p->scheduler.playlistStream,3)!='P' || stream_u8(&p->scheduler.playlistStream,4)!=1) {
        trackerLastError=0x10; p->decoder.status=TRACKER_ERROR; return 0;
    }
    p->scheduler.trackCount=stream_u16(&p->scheduler.playlistStream,6);
    p->scheduler.playlistSize=stream_u32(&p->scheduler.playlistStream,8);
    if (!p->scheduler.trackCount || p->scheduler.playlistSize>size
     || p->scheduler.playlistSize<T10P_HEADER_SIZE+(uint32_t)p->scheduler.trackCount*T10P_ENTRY_SIZE) {
        trackerLastError=0x11; return 0;
    }
    p->scheduler.mode=mode;
    p->scheduler.currentTrack=0;
    return open_track(p,0);
}

void TrackerPlayerProcess(MEM_XDATA(TrackerPlayer) *p)
{
    MEM_XDATA(TrackerDecoder) *d=&p->decoder;
    if (p->scheduler.switchPending) {
        int16_t i=p->scheduler.requestedTrack;
        p->scheduler.switchPending=0;
        if (i<0) i=p->scheduler.trackCount-1;
        if (i>=p->scheduler.trackCount) i=0;
        p->scheduler.currentTrack=i;
        if (!open_track(p,(uint16_t)i)) d->status=TRACKER_ERROR;
    }
    if (d->status!=TRACKER_PRIMING && d->status!=TRACKER_PLAYING) return;
    if (d->pendingEnd) return;
    while (!queue_full()) {
        if (d->remainingWait) {
            memset(&decodeEvent,0,sizeof(decodeEvent));
            decodeEvent.waitSamples=d->remainingWait>65535UL?65535:(uint16_t)d->remainingWait;
            d->remainingWait-=decodeEvent.waitSamples;
        } else if (!decode_one(d,&decodeEvent)) { d->status=TRACKER_ERROR; return; }
        if (decodeEvent.terminal) {
            if (d->loop) { if (!reset_loop(d)) { d->status=TRACKER_ERROR; return; } continue; }
            d->pendingEnd=1;
        }
        if (!queue_commit(&decodeEvent)) return;
        if (d->status==TRACKER_PRIMING) d->status=TRACKER_PLAYING;
        if (d->pendingEnd) return;
    }
}

void TrackerPlayerSampleTick(void) __using(1)
{
    uint8_t i, head;
    uint16_t bit;
    MEM_XDATA(TrackerControlEvent) *e;
    if (trackerQueue.countdown) { trackerQueue.countdown--; return; }
    if (trackerQueue.head==trackerQueue.tail) { trackerQueue.underruns++; return; }
    head=trackerQueue.head;
    e=&trackerQueue.slots[head];
    bit=1;
    for (i=0;i<WT_VOICE_COUNT;i++,bit<<=1) if (e->changedMask & bit) {
        if (e->resetMask & bit) wavetableSynth.voice[i].phase[0]=wavetableSynth.voice[i].phase[1]=wavetableSynth.voice[i].phase[2]=0;
        wavetableSynth.voice[i].increment[0]=e->voice[i].increment[0];
        wavetableSynth.voice[i].increment[1]=e->voice[i].increment[1];
        wavetableSynth.voice[i].increment[2]=e->voice[i].increment[2];
        wavetableSynth.voice[i].volume=e->voice[i].volume;
        wavetableSynth.voice[i].waveOffset=e->voice[i].waveOffset;
    }
    trackerQueue.countdown=e->waitSamples ? e->waitSamples-1 : 0;
    trackerQueue.head=(uint8_t)((head+1)&(TRACKER_QUEUE_LENGTH-1));
    if (e->terminal) {
        mainPlayer.decoder.status=TRACKER_STOPPED;
        WavetableSynthSilence();
    }
}

void TrackerPlayerStop(MEM_XDATA(TrackerPlayer) *p) { p->decoder.status=TRACKER_STOPPED; queue_reset(); WavetableSynthSilence(); }
void TrackerPlayerPlay(MEM_XDATA(TrackerPlayer) *p) { if (p->decoder.status==TRACKER_STOPPED) { p->scheduler.requestedTrack=p->scheduler.currentTrack<0?0:p->scheduler.currentTrack; p->scheduler.switchPending=1; } }
void TrackerPlayerNext(MEM_XDATA(TrackerPlayer) *p) { p->scheduler.requestedTrack=p->scheduler.currentTrack+1; p->scheduler.switchPending=1; }
void TrackerPlayerPrevious(MEM_XDATA(TrackerPlayer) *p) { p->scheduler.requestedTrack=p->scheduler.currentTrack-1; p->scheduler.switchPending=1; }
void TrackerPlayerSelect(MEM_XDATA(TrackerPlayer) *p,int16_t i) { if (i>=0 && i<p->scheduler.trackCount) { p->scheduler.requestedTrack=i; p->scheduler.switchPending=1; } }
