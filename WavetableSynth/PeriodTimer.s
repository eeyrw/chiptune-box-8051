; 32 kHz Timer0 audio consumer.
;
; Ownership and timing invariants:
;   * The main thread owns audioWrite and publishes it only after storing a
;     complete byte in audioBuffer[]. This ISR alone advances audioRead.
;   * Empty is read == write. The producer never permits a 256-byte distance,
;     so all other modulo-256 differences represent valid buffered data.
;   * PWM2N is inverted by the PWMA peripheral. This ISR writes the original
;     unsigned sample only once, to CCR2; it must not invert a second value in
;     software.
;   * Every firmware path uses register bank 0. The ISR touches A, DPTR, DPS,
;     PSW flags and R0, and therefore saves exactly those. B and R1-R7 are
;     neither read nor written here or by the inline UpdateTick body. DPS is
;     forced to DPTR0 so dual-DPTR main-thread helpers cannot divert ISR DPTR.
;   * P5.5 brackets the complete assembly ISR body, including context saves and
;     restores, so its high pulse is a conservative execution-time measurement.
    .include "WavetableSynth.inc"
    .include "8051.inc"
    .module TRACKER_PERIOD_TIMER
    .globl _timer_isr
    .globl _audioBuffer
    .globl _audioRead
    .globl _audioWrite
    .globl _audioUnderruns
    .area CSEG (CODE)
_timer_isr:
    ar0=0x00
    setb P55
    push acc
    push dpl
    push dph
    push 0xE3
    push psw
    push ar0
    ; Force DPTR0 so dual-DPTR score_copy cannot leave DPS=1 across the ISR.
    mov 0xE3,#0

    ; Snapshot the consumer index in R0. audioWrite may change only in the main
    ; thread, but each byte access is atomic on the 8051, so one read is enough.
    mov dptr,#_audioRead
    movx a,@dptr
    mov r0,a
    mov dptr,#_audioWrite
    movx a,@dptr
    xrl a,r0
    jz audio_empty$

    ; DPTR = audioBuffer + read. The ring is exactly 256 bytes, so the low-byte
    ; addition plus carry is sufficient and index wrap needs no branch.
    mov dptr,#_audioBuffer
    mov a,dpl
    add a,r0
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr

    ; A now contains the sample, while R0 still contains the old index. Preserve
    ; the sample for the few instructions needed to publish read + 1. This
    ; balanced temporary push is nested inside the fixed ISR context frame.
    push acc
    mov dptr,#_audioRead
    mov a,r0
    inc a
    movx @dptr,a
    pop acc
    sjmp audio_output$
audio_empty$:
    ; Keep the carrier centered on underrun. The counter intentionally wraps at
    ; 255; diagnostics compare its value over an active playback interval.
    mov dptr,#_audioUnderruns
    movx a,@dptr
    inc a
    movx @dptr,a
    mov a,#128
audio_output$:
    ; ARR is 0x0100 and valid audio compares are 0x0000..0x00ff. Writing high
    ; then low updates the shared compare used by PWM2P and hardware-inverted
    ; PWM2N. R0 temporarily carries the sample because CLR A is needed for CCRH.
    mov r0,a
    mov dptr,#REG_PWMA_CCR2H
    clr a
    movx @dptr,a
    mov dptr,#REG_PWMA_CCR2L
    mov a,r0
    movx @dptr,a

    ; Derive the millisecond clock from exactly 32 audio interrupts. TF0 is
    ; automatically cleared on ISR entry; if it is set again already, another
    ; period elapsed before completion and the sticky overrun bit is latched.
    .include "UpdateTick.inc"
    jnb TF0,timer_on_time$
    orl (WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1),#0x80
timer_on_time$:
    pop ar0
    pop psw
    pop 0xE3
    pop dph
    pop dpl
    pop acc
    clr P55
    reti
