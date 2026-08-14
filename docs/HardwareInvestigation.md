# Hardware Investigations

This document records hardware findings that have been checked against both the
firmware and the device-specific sections of the STC8H data sheet. A finding is
not an implementation change: unresolved PCB connectivity and measured signals
remain explicitly marked as such.

## 2026-08-14: PWMA audio pair and visualization LED

### Question

Audit the current use of PWMA for two complementary Class-D audio outputs and
one sound-visualization LED. Determine whether the channels, registers, pin
multiplexing, or other enabled peripherals conflict.

### Target and sources

- Target MCU: `STC8H3K64S2`, as selected by the firmware and documented by the
  project.
- Firmware configuration: `Bsp.c`, `main.c`, `Protocol.c`, and
  `WavetableSynth/AudioRender.s`.
- Register helper definitions: `STC8xxxx_SDCC.h`.
- Authoritative pin information: `STC8H.pdf`, especially sections 4.9.1,
  4.9.2, and 4.9.4 for the `STC8H3K64S2` family.

The device-specific data-sheet tables take precedence over comments in the
generic `STC8xxxx_SDCC.h` header. That header covers several STC8 variants whose
available pins are not identical.

### Current configuration

`HardwareInit()` configures PWMA as follows:

| Function | PWMA channel/output | Requested pin | Compare register | Enabled output |
| --- | --- | --- | --- | --- |
| Audio bridge leg A | PWM2P | P1.2 | CCR2 | `CC2E`, ENO bit 2 |
| Audio bridge leg B | PWM2N | P1.3 | CCR2 | `CC2NE`, ENO bit 3 |
| Visualization LED | PWM4N | P1.7 | CCR4 | `CC4NE`, ENO bit 7 |

PWMA uses a prescaler of zero and `ARR=256`. Channel 2 and channel 4 therefore
share the same counter, carrier frequency, and phase, but use independent mode,
polarity, enable, and compare registers. `PWMA_DTR=0`; no dead time is inserted.

Timer0 consumes one byte from the audio ring at 32 kHz and writes the audio duty
to CCR2. The main thread calls `VisualizeSound()`, which derives a peak/decay
level and writes CCR4. Timer0 can interrupt a CCR4 update, but it writes CCR2,
not CCR4.

### Confirmed pin-multiplexing defect

The requested `PWM2P/P1.2` plus `PWM2N/P1.3` pair is **not available on the
STC8H3K64S2**.

The family-specific GPIO list in data-sheet section 4.9.1 contains P1.0 through
P1.2 and P1.6 through P1.7. It explicitly omits P1.3, P1.4, and P1.5. Section
4.9.2 gives these PWMA mappings:

| PWMA output | First mapping | Second mapping |
| --- | --- | --- |
| PWM2P | P1.2 | P2.2 |
| PWM2N | unavailable | P2.3 |
| PWM4P | P1.6 | P2.6 |
| PWM4N | P1.7 | P2.7 |

Consequently:

- PWM2P can appear on P1.2.
- Enabling PWM2N in `PWMA_CCER1` and `PWMA_ENO` does not create a P1.3 pin that
  this MCU does not provide.
- The complete second PWM2 mapping, P2.2/P2.3, is the direct hardware-supported
  complementary pair.
- PWM4N/P1.7 is valid for the visualization LED.

`PWM2_USE_P12P13()` in the generic header only clears the PWM2 selection bits in
`PWMA_PS`. Its comment mentions P1.3 because the header serves other devices.
The macro cannot override the target package's missing pin. The current writes
to P1 mode bit 3 likewise have no usable external P1.3 result on this MCU.

This is a pin-routing defect, not a CCR or channel collision. The current
firmware cannot provide the intended differential pair on P1.2/P1.3.

### Resource conflict matrix

| Resource | Audio PWM2 | LED PWM4 | Assessment |
| --- | --- | --- | --- |
| PWMA counter, prescaler, ARR | Shared | Shared | No corruption; LED must accept the audio carrier frequency |
| Compare register | CCR2 | CCR4 | Independent; no cross-channel overwrite |
| Output-enable bits | ENO 2/3 | ENO 7 | Independent |
| Capture/compare control | CCER1 | CCER2 | Independent |
| Pin-select bits in `PWMA_PS` | bits 2-3 | bits 6-7 | Independent |
| Dead-time register | Shared, value 0 | Shared, value 0 | No present effect on either channel |
| Update context | Timer0 ISR | Main thread | Different CCRs; interrupt interleaving does not mix the two duties |

There is therefore no direct conflict between channel 2 audio and channel 4 LED
visualization. The LED can remain on PWM4N/P1.7 when audio is moved to the valid
PWM2 mapping.

### Additional risks found during the audit

#### Outputs are enabled before audio starts

`HardwareInit()` initializes CCR2 to 256 and immediately enables PWM2P, PWM2N,
the PWMA main output, and the PWMA counter. `main()` subsequently initializes the
player and storage and pre-renders the audio ring before `StartAudioOutput()`
starts Timer0.

On a valid differential pair, this leaves the bridge at an extreme initial duty
during initialization instead of the unsigned-audio center value 128. Depending
on the external Class-D circuit, this can cause a startup pop or sustained DC
stress. A future fix should either initialize CCR2 to 128 or keep the audio
outputs/main output disabled until the ring has been primed.

#### P1.7 output mode is not explicitly configured

The firmware explicitly makes P1.6 push-pull but does not make P1.7 push-pull.
The data sheet states that pins other than P3.0/P3.1 reset to high-impedance
input. Although the peripheral output may currently drive the LED, P1.7 should
be configured explicitly so behavior does not depend on an undocumented or
reset-default interaction.

#### ADC command overlaps PWM pins

The protocol accepts any requested ADC channel without excluding active PWM
pins. On this MCU:

- ADC2 is P1.2 and overlaps the current PWM2P output.
- ADC6 and ADC7 are P1.6 and P1.7 and overlap the PWM4 pin pair/LED.
- ADC3 through ADC5 have no external P1.3 through P1.5 pins on this family.
- ADC15 is the internal 1.19 V reference rather than an external input.

Reading an overlapping or unavailable channel during playback is not a valid
board measurement. The protocol should eventually validate the channel against
the target MCU and reserve pins actively owned by PWM.

#### Compare updates are not latched atomically

Output-compare preload is disabled, and firmware writes the high and low CCR
bytes separately. There is no cross-channel conflict, but a counter update can
observe an intermediate 16-bit value. CCR2 normally has a zero high byte after
startup, limiting the steady-state audio risk. CCR4 can cross the 255/256
boundary and may produce a one-carrier-cycle LED glitch. This should be checked
only if measurements show a practical problem; LED duty accuracy does not
justify adding work to the audio ISR.

### Peripheral review

No currently enabled peripheral was found to claim the same valid output pins:

- UART1 uses P3.0/P3.1, not the alternate UART mapping on P1.6/P1.7.
- The SPI flash backend uses GPIO bit banging on P3.2 through P3.5, not hardware
  SPI on P2.2/P2.3.
- Timer2 clock output is not enabled on a PWM pin.
- The firmware uses the internal IRC rather than an external crystal on
  P1.6/P1.7.

Moving PWM2 to P2.2/P2.3 must still be checked against the actual PCB wiring.
No board schematic is present in this repository, so this audit cannot confirm
that those pins reach the Class-D inputs or are available for rework.

### Recommended implementation order

1. Confirm the MCU package marking and trace or probe the PCB connections for
   P2.2 and P2.3.
2. If accessible, select `PWM2_USE_P22P23()`, configure both P2 pins push-pull,
   and retain PWM4N/P1.7 for the LED.
3. Initialize audio at duty 128 or defer PWMA audio-output enable until the
   audio ring is primed.
4. Explicitly configure P1.7 for the intended peripheral output mode.
5. Restrict `CMD_ADC_READ` to valid, unreserved channels.
6. Keep README, architecture, testing, and maintainer guidance aligned with the
   device-specific pin limitation and the measured hardware state.

### Required hardware verification

After implementing a valid mapping, verify with an oscilloscope rather than the
UART diagnostic alone:

- Both physical audio pins switch at the expected PWMA carrier frequency.
- The two pins are strict logical complements for several duty values, including
  0, 128, and 255; no dead-time gap is expected.
- Startup stays centered or outputs remain disabled until audio begins.
- P1.7 continues to show the peak/decay duty independently of CCR2 changes.
- Timer0 underruns do not increase during active playback.

Until that measurement is complete, register configuration is verified but the
external Class-D signal path remains unverified.
