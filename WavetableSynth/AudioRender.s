    .module AUDIO_RENDER
    .globl _AudioRenderOne
    .globl _TrackerPlayerSampleTick
    .globl _WavetableSynthStep

    .area CSEG (CODE)
_AudioRenderOne::
    push psw
    mov psw,#0x08
    lcall _TrackerPlayerSampleTick
    lcall _WavetableSynthStep
    pop psw
    ret
