#!/usr/bin/env python3
"""Original Apache-2.0 geometric Arandu icon, deterministic stdlib PNG."""
from pathlib import Path
import struct
import zlib


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def icon():
    pixels = bytearray()
    for y in range(256):
        pixels.append(0)
        for x in range(256):
            bubble = 20 <= x < 236 and 20 <= y < 216
            tail = 44 <= x <= 85 and 216 <= y < 241 and x <= 85 - (y - 216)
            left = 68 <= y <= 168 and abs(x - (128 - (y - 68) * .48)) <= 12
            right = 68 <= y <= 168 and abs(x - (128 + (y - 68) * .48)) <= 12
            bar = 127 <= y <= 146 and 97 <= x <= 159
            color = (255, 255, 255, 255) if bubble and (left or right or bar) else (23, 99, 147, 255) if bubble or tail else (0, 0, 0, 0)
            pixels.extend(color)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 256, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels, 9)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'custom_components/local_nlu/brand/icon.png'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(icon())
