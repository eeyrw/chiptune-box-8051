; Main-thread control consumer and audio renderer.
;
; C ABI and register contract:
;   * uint8_t AudioRenderProcess(void) returns the batch peak in DPL.
;   * A, B, DPTR, carry and R0-R7 are scratch, as for an ordinary SDCC call.
;   * The whole firmware uses register bank 0. No instruction changes RS0/RS1.
;
; Concurrency and publication rules:
;   * Timer0 alone advances audioRead. This routine alone stores audio samples
;     and publishes audioWrite; the byte is written before the index changes.
;   * TrackerPlayerProcess writes a complete queue slot before publishing tail.
;     This routine applies a complete slot before publishing head.
;   * At most 16 samples are rendered per call so protocol and tracker work in
;     the cooperative main loop cannot be starved by a nearly empty ring.
;
; Register lifetimes:
;   Outer batch: R5 = samples left, R6 = peak, R7 = unpublished write index.
;   Inline path: R0/R1/R4-R6 stage controls; R2:R3 is an XRAM/code pointer;
;                R5:R6:R7 becomes the signed 24-bit synthesis accumulator.
; The outer R5-R7 values therefore receive one balanced push/pop around every
; inline step. All jumps after the pushes converge at tracker_control_done$;
; no return or loop exit is reachable with those three bytes still stacked.
    .module AUDIO_RENDER
    .include "WavetableSynth.inc"
    .include "../TrackerPlayer/TrackerPlayer.inc"

    .globl _AudioRenderProcess
    .globl _audioBuffer
    .globl _audioRead
    .globl _audioWrite
    .globl _trackerQueue
    .globl _mainPlayer
    .globl _wavetableCodeBase

    ; PUSH/POP require direct addresses. These aliases deliberately name bank 0
    ; and must not be changed to the 0x08/0x10/0x18 alternate-bank ranges.
    ar5 = 0x05
    ar6 = 0x06
    ar7 = 0x07               ; register bank 0 direct addresses

    .area CSEG (CODE)
_AudioRenderProcess::
    ; Keep audioWrite private in R7 until each corresponding audioBuffer byte is
    ; complete. Returning without work is valid when the ring is already full.
    mov dptr,#_audioWrite
    movx a,@dptr
    mov r7,a                  ; unpublished write index
    mov r6,#0                 ; batch peak distance from PWM center
    mov r5,#16                ; bounded work per main-loop visit

audio_render_loop$:
    mov a,r5
    jnz audio_render_budget$
    ljmp audio_render_done$
audio_render_budget$:
    dec r5

    ; A modulo difference of 0xff means all 255 effective slots are occupied.
    ; A full 256-byte distance is forbidden because it is indistinguishable
    ; from empty when only 8-bit monotonic indices are retained.
    mov dptr,#_audioRead
    movx a,@dptr
    mov r4,a
    mov a,r7
    clr c
    subb a,r4
    cjne a,#0xff,audio_render_space$
    ljmp audio_render_done$

audio_render_space$:
    ; The inline path uses r1-r7. Preserve the outer batch state without
    ; dedicating another register bank. There is no per-sample lcall/ret.
    push ar5
    push ar6
    push ar7

    ; countdown is a little-endian 16-bit count of *additional* samples before
    ; the next event. Most samples take only this decrement path. The low-byte
    ; underflow test (0 -> 0xff) is what decides whether to borrow from R5.
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_COUNTDOWN)
    movx a,@dptr
    mov r4,a
    inc dptr
    movx a,@dptr
    mov r5,a
    orl a,r4
    jz tracker_queue_due$
    dec r4
    cjne r4,#0xff,tracker_countdown_store$
    dec r5
tracker_countdown_store$:
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_COUNTDOWN)
    mov a,r4
    movx @dptr,a
    inc dptr
    mov a,r5
    movx @dptr,a
    ljmp tracker_control_done$

tracker_queue_due$:
    ; head == tail means no decoded control is ready. Audio still has to be
    ; rendered from the current voice state; only active playback counts this
    ; condition as a producer underrun.
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_HEAD)
    movx a,@dptr
    mov r7,a
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_TAIL)
    movx a,@dptr
    cjne a,ar7,tracker_event_ready$

    ; An empty queue is only an underrun while the VM is actively playing.
    mov dptr,#(_mainPlayer + TRACKER_PLAYER_VM_STATUS)
    movx a,@dptr
    cjne a,#TRACKER_STATUS_PLAYING,tracker_empty_done$
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_UNDERRUNS)
    movx a,@dptr
    inc a
    movx @dptr,a
tracker_empty_done$:
    ljmp tracker_control_done$

tracker_event_ready$:
    ; event = &slots[(head & 3) * 57]. Four slots make the product at most 171,
    ; but MUL is retained so the assembly constant remains tied to the C layout.
    mov a,r7
    anl a,#(TRACKER_QUEUE_LENGTH - 1)
    mov b,#TRACKER_EVENT_SIZE
    mul ab
    add a,#_trackerQueue
    mov r2,a
    mov a,b
    addc a,#(_trackerQueue >> 8)
    mov r3,a

    ; Apply the ten fixed voice controls. changedMask avoids all state writes for
    ; unchanged lanes. Each five-byte control is staged before committing it so
    ; DPTR can be reused for mask lookup without partially updated voice state.
.irp Idx,0,1,2,3,4,5,6,7,8,9
    pVoice = WT_SYNTH_ABS_ADDR + Idx * WT_VOICE_SIZE
    eVoice = TRACKER_EVENT_VOICES + Idx * TRACKER_VOICE_CONTROL_SIZE

    mov dpl,r2
    mov dph,r3
    mov a,dpl
    add a,#(TRACKER_EVENT_CHANGED + (Idx / 8))
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr
    anl a,#(1 << (Idx & 7))
    jz tracker_voice_done'Idx'$

    mov dpl,r2
    mov dph,r3
    mov a,dpl
    add a,#eVoice
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr
    mov r0,a
    inc dptr
    movx a,@dptr
    mov r1,a
    inc dptr
    movx a,@dptr
    mov r4,a
    inc dptr
    movx a,@dptr
    mov r5,a
    inc dptr
    movx a,@dptr
    mov r6,a

    ; volume bit 7 selects the packed PCM interpretation:
    ;   increment[0..2] -> Code address low/high and remaining low
    ;   volume          -> PCM tag/prime flag/five-bit authored gain
    ;   waveOffset      -> remaining high
    ; The synth state reuses phase as address/count storage. increment[2] is a
    ; per-channel 16 kHz hold/prime phase, staggered by channel parity.
    mov a,r5
    jnb acc.7,tracker_voice_tonal'Idx'$
    mov (pVoice + WT_PHASE_0),r0
    mov (pVoice + WT_PHASE_1),r1
    mov (pVoice + WT_PHASE_2),r4
    mov a,r6
    mov (pVoice + WT_INCREMENT_0),a
    mov (pVoice + WT_INCREMENT_2),#(Idx & 1)
    mov a,r5
    mov (pVoice + WT_VOLUME),a
    sjmp tracker_voice_done'Idx'$

tracker_voice_tonal'Idx'$:
    ; Tonal controls use a 24-bit DDS increment. resetMask is independent from
    ; changedMask and restarts phase only for an actual retrigger; portamento and
    ; continuous parameter updates preserve phase.
    mov dpl,r2
    mov dph,r3
    mov a,dpl
    add a,#(TRACKER_EVENT_RESET + (Idx / 8))
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr
    anl a,#(1 << (Idx & 7))
    jz tracker_voice_no_reset'Idx'$
    mov (pVoice + WT_PHASE_0),#0
    mov (pVoice + WT_PHASE_1),#0
    mov (pVoice + WT_PHASE_2),#0
tracker_voice_no_reset'Idx'$:
    mov (pVoice + WT_INCREMENT_0),r0
    mov (pVoice + WT_INCREMENT_1),r1
    mov (pVoice + WT_INCREMENT_2),r4
    mov a,r5
    mov (pVoice + WT_VOLUME),a
    mov a,r6
    mov (pVoice + WT_WAVE_OFFSET),a
tracker_voice_done'Idx'$:
.endm

    ; This sample applies the event immediately, so only waitSamples - 1 future
    ; samples remain. Saturating zero avoids converting a malformed/terminal
    ; zero wait into 65535.
    mov dpl,r2
    mov dph,r3
    movx a,@dptr
    mov r4,a
    inc dptr
    movx a,@dptr
    mov r5,a
    mov a,r4
    orl a,r5
    jz tracker_wait_store$
    dec r4
    cjne r4,#0xff,tracker_wait_store$
    dec r5
tracker_wait_store$:
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_COUNTDOWN)
    mov a,r4
    movx @dptr,a
    inc dptr
    mov a,r5
    movx @dptr,a

    ; Publish queue consumption only after every changed voice and countdown
    ; byte is committed. The producer may reuse the slot once head advances.
    mov dptr,#(_trackerQueue + TRACKER_QUEUE_HEAD)
    mov a,r7
    inc a
    movx @dptr,a

    mov dpl,r2
    mov dph,r3
    mov a,dpl
    add a,#TRACKER_EVENT_TERMINAL
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr
    jz tracker_control_done$

    ; A terminal event is consumed once, marks the VM stopped and zeros all
    ; audible state before the synthesis body runs. The body then deterministically
    ; writes centered PWM rather than leaving the final note or compare latched.
    mov dptr,#(_mainPlayer + TRACKER_PLAYER_VM_STATUS)
    mov a,#TRACKER_STATUS_STOPPED
    movx @dptr,a
.irp Idx,0,1,2,3,4,5,6,7,8,9
    mov (WT_SYNTH_ABS_ADDR + Idx * WT_VOICE_SIZE + WT_VOLUME),#0
.endm
    mov (WT_SYNTH_ABS_ADDR + WT_MIX_OFFSET),#0
    mov (WT_SYNTH_ABS_ADDR + WT_MIX_OFFSET + 1),#0
    mov (WT_SYNTH_ABS_ADDR + WT_PWM_OFFSET),#128

tracker_control_done$:
    ; The included body falls through with pwmSample updated. It contains no RET
    ; and no LCALL. Restore the outer batch frame immediately afterward.
    .include "WavetableSynthStep.inc"
    pop ar7
    pop ar6
    pop ar5

    mov dptr,#_audioBuffer
    mov a,dpl
    add a,r7
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    mov a,(WT_SYNTH_ABS_ADDR + WT_PWM_OFFSET)
    movx @dptr,a
    inc r7

    ; amplitude = abs(pwmSample - 128). For PWM=0 the signed distance is -128;
    ; two's-complement negate deliberately returns unsigned 128, not +127.
    mov a,(WT_SYNTH_ABS_ADDR + WT_PWM_OFFSET)
    clr c
    subb a,#128
    jnb acc.7,audio_amplitude_ready$
    cpl a
    inc a
audio_amplitude_ready$:
    mov r4,a
    clr c
    mov a,r6
    subb a,r4
    jnc audio_peak_ready$
    mov a,r4
    mov r6,a
audio_peak_ready$:
    ; Publish only after the sample byte is complete. Timer0 can preempt before
    ; this MOVX and see the old write index, or after it and consume valid data.
    mov dptr,#_audioWrite
    mov a,r7
    movx @dptr,a
    ljmp audio_render_loop$

audio_render_done$:
    mov dpl,r6
    ret
