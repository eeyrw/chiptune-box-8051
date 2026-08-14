# Testing

Production and host tests are separate; there is no `RUN_TEST` firmware mode.

```bash
make host-test
make clean && make
```

The link report must keep the absolute synthesizer state at DATA `0x21`, leave
the stack beginning at `0x7B`, and stay within the configured 65,024-byte
program region / 3 KiB XRAM. The remaining 512 bytes are selected as EEPROM by
the STC option `program_eeprom_split=65024`.

The map must reserve only `REG_BANK_0` (8 bytes); `REG_BANK_1`, `REG_BANK_2`
and `REG_BANK_3` must all have zero length. Timer0 shares bank 0 and preserves
`A`, `DPTR`, `PSW` and `R0`. The current map reports 110 linked DATA bytes and
an 18-byte spare region at `0x0E..0x1F`.

Hardware smoke test:

```bash
make flash
python3 tools/musicbox_proto.py ping
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
```

当前板需要先断电并启动 `make flash`，待 `stcgal` 显示
`Waiting for MCU, please cycle power` 后再上电。写入过程中保持供电，直到出现
`Finishing write: done` 和 `Disconnected!`。软件 RESET 不是这块板上可靠替代
手动上电的前提。

Check `audio` repeatedly while the full 10-channel track plays. Queue underruns
must not increase during the active observation window; their absolute value can
include startup history. Use the 10-bit mute mask to isolate each voice.
P5.5 is high for the complete Timer0 ISR and can be measured to determine the
worst-case CPU duty cycle at 32 kHz.

The current P1.2/P1.3 Class-D configuration is not a valid complementary pair
on STC8H3K64S2: PWM2P exists on P1.2, but this MCU has no P1.3 pin. Do not treat
a UART `audio` response or register readback as evidence that PWM2N is present.
See [HardwareInvestigation.md](HardwareInvestigation.md) for the pin-multiplexing
audit.

After PCB connectivity has been confirmed and PWM2 is moved to the supported
P2.2/P2.3 mapping, verify both physical pins with an oscilloscope. They must be
strict logical complements for duties 0, 128 and 255, with no intentional
dead-time gap. Also confirm that startup is centered at duty 128 or that both
outputs remain disabled until the audio ring is primed. `PWMA_DTR` remains zero
because the outputs drive opposite bridge legs, not two switches in one half
bridge. This physical relationship cannot be established by UART diagnostics.

All serial checks must run sequentially. Two clients opening the same UART at
once can exchange each other's response frames and produce false protocol
errors. Compare the underrun counter before and after one active playback window;
its absolute value can include startup or stopped-ring history.

The complete new-song acceptance workflow is in [UsingXM.md](UsingXM.md).
