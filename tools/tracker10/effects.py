"""Shared tracker effect lowering for XM/MOD frontends.

Device VM (TrackerPlayer.c) is the semantic authority. Host frontends only
normalize source-format quirks into the T10 effect set documented in T10Format.md.
"""
from __future__ import annotations

# Effects executed by the device VM.
DEVICE_EFFECTS = frozenset({
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F,
})

# E0x subcommands the device implements.
DEVICE_EXX = frozenset({
    0x1,  # fine porta up
    0x2,  # fine porta down
    0x6,  # pattern loop
    0x9,  # retrigger
    0xA,  # fine vol up
    0xB,  # fine vol down
    0xC,  # note cut
    0xD,  # note delay
    0xE,  # pattern delay
})


# Resource lowering policy for host compile (not a device format field).
# wave: prefer 16-point wavetables (default; smaller Flash, short hits may sustain)
# pcm:  prefer PCM one-shots for non-looped / long loops (MOD-like; larger Flash)
RESOURCE_POLICY_WAVE = "wave"
RESOURCE_POLICY_PCM = "pcm"
RESOURCE_POLICIES = frozenset({RESOURCE_POLICY_WAVE, RESOURCE_POLICY_PCM})
DEFAULT_RESOURCE_POLICY = RESOURCE_POLICY_WAVE
MAX_WAVE_LOOP = 64

# When True, PCM instruments used on multiple notes are split into per-note
# variants (larger Flash, pitched one-shots). Default off: bake most-common note.
DEFAULT_MULTI_NOTE_PCM = False


def scale_sample_offset(param: int, source_len: int, pcm_len: int) -> int:
    """Map XM/MOD 9xx (256 source frames) onto compiled PCM skip units.

    Device applies skip = param * 256 into the PCM byte stream. Scale so that
    the relative position in the source is preserved after resampling.
    """
    if not param or source_len <= 0 or pcm_len <= 0:
        return param & 0xFF
    # new_param * 256 / pcm_len ≈ param * 256 / source_len
    scaled = (param * pcm_len + source_len // 2) // source_len
    if scaled < 1:
        scaled = 1
    if scaled > 255:
        scaled = 255
    return scaled
