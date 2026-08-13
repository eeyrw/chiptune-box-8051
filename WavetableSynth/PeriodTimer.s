    .include "WavetableSynth.inc"
    .include "8051.inc"
    .module TRACKER_PERIOD_TIMER
    .globl _timer_isr
    .globl _audioBuffer
    .globl _audioRead
    .globl _audioWrite
    .globl _audioUnderruns
    .area REG_BANK_2 (REL,OVR,DATA)
    .ds 8
    .area CSEG (CODE)
_timer_isr:
    ar0=0x10
    push acc
    push b
    push dpl
    push dph
    push psw
    mov psw,#0x10
    setb P55
    mov dptr,#_audioRead
    movx a,@dptr
    mov r0,a
    mov dptr,#_audioWrite
    movx a,@dptr
    xrl a,r0
    jz audio_empty$
    mov dptr,#_audioBuffer
    mov a,dpl
    add a,r0
    mov dpl,a
    clr a
    addc a,dph
    mov dph,a
    movx a,@dptr
    push acc
    mov dptr,#_audioRead
    mov a,r0
    inc a
    movx @dptr,a
    pop acc
    sjmp audio_output$
audio_empty$:
    mov dptr,#_audioUnderruns
    movx a,@dptr
    inc a
    movx @dptr,a
    mov a,#128
audio_output$:
    mov r0,a
    mov dptr,#REG_PWMA_CCR2H
    clr a
    movx @dptr,a
    mov dptr,#REG_PWMA_CCR2L
    mov a,r0
    movx @dptr,a
    .include "UpdateTick.inc"
    jnb TF0,timer_on_time$
    orl (WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1),#0x80
timer_on_time$:
    clr P55
    pop psw
    pop dph
    pop dpl
    pop b
    pop acc
    reti
