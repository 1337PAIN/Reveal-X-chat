"""Regenerate png-gray.json, the decoder fixtures for tests/js/vc-client.test.mjs.

vc-client.js writes PNGs with filter 0 only, so a round trip through its own
encoder never exercises the other four row filters. A Share 1 the recipient
downloads is the sender's bytes verbatim today, but the decoder should not
depend on that: these PNGs use every filter type (built by hand here, one per
row in rotation) plus one written by OpenCV, which picks its own filters.

    python tests/js/fixtures/make_png_fixtures.py
"""

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent


def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def encode_with_filters(pixels):
    height, width = pixels.shape
    raw = bytearray()
    previous = [0] * width
    for y in range(height):
        row = [int(v) for v in pixels[y]]
        kind = y % 5
        out = []
        for x in range(width):
            left = row[x - 1] if x else 0
            up = previous[x]
            up_left = previous[x - 1] if x else 0
            predictor = (0, left, up, (left + up) // 2, paeth(left, up, up_left))[kind]
            out.append((row[x] - predictor) & 0xFF)
        raw.append(kind)
        raw.extend(out)
        previous = row
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))


def entry(name, png, pixels):
    return {
        'name': name,
        'png_b64': base64.b64encode(png).decode('ascii'),
        'width': int(pixels.shape[1]),
        'height': int(pixels.shape[0]),
        'pixels_sha256': hashlib.sha256(pixels.tobytes()).hexdigest(),
    }


def main():
    rng = np.random.default_rng(2026)
    structured = np.tile(np.arange(37, dtype=np.uint8) * 7, (23, 1))
    structured[5:15, 8:20] = 250
    noise = rng.integers(0, 256, (31, 29), dtype=np.uint8)
    ok, cv_png = cv2.imencode('.png', structured)
    assert ok
    fixtures = [
        entry('all five filters, structured', encode_with_filters(structured), structured),
        entry('all five filters, noise', encode_with_filters(noise), noise),
        entry('written by OpenCV', cv_png.tobytes(), structured),
    ]
    (HERE / 'png-gray.json').write_text(json.dumps(fixtures, indent=1) + '\n', encoding='utf-8')
    print(f'wrote {len(fixtures)} fixtures')


if __name__ == '__main__':
    main()
