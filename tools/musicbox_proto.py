#!/usr/bin/env python3
"""Serial client for the Tracker10 8051 firmware."""

from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

import serial

SYNC = 0x5A
RSP_FLAG = 0x80

CMD_PING = 0x00
CMD_GET_INFO = 0x01
CMD_RESET = 0x02
CMD_UPTIME = 0x03
CMD_MEM_INFO = 0x04
CMD_AUDIO_INFO = 0x05
CMD_ADC_READ = 0x06
CMD_SYS_INFO = 0x08
CMD_PANIC = 0x0D
CMD_PLAY = 0x10
CMD_STOP = 0x11
CMD_PREV = 0x12
CMD_NEXT = 0x13
CMD_SET_SONG = 0x14
CMD_GET_STATUS = 0x15
CMD_FORMAT_INFO = 0x17
CMD_CHANNEL_MUTE = 0x18
CMD_FLASH_INFO = 0x20
CMD_FLASH_ERASE = 0x21
CMD_FLASH_ERASE_ALL = 0x22
CMD_FLASH_READ = 0x23
CMD_FLASH_WRITE = 0x24
CMD_FLASH_READ_ID = 0x25

STATUS_NAMES = {
    0x00: "OK",
    0x01: "UNKNOWN_CMD",
    0x02: "BAD_CHECKSUM",
    0x03: "BAD_LEN",
    0x04: "FLASH_ERR",
    0x05: "NOT_SUPPORTED",
    0x06: "INVALID_ADDR",
    0x07: "INVALID_PARAM",
}


def calc_csum(data: bytes) -> int:
    result = 0
    for value in data:
        result ^= value
    return result


def build_frame(command: int, payload: bytes = b"") -> bytes:
    if not 0 <= command <= 0x7F or len(payload) > 250:
        raise ValueError("command or payload is outside protocol limits")
    body = bytes((command, len(payload))) + payload
    return bytes((SYNC,)) + body + bytes((calc_csum(body),))


def parse_response(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < 5 or data[0] != SYNC or not data[1] & RSP_FLAG:
        raise ValueError("invalid response header")
    length = data[3]
    if len(data) != 5 + length:
        raise ValueError("invalid response length")
    if calc_csum(data[1:-1]) != data[-1]:
        raise ValueError("invalid response checksum")
    return data[1] & 0x7F, data[2], data[4:-1]


class Tracker10Client:
    def __init__(self, port: str, baud: int = 115200):
        self.serial = serial.Serial(port, baud, timeout=1.0)

    def close(self) -> None:
        self.serial.close()

    def command(self, command: int, payload: bytes = b"", timeout: float = 3.0) -> bytes:
        self.serial.reset_input_buffer()
        self.serial.write(build_frame(command, payload))
        self.serial.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = self.serial.read(1)
            if value == bytes((SYNC,)):
                break
        else:
            raise RuntimeError("no response from device")
        header = self.serial.read(3)
        if len(header) != 3:
            raise RuntimeError("truncated response header")
        tail = self.serial.read(header[2] + 1)
        if len(tail) != header[2] + 1:
            raise RuntimeError("truncated response payload")
        response_command, status, data = parse_response(bytes((SYNC,)) + header + tail)
        if response_command != command:
            raise RuntimeError(f"response command {response_command:#x} does not match {command:#x}")
        if status:
            raise RuntimeError(f"device returned {STATUS_NAMES.get(status, hex(status))}")
        return data

    def simple(self, command: int, message: str) -> None:
        self.command(command)
        print(message)

    def ping(self) -> None:
        print(self.command(CMD_PING).decode("ascii", errors="replace"))

    def info(self) -> None:
        major, minor, backend, tracks = struct.unpack("BBBB", self.command(CMD_GET_INFO))
        print(f"Firmware: v{major}.{minor}")
        print(f"Storage:  {'SPI Flash' if backend else 'Internal Flash'}")
        print(f"Tracks:   {tracks}")

    def uptime(self) -> None:
        milliseconds, = struct.unpack("<I", self.command(CMD_UPTIME))
        print(f"Uptime: {milliseconds} ms")

    def memory(self) -> None:
        stack_pointer, free = struct.unpack("BB", self.command(CMD_MEM_INFO)[:2])
        print(f"SP:         {stack_pointer:#04x}")
        print(f"Free stack: {free} bytes")

    def audio(self) -> None:
        data = self.command(CMD_AUDIO_INFO)
        values = struct.unpack("BBBBBBBB", data[:8])
        mix = struct.unpack("<h", bytes(values[:2]))[0]
        state = values[6] | values[7] << 8
        muted = state & 0x07FF
        print(f"Mix/PWM:    {mix:+d} / {values[2]}")
        print(f"Muted:      {muted:#05x}")
        level = (values[5] - values[4]) & 0xFF
        print(f"Buffer:     level={level}/255 read={values[4]} write={values[5]} underruns={values[3]}")
        print(f"Diagnostics: clip={bool(state & 0x4000)} isr_overrun={bool(state & 0x8000)}")
        if len(data) >= 10:
            active, = struct.unpack_from("<H", data, 8)
            print(f"PCM voices: {active:#05x}")

    def format_info(self) -> None:
        magic, version, channels, rate = struct.unpack("<4sBBH", self.command(CMD_FORMAT_INFO))
        print(f"Format:     {magic.decode()} v{version}")
        print(f"Rate:       {rate} Hz")
        print(f"Channels:   {channels}")

    def mute(self, mask: int) -> None:
        if not 0 <= mask <= 0x7FF:
            raise ValueError("mute mask must be in range 0x000..0x7ff")
        self.command(CMD_CHANNEL_MUTE, struct.pack("<H", mask))
        print(f"Mute mask: {mask:#05x}")

    def adc(self, channel: int) -> None:
        value, = struct.unpack(">H", self.command(CMD_ADC_READ, bytes((channel,))))
        print(f"ADC{channel}: {value}")

    def status(self) -> None:
        data = self.command(CMD_GET_STATUS)
        if len(data) >= 10:
            current, count, state, parser_error, order, row, tick, speed = struct.unpack("<BBBBHHBB", data[:10])
        elif len(data) == 4:
            current, count, state, parser_error = struct.unpack("BBBB", data)
            order = row = tick = speed = 0
        else:
            current, count, state = struct.unpack("BBB", data)
            parser_error = 0
            order = row = tick = speed = 0
        print(f"Track: {current}/{count}")
        print(f"State: {state} ({ {0: 'stopped', 1: 'playing', 2: 'error'}.get(state, '?') })")
        print(f"Parser error: {parser_error:#04x}")
        if len(data) >= 10:
            print(f"Position: order={order} row={row} tick={tick}/{speed}")

    def select(self, index: int) -> None:
        self.command(CMD_SET_SONG, bytes((index,)))
        print(f"Selected track {index}.")

    def flash_info(self) -> None:
        data=self.command(CMD_FLASH_INFO)
        size,sector=struct.unpack(">IH",data[3:])
        print(f"JEDEC:  {data[:3].hex().upper()}")
        print(f"Size:   {size} bytes")
        print(f"Sector: {sector} bytes")

    def flash_read(self,address: int,length: int,output: Path|None) -> None:
        if not 0<=length<=120:
            raise ValueError("one flash-read command supports 0..120 bytes")
        data=self.command(CMD_FLASH_READ,struct.pack("<IH",address,length))
        if output:
            output.write_bytes(data); print(f"Read {len(data)} bytes to {output}")
        else:
            sys.stdout.buffer.write(data)

    def flash_write(self,address: int,source: Path) -> None:
        data=source.read_bytes()
        for offset in range(0,len(data),124):
            chunk=data[offset:offset+124]
            self.command(CMD_FLASH_WRITE,struct.pack("<I",address+offset)+chunk)
            print(f"\rWriting {offset+len(chunk)}/{len(data)}",end="",flush=True)
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("command")
    parser.add_argument("args", nargs="*")
    args = parser.parse_args()
    client = Tracker10Client(args.port, args.baud)
    try:
        command = args.command
        if command == "ping": client.ping()
        elif command == "info": client.info()
        elif command == "uptime": client.uptime()
        elif command == "mem": client.memory()
        elif command == "audio": client.audio()
        elif command == "format": client.format_info()
        elif command == "mute": client.mute(int(args.args[0], 0))
        elif command == "adc": client.adc(int(args.args[0], 0))
        elif command == "status": client.status()
        elif command == "play": client.simple(CMD_PLAY, "Playing.")
        elif command == "stop": client.simple(CMD_STOP, "Stopped.")
        elif command == "panic": client.simple(CMD_PANIC, "Stopped and silenced.")
        elif command == "prev": client.simple(CMD_PREV, "Previous track requested.")
        elif command == "next": client.simple(CMD_NEXT, "Next track requested.")
        elif command == "track": client.select(int(args.args[0], 0))
        elif command == "flash-info": client.flash_info()
        elif command == "flash-id": print(client.command(CMD_FLASH_READ_ID).hex().upper())
        elif command == "flash-read": client.flash_read(int(args.args[0],0),int(args.args[1],0),Path(args.args[2]) if len(args.args)>2 else None)
        elif command == "flash-write": client.flash_write(int(args.args[0],0),Path(args.args[1]))
        elif command == "flash-erase": client.command(CMD_FLASH_ERASE,struct.pack("<I",int(args.args[0],0)),timeout=5.0)
        elif command == "flash-erase-all": client.command(CMD_FLASH_ERASE_ALL,timeout=35.0)
        elif command == "reset":
            client.serial.write(build_frame(CMD_RESET))
            client.serial.flush()
            print("Reset command sent.")
        else:
            parser.error(f"unknown command: {command}")
    except (IndexError, ValueError, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
