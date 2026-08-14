; Direct Code Flash (MOVC) helpers for the built-in Score image.
; SDCC large model MCS51:
;   16-bit first arg  -> DPL/DPH
;   further args      -> stack (push order: last arg first)
;   u8  ret -> DPL
;   u16 ret -> DPL/DPH
;   u32 ret -> DPL/DPH (lo16) + B + A (hi16)
;
; score_copy_xdata(codeAddr, dst, len):
;   After lcall: @SP=retH, @SP-1=retL, @SP-2=dstH, @SP-3=dstL, @SP-4=len
; Uses STC8 dual-DPTR (DPS) so MOVC and MOVX do not thrash a single DPTR.
    .module ScoreFlash
    .globl _score_u8
    .globl _score_u16
    .globl _score_u32
    .globl _score_copy_xdata
    .globl _score_copy48_xdata
    .area CSEG (CODE)

DPS  = 0xE3
DPL1 = 0xE4
DPH1 = 0xE5

_score_u8:
    clr a
    movc a,@a+dptr
    mov dpl,a
    ret

_score_u16:
    clr a
    movc a,@a+dptr
    mov r2,a
    inc dptr
    clr a
    movc a,@a+dptr
    mov dpl,r2
    mov dph,a
    ret

_score_u32:
    clr a
    movc a,@a+dptr
    mov r2,a
    inc dptr
    clr a
    movc a,@a+dptr
    mov r3,a
    inc dptr
    clr a
    movc a,@a+dptr
    mov b,a
    inc dptr
    clr a
    movc a,@a+dptr
    mov dpl,r2
    mov dph,r3
    ret

; Generic copy: stack @SP-4=len, @SP-3=dstL, @SP-2=dstH
_score_copy_xdata:
    mov a,sp
    clr c
    subb a,#4
    mov r0,a
    mov a,@r0
    jz scx_done
    mov r2,a
    inc r0
    mov DPL1,@r0
    inc r0
    mov DPH1,@r0
scx_loop:
    clr a
    movc a,@a+dptr
    inc dptr
    inc DPS
    movx @dptr,a
    inc dptr
    dec DPS
    djnz r2,scx_loop
scx_done:
    ret

; Fixed 48-byte copy: push dst_lo; push dst_hi; DPTR=code; lcall
; After lcall: @SP=retH, @SP-1=retL, @SP-2=dstH, @SP-3=dstL
_score_copy48_xdata:
    mov a,sp
    clr c
    subb a,#3
    mov r0,a            ; -> dstL
    mov DPL1,@r0
    inc r0
    mov DPH1,@r0
    mov r2,#48
sc48_loop:
    clr a
    movc a,@a+dptr
    inc dptr
    inc DPS
    movx @dptr,a
    inc dptr
    dec DPS
    djnz r2,sc48_loop
    ret
