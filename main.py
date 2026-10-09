import asyncio
import os
import sys
from bleak import BleakScanner, BleakClient

DEVICE_NAME_PREFIX = "BSC300"


def nus(kind: str, last: str) -> str:
    return f"6e40000{kind}-b5a3-f393-e0a9-e50e24dcca{last}"


# The device has 4 NUS-like services: the phone uses 9e for commands and 6e for file data.
CTRL_WRITE, CTRL_NOTIFY = nus("2", "9e"), nus("3", "9e")
DATA_WRITE, DATA_NOTIFY = nus("2", "6e"), nus("3", "6e")
AUX_NOTIFY = [nus("3", "8e"), nus("3", "7e")]

CHUNK = 244


def crc8_maxim(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def frame(cmd: int, kind: int, payload: bytes = b"", b3: int = 0xFF) -> bytes:
    """20-byte header (byte 9 = CRC8 of payload, byte 19 = CRC8 of bytes 0..18) + payload."""
    hdr = bytes([0x01, cmd, 0xFF, b3, kind, 0xFF, 0xFF, 0x00, len(payload), crc8_maxim(payload), 0x01]) + b"\xff" * 8
    return hdr + bytes([crc8_maxim(hdr)]) + payload


def proto_cmd(cmd: int, arg: int) -> bytes:
    return b"\x08" + varint(cmd) + b"\x10" + varint(arg)


def file_header(name: str, size: int, file_id: int) -> bytes:
    name_b = name.encode()
    return (
        b"\x08\x15\x10\x03\x18\x02"
        + b"\x20" + varint(size)
        + b"\x28" + varint(file_id)
        + b"\x32" + varint(len(name_b)) + name_b
        + b"\x3a\x03fit"
    )


def make_notifier(tag: str, queue: asyncio.Queue):
    def handler(_sender, data: bytearray):
        print(f"[<<< {tag}] {bytes(data).hex(' ')}")
        queue.put_nowait((tag, bytes(data)))
    return handler


async def wait_for(queue: asyncio.Queue, tag: str, cmd: int, timeout: float = 5.0) -> bytes:
    async def _loop():
        while True:
            t, d = await queue.get()
            if t == tag and len(d) > 1 and d[1] == cmd:
                return d
    return await asyncio.wait_for(_loop(), timeout)

async def send_track_file(file_path: str, file_id: int):
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    file_name = os.path.basename(file_path)

    print(f"Searching for {DEVICE_NAME_PREFIX}...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and DEVICE_NAME_PREFIX in d.name, timeout=10.0
    )
    if not device:
        print("BSC300 not found")
        return

    queue: asyncio.Queue = asyncio.Queue()
    async with BleakClient(device) as client:
        print(f"Connected to {device.address}")
        for tag, uuid in [("ctrl", CTRL_NOTIFY), ("data", DATA_NOTIFY)] + [("aux", u) for u in AUX_NOTIFY]:
            await client.start_notify(uuid, make_notifier(tag, queue))

        for cmd, arg in [(0x0A, 1), (0x04, 1), (0x0B, 6), (0x0B, 1)]:
            kind = 6 if (cmd, arg) == (0x0B, 6) else 1
            await client.write_gatt_char(CTRL_WRITE, frame(cmd, kind, proto_cmd(cmd, arg)), response=False)
        await wait_for(queue, "ctrl", 0x0B)
        await asyncio.sleep(1.0)

        proto = file_header(file_name, len(file_bytes), file_id)
        stream = frame(0x15, 0x03, b"", b3=0xAA) + len(proto).to_bytes(4, "big") + proto + file_bytes
        print(f"Sending {file_name}: {len(file_bytes)} bytes")
        for i in range(0, len(stream), CHUNK):
            await client.write_gatt_char(DATA_WRITE, stream[i:i + CHUNK], response=False)
            await asyncio.sleep(0.01)

        try:
            await wait_for(queue, "data", 0x15, timeout=10.0)
            print("Device acknowledged the file")
        except asyncio.TimeoutError:
            print("No acknowledgement from device")
        await asyncio.sleep(6.0)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/track.fit"
    fid = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x4E938270
    asyncio.run(send_track_file(path, fid))
