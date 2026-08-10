    .module WAVETABLE_SYNTH_STATE
    .include "WavetableSynth.inc"

    .globl _wavetableSynth

    .area IABS (ABS,DATA)
    .org WT_SYNTH_ABS_ADDR
_wavetableSynth::
    .ds WT_SYNTH_DATA_SIZE
