; Ten fixed voices. Tracker semantics and envelopes run in the main loop;
; this 32 kHz hot path only generates two noise sources, mixes and advances phase.
    .module WAVETABLE_SYNTH_STEP
    .include "WavetableSynth.inc"

    .globl _WavetableSynthStep
    .globl _wavetableSynth
    .globl _waveTables

    .area CSEG (CODE)
_WavetableSynthStep::
    mov r5,#0
    mov r6,#0
    mov r7,#0

    ; One long-period Galois LFSR shared by all mode-6 voices.
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET)
    jnb acc.0,wt_long_zero$
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1)
    clr c
    rrc a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1),a
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET)
    rrc a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET),a
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1)
    xrl a,#0xB4
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1),a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_SAMPLE_OFFSET),#96
    sjmp wt_long_done$
wt_long_zero$:
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1)
    clr c
    rrc a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET + 1),a
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET)
    rrc a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_OFFSET),a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_SAMPLE_OFFSET),#0xA0
wt_long_done$:

    ; A short 7-bit source gives metallic/periodic mode-7 percussion.
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_OFFSET)
    jnb acc.0,wt_short_zero$
    clr c
    rrc a
    xrl a,#0x60
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_OFFSET),a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_SAMPLE_OFFSET),#80
    sjmp wt_short_done$
wt_short_zero$:
    clr c
    rrc a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_OFFSET),a
    mov (WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_SAMPLE_OFFSET),#0xB0
wt_short_done$:

.irp Idx,0,1,2,3,4,5,6,7,8,9
    pVoice = WT_SYNTH_ABS_ADDR + Idx * WT_VOICE_SIZE

    ; A muted voice keeps its phase, so unmuting does not retrigger the note.
    mov a,#(1 << (Idx & 7))
.iflt Idx-8
    anl a,(WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET)
.else
    anl a,(WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1)
.endif
    jnz wt_phase'Idx'$

    mov a,(pVoice + WT_VOLUME)
    jz wt_phase'Idx'$
    mov r4,a

    mov a,(pVoice + WT_WAVE_OFFSET)
    cjne a,#0x60,wt_try_short_noise'Idx'$
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_SAMPLE_OFFSET)
    sjmp wt_sample'Idx'$
wt_try_short_noise'Idx'$:
    cjne a,#0x70,wt_table'Idx'$
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_SAMPLE_OFFSET)
    sjmp wt_sample'Idx'$

    ; Six tonal waveforms use 16 samples and a prepacked waveform << 4 offset.
wt_table'Idx'$:
    mov a,(pVoice + WT_PHASE_2)
    swap a
    anl a,#0x0f
    orl a,(pVoice + WT_WAVE_OFFSET)
    mov dptr,#_waveTables
    movc a,@a+dptr
wt_sample'Idx'$:
    mov b,r4
    jb a.7,wt_neg'Idx'$

    mul ab
    add a,r5
    mov r5,a
    xch a,b
    addc a,r6
    mov r6,a
    clr a
    addc a,r7
    mov r7,a
    sjmp wt_phase'Idx'$

wt_neg'Idx'$:
    mul ab
    add a,r5
    mov r5,a
    xch a,b
    addc a,r6
    mov r6,a
    clr a
    addc a,r7
    mov r7,a
    clr c
    mov a,r6
    subb a,r4
    mov r6,a
    mov a,r7
    subb a,#0
    mov r7,a

wt_phase'Idx'$:
    mov a,(pVoice + WT_INCREMENT_0)
    add a,(pVoice + WT_PHASE_0)
    mov (pVoice + WT_PHASE_0),a
    mov a,(pVoice + WT_INCREMENT_1)
    addc a,(pVoice + WT_PHASE_1)
    mov (pVoice + WT_PHASE_1),a
    mov a,(pVoice + WT_INCREMENT_2)
    addc a,(pVoice + WT_PHASE_2)
    mov (pVoice + WT_PHASE_2),a
.endm

    ; Signed 24-bit sum >> 4, saturated to signed 8-bit, then biased for PWM.
    mov a,r6
    mov r5,a
    mov a,r7
    mov r6,a
    mov a,r5
    add a,r5
    mov r5,a
    mov a,r6
    addc a,r6
    mov r6,a
    mov a,r5
    add a,r5
    mov r5,a
    mov a,r6
    addc a,r6
    mov r6,a
    mov a,r5
    add a,r5
    mov r5,a
    mov a,r6
    addc a,r6
    mov r6,a
    mov a,r5
    add a,r5
    mov r5,a
    mov a,r6
    addc a,r6
    mov r6,a
    mov (WT_SYNTH_ABS_ADDR + WT_MIX_OFFSET),r5
    mov (WT_SYNTH_ABS_ADDR + WT_MIX_OFFSET + 1),r6
    mov a,r6
    jz wt_positive$
    cjne a,#0xff,wt_clip_sign$
    mov a,r5
    jb a.7,wt_output$
    sjmp wt_clip_negative$
wt_positive$:
    mov a,r5
    jnb a.7,wt_output$
wt_clip_positive$:
    mov a,#127
    sjmp wt_output$
wt_clip_sign$:
    jb a.7,wt_clip_negative$
    sjmp wt_clip_positive$
wt_clip_negative$:
    mov a,#0x80
wt_output$:
    add a,#128
    mov (WT_SYNTH_ABS_ADDR + WT_PWM_OFFSET),a
    ret
