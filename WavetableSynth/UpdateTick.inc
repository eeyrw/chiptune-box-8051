    .globl _sysMsPre
    .globl _sysMs
    .area DSEG (DATA)
_sysMsPre:: .ds 1
_sysMs:: .ds 4
    .area CSEG (CODE)
_UpdateTick:
    inc _sysMsPre
    mov a,_sysMsPre
    clr c
    subb a,#32
    jc wtSysMsEnd$
    mov _sysMsPre,#0
    mov a,_sysMs
    add a,#1
    mov _sysMs,a
    mov a,(_sysMs+1)
    addc a,#0
    mov (_sysMs+1),a
    mov a,(_sysMs+2)
    addc a,#0
    mov (_sysMs+2),a
    mov a,(_sysMs+3)
    addc a,#0
    mov (_sysMs+3),a
wtSysMsEnd$:
