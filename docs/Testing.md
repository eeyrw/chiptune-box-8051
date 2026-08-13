# Testing

Production and host tests are separate; there is no `RUN_TEST` firmware mode.

```bash
make host-test
make clean && make
```

The link report must keep the absolute synthesizer state at DATA `0x21`, leave
the stack beginning at `0x7B`, and stay within 64 KiB code / 3 KiB XRAM.

Hardware smoke test:

```bash
make flash
python3 tools/musicbox_proto.py ping
python3 tools/musicbox_proto.py status
python3 tools/musicbox_proto.py audio
```

Check `audio` repeatedly while the full 10-channel track plays. Queue underruns
should stay zero after startup. Use the 10-bit mute mask to isolate each voice.
P5.5 is high for the complete Timer0 ISR and can be measured to determine the
worst-case CPU duty cycle at 32 kHz.
