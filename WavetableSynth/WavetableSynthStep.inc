; Inline ten-voice synthesis body. AudioRender.s provides symbols and layout.
;
; Entry: bank 0 selected; absolute voice state is complete for this sample.
; Scratch: R1 is mute/PCM-path state, R2:R3 is the wavetable base, R4 is gain,
;          R5:R6:R7 is a signed 24-bit accumulator, A/B/DPTR/carry are clobbered.
; Exit: pwmSample and signed mixOut are updated; execution falls through to the
;       three outer-register POPs in AudioRender.s. There is intentionally no
;       callable entry point, LCALL or RET in this include file.
    ; The active song's 256-byte wavetable bank lives in internal Code Flash.
    ; Its base changes only at track open, so fetch it once for all ten lanes.
    mov dptr,#_wavetableCodeBase
    movx a,@dptr
    mov r2,a
    inc dptr
    movx a,@dptr
    mov r3,a

    mov r5,#0
    mov r6,#0
    mov r7,#0

    ; Advance each shared noise source exactly once per output sample, regardless
    ; of how many channels select it. Advancing inside the lane macro would make
    ; the noise pitch and sequence depend on voice count.
    ; One long-period Galois LFSR is shared by all long-noise voices.
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

    ; A short 7-bit source gives metallic/periodic percussion.
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

    ; Muting suppresses mixing while DDS phase or PCM position keeps advancing,
    ; so unmuting a diagnostic channel does not restart or time-shift the song.
    mov r1,#0
    mov a,#(1 << (Idx & 7))
.iflt Idx-8
    anl a,(WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET)
.else
    anl a,(WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1)
.endif
    jz wt_not_muted'Idx'$
    inc r1
wt_not_muted'Idx'$:

    mov a,(pVoice + WT_VOLUME)
    jnz wt_has_volume'Idx'$
    ljmp wt_phase'Idx'$
wt_has_volume'Idx'$:
    jb a.7,wt_pcm'Idx'$
    mov r4,a
    mov a,r1
    jz wt_tonal'Idx'$
    ljmp wt_phase'Idx'$
wt_tonal'Idx'$:

    mov a,(pVoice + WT_WAVE_OFFSET)
    cjne a,#WT_MODE_NOISE_LONG,wt_try_short_noise'Idx'$
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_LONG_SAMPLE_OFFSET)
    sjmp wt_sample'Idx'$
wt_try_short_noise'Idx'$:
    cjne a,#WT_MODE_NOISE_SHORT,wt_table'Idx'$
    mov a,(WT_SYNTH_ABS_ADDR + WT_NOISE_SHORT_SAMPLE_OFFSET)
    sjmp wt_sample'Idx'$

    ; Song-specific tonal waves contain 16 signed samples. phase[2].7..4 selects
    ; the phase cell and waveOffset is prepacked as ID << 4, making OR produce
    ; the complete 0..255 MOVC offset without a multiply in each lane.
wt_table'Idx'$:
    mov a,(pVoice + WT_PHASE_2)
    swap a
    anl a,#0x0f
    orl a,(pVoice + WT_WAVE_OFFSET)
    mov dpl,r2
    mov dph,r3
    movc a,@a+dptr
    sjmp wt_sample'Idx'$

    ; In PCM mode phase[0..1] is the Code Flash cursor, phase[2] and
    ; increment[0] are a 16-bit remaining-sample count, increment[1] caches the
    ; current sample, and increment[2] is a per-voice divide-by-two counter.
    ; Volume bit 7 tags PCM, bit 6 requests cache priming, and the low five bits
    ; retain mixer gain. Scale the 5-bit PCM control into the tonal lane's
    ; 7-bit gain domain without applying any PCM-specific boost. increment[2]
    ; holds one output frame after each fetch, so each 16 kHz byte is emitted for
    ; two 32 kHz render samples. Bit 6 primes a new trigger before cached reuse.
wt_pcm'Idx'$:
    anl a,#0x1f
    mov r4,a
    add a,r4
    mov r4,a
    add a,r4
    mov r4,a
    mov a,(pVoice + WT_PHASE_2)
    orl a,(pVoice + WT_INCREMENT_0)
    jnz wt_pcm_active'Idx'$
    mov a,(pVoice + WT_INCREMENT_2)
    jnz wt_pcm_active'Idx'$
    mov (pVoice + WT_VOLUME),#0
    sjmp wt_voice_done'Idx'$
wt_pcm_active'Idx'$:
    mov a,(pVoice + WT_VOLUME)
    jnb a.6,wt_pcm_primed'Idx'$
    mov a,(pVoice + WT_INCREMENT_2)
    jz wt_pcm_fetch'Idx'$
    dec (pVoice + WT_INCREMENT_2)
    sjmp wt_voice_done'Idx'$
wt_pcm_primed'Idx'$:
    mov a,(pVoice + WT_INCREMENT_2)
    jz wt_pcm_fetch'Idx'$
    dec (pVoice + WT_INCREMENT_2)
    sjmp wt_pcm_cached'Idx'$
wt_pcm_fetch'Idx'$:
    ; MOVC is safe here because T10M v4 resources are accepted only from internal
    ; Code Flash. Decrement the 16-bit remaining count after the successful fetch;
    ; the final cached byte still receives its complete two-frame hold.
    mov dpl,(pVoice + WT_PHASE_0)
    mov dph,(pVoice + WT_PHASE_1)
    clr a
    movc a,@a+dptr
    mov (pVoice + WT_INCREMENT_1),a
    mov (pVoice + WT_INCREMENT_2),#1
    anl (pVoice + WT_VOLUME),#0xBF
    inc (pVoice + WT_PHASE_0)
    mov a,(pVoice + WT_PHASE_0)
    jnz wt_pcm_count'Idx'$
    inc (pVoice + WT_PHASE_1)
wt_pcm_count'Idx'$:
    mov a,(pVoice + WT_PHASE_2)
    jnz wt_pcm_count_low'Idx'$
    dec (pVoice + WT_INCREMENT_0)
wt_pcm_count_low'Idx'$:
    dec (pVoice + WT_PHASE_2)
wt_pcm_cached'Idx'$:
    mov a,r1
    jnz wt_voice_done'Idx'$
    mov a,(WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1)
    jb acc.2,wt_voice_done'Idx'$
    mov a,(pVoice + WT_INCREMENT_1)
    mov r1,#1
wt_sample'Idx'$:
    ; A is a signed 8-bit sample and R4 is unsigned gain. MUL AB is unsigned, so
    ; the negative path corrects the high product byte afterward:
    ;   unsigned(sample)*gain - 256*gain == signed(sample)*gain (mod 65536).
    ; Carry/borrow is extended through R7, preserving a signed 24-bit sum across
    ; all ten voices without per-lane saturation or authored-gain changes.
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
    sjmp wt_after_sample'Idx'$

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

wt_after_sample'Idx'$:
    mov a,r1
    jnz wt_voice_done'Idx'$
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
wt_voice_done'Idx'$:
.endm

    ; Tonal controls use 7-bit gain. Arithmetic-shift the signed 24-bit sum once,
    ; then select its high 16 bits: this is exactly signed sum >> 9 (one explicit
    ; shift plus the implicit eight-bit byte selection), not sum >> 8 or >> 7.
    ; mixOut keeps that signed 16-bit diagnostic value before clipping.
    ;
    ; A valid signed 8-bit result has high byte 0x00 with low bit7 clear, or high
    ; byte 0xff with low bit7 set. All other sign combinations saturate to +127
    ; or -128 and latch the sticky clip diagnostic before adding the PWM bias.
    mov a,r7
    mov c,acc.7
    rrc a
    mov r7,a
    mov a,r6
    rrc a
    mov r6,a
    mov a,r5
    rrc a
    mov r5,a
    mov a,r6
    mov r5,a
    mov a,r7
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
    orl (WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1),#0x40
    mov a,#127
    sjmp wt_output$
wt_clip_sign$:
    jb a.7,wt_clip_negative$
    sjmp wt_clip_positive$
wt_clip_negative$:
    orl (WT_SYNTH_ABS_ADDR + WT_MUTE_OFFSET + 1),#0x40
    mov a,#0x80
wt_output$:
    add a,#128
    mov (WT_SYNTH_ABS_ADDR + WT_PWM_OFFSET),a
