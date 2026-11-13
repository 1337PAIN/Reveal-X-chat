/**
 * Reveal-X: visual cryptography in the sender's browser.
 *
 * The image used to be uploaded in the clear and split on the server, so the
 * server saw every picture anyone sent -- the one place the project's E2EE
 * did not reach, and the boundary SECURITY.md had to warn about. The split
 * now happens here:
 *
 *   Share 1 = fresh random bytes (crypto.getRandomValues)
 *   Share 2 = image XOR Share 1
 *
 * Only Share 1 is uploaded. On its own it is a one-time pad, independent of
 * the image. Share 2 goes inside an E2EE v2 message (see e2ee.js) whose
 * plaintext is the JSON envelope built here: Share 2 as a PNG, the SHA-256 of
 * Share 1's PNG bytes, and the dimensions. The server can store that message
 * but not read it, so the recipient no longer has to be online to receive it.
 *
 * The hash is the end-to-end integrity check. The server's HMAC proves the
 * stored Share 1 has not changed since the *server* recorded it; the hash
 * proves the Share 1 the recipient downloaded is the one the *sender* made,
 * which holds even if the server itself is the one changing it.
 *
 * Shares are written as single-channel 8-bit PNGs by a small encoder here,
 * because canvas.toBlob() always writes RGBA and the server checks that what
 * it stores really is one channel. A matching decoder reads them back so the
 * XOR runs on exact bytes rather than on whatever a canvas hands back.
 */

(function () {
    const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
    const MAX_SIDE = 4096;
    const RANDOM_CHUNK = 65536; // getRandomValues refuses more per call

    /* ------------------------------------------------------------ pixels */

    /**
     * RGBA to grey with the same integer BT.601 weights as tamper-onnx.js,
     * taking the channel directly when the pixel is already grey.
     */
    function toGrayscale(rgba, width, height) {
        const gray = new Uint8Array(width * height);
        for (let i = 0, p = 0; i < gray.length; i += 1, p += 4) {
            const r = rgba[p];
            const g = rgba[p + 1];
            const b = rgba[p + 2];
            gray[i] = (r === g && g === b) ? r : (r * 4899 + g * 9617 + b * 1868 + 8192) >> 14;
        }
        return gray;
    }

    function randomBytes(length) {
        const out = new Uint8Array(length);
        for (let offset = 0; offset < length; offset += RANDOM_CHUNK) {
            window.crypto.getRandomValues(out.subarray(offset, Math.min(offset + RANDOM_CHUNK, length)));
        }
        return out;
    }

    function split(gray) {
        const share1 = randomBytes(gray.length);
        const share2 = new Uint8Array(gray.length);
        for (let i = 0; i < gray.length; i += 1) share2[i] = gray[i] ^ share1[i];
        return { share1, share2 };
    }

    function reconstruct(share1, share2) {
        if (share1.length !== share2.length) throw new Error('The shares are different sizes');
        const out = new Uint8Array(share1.length);
        for (let i = 0; i < out.length; i += 1) out[i] = share1[i] ^ share2[i];
        return out;
    }

    /* --------------------------------------------------------------- PNG */

    const CRC_TABLE = (() => {
        const table = new Uint32Array(256);
        for (let n = 0; n < 256; n += 1) {
            let c = n;
            for (let k = 0; k < 8; k += 1) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
            table[n] = c >>> 0;
        }
        return table;
    })();

    function crc32(bytes) {
        let c = 0xffffffff;
        for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
        return (c ^ 0xffffffff) >>> 0;
    }

    async function through(bytes, stream) {
        const response = new Response(new Blob([bytes]).stream().pipeThrough(stream));
        return new Uint8Array(await response.arrayBuffer());
    }

    function u32(value) {
        return [(value >>> 24) & 0xff, (value >>> 16) & 0xff, (value >>> 8) & 0xff, value & 0xff];
    }

    function pngChunk(type, data) {
        const typeBytes = new TextEncoder().encode(type);
        const body = new Uint8Array(typeBytes.length + data.length);
        body.set(typeBytes);
        body.set(data, typeBytes.length);
        const out = new Uint8Array(12 + data.length);
        out.set(u32(data.length), 0);
        out.set(body, 4);
        out.set(u32(crc32(body)), 8 + data.length);
        return out;
    }

    /** 8-bit greyscale, filter 0 on every row, no ancillary chunks. */
    async function encodeGrayPng(pixels, width, height) {
        if (pixels.length !== width * height) throw new Error('Pixel count does not match the size');
        const raw = new Uint8Array(height * (width + 1));
        for (let y = 0; y < height; y += 1) {
            raw[y * (width + 1)] = 0;
            raw.set(pixels.subarray(y * width, (y + 1) * width), y * (width + 1) + 1);
        }
        const ihdr = new Uint8Array([...u32(width), ...u32(height), 8, 0, 0, 0, 0]);
        const idat = await through(raw, new CompressionStream('deflate'));
        const parts = [Uint8Array.from(PNG_SIGNATURE), pngChunk('IHDR', ihdr),
            pngChunk('IDAT', idat), pngChunk('IEND', new Uint8Array(0))];
        const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
        let offset = 0;
        for (const part of parts) {
            out.set(part, offset);
            offset += part.length;
        }
        return out;
    }

    function paeth(a, b, c) {
        const p = a + b - c;
        const pa = Math.abs(p - a);
        const pb = Math.abs(p - b);
        const pc = Math.abs(p - c);
        if (pa <= pb && pa <= pc) return a;
        return pb <= pc ? b : c;
    }

    /**
     * Read an 8-bit greyscale, non-interlaced PNG. Anything else is refused,
     * as is any chunk whose CRC does not match: a share is either exactly the
     * bytes that were written or it is not a share.
     */
    async function decodeGrayPng(bytes) {
        const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        for (let i = 0; i < 8; i += 1) {
            if (bytes[i] !== PNG_SIGNATURE[i]) throw new Error('Not a PNG file');
        }
        let offset = 8;
        let width = 0;
        let height = 0;
        const idat = [];
        let sawEnd = false;
        while (offset + 12 <= bytes.length) {
            const length = view.getUint32(offset);
            const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
            if (offset + 12 + length > bytes.length) throw new Error('Truncated PNG chunk');
            const body = bytes.subarray(offset + 4, offset + 8 + length);
            if (crc32(body) !== view.getUint32(offset + 8 + length)) {
                throw new Error(`PNG ${type} chunk failed its CRC check`);
            }
            const data = bytes.subarray(offset + 8, offset + 8 + length);
            if (type === 'IHDR') {
                width = view.getUint32(offset + 8);
                height = view.getUint32(offset + 12);
                const [depth, colour, compression, filter, interlace] = data.subarray(8, 13);
                if (depth !== 8 || colour !== 0 || compression !== 0 || filter !== 0 || interlace !== 0) {
                    throw new Error('A share must be an 8-bit greyscale, non-interlaced PNG');
                }
                if (!width || !height || width > MAX_SIDE || height > MAX_SIDE) {
                    throw new Error('PNG dimensions are out of range');
                }
            } else if (type === 'IDAT') {
                idat.push(data);
            } else if (type === 'IEND') {
                sawEnd = true;
                break;
            }
            offset += 12 + length;
        }
        if (!width || !sawEnd || !idat.length) throw new Error('Incomplete PNG');

        const compressed = new Uint8Array(idat.reduce((n, c) => n + c.length, 0));
        let at = 0;
        for (const c of idat) {
            compressed.set(c, at);
            at += c.length;
        }
        const raw = await through(compressed, new DecompressionStream('deflate'));
        const stride = width + 1;
        if (raw.length !== stride * height) throw new Error('PNG image data has the wrong length');

        const pixels = new Uint8Array(width * height);
        for (let y = 0; y < height; y += 1) {
            const kind = raw[y * stride];
            const row = y * width;
            const prev = row - width;
            for (let x = 0; x < width; x += 1) {
                const value = raw[y * stride + 1 + x];
                const left = x ? pixels[row + x - 1] : 0;
                const up = y ? pixels[prev + x] : 0;
                const upLeft = x && y ? pixels[prev + x - 1] : 0;
                let predictor;
                if (kind === 0) predictor = 0;
                else if (kind === 1) predictor = left;
                else if (kind === 2) predictor = up;
                else if (kind === 3) predictor = (left + up) >> 1;
                else if (kind === 4) predictor = paeth(left, up, upLeft);
                else throw new Error('Unknown PNG row filter');
                pixels[row + x] = (value + predictor) & 0xff;
            }
        }
        return { pixels, width, height };
    }

    /* ---------------------------------------------------------- envelope */

    function bytesToBase64(bytes) {
        let binary = '';
        for (let i = 0; i < bytes.length; i += 0x8000) {
            binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        }
        return btoa(binary);
    }

    function base64ToBytes(value) {
        const binary = atob(value);
        const out = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
        return out;
    }

    async function sha256Hex(bytes) {
        const digest = await window.crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
    }

    /**
     * Split a greyscale image and package it for sending. Returns the Share 1
     * PNG to upload and the envelope (a JSON string) to encrypt for the peer.
     */
    async function prepareShare(gray, width, height) {
        const { share1, share2 } = split(gray);
        const share1Png = await encodeGrayPng(share1, width, height);
        const share2Png = await encodeGrayPng(share2, width, height);
        const share1Sha256 = await sha256Hex(share1Png);
        const envelope = JSON.stringify({
            share2_png_b64: bytesToBase64(share2Png),
            share1_sha256: share1Sha256,
            width,
            height,
        });
        return { share1Png, share1Sha256, envelope, width, height };
    }

    function openEnvelope(text) {
        let value;
        try {
            value = JSON.parse(text);
        } catch (error) {
            throw new Error('The share envelope is not valid JSON');
        }
        const ok = value && typeof value === 'object' && !Array.isArray(value)
            && typeof value.share2_png_b64 === 'string' && value.share2_png_b64.length > 0
            && typeof value.share1_sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.share1_sha256)
            && Number.isInteger(value.width) && value.width > 0 && value.width <= MAX_SIDE
            && Number.isInteger(value.height) && value.height > 0 && value.height <= MAX_SIDE;
        if (!ok) throw new Error('The share envelope is malformed');
        return value;
    }

    /**
     * Check the downloaded Share 1 against the sender's hash, then XOR.
     * Reconstructs even on a mismatch -- the damage is itself evidence -- and
     * reports `hashMatches` so the caller can say so first.
     */
    async function verifyAndReconstruct(share1Png, envelope) {
        const hashMatches = (await sha256Hex(share1Png)) === envelope.share1_sha256;
        const one = await decodeGrayPng(share1Png);
        const two = await decodeGrayPng(base64ToBytes(envelope.share2_png_b64));
        if (one.width !== two.width || one.height !== two.height
            || one.width !== envelope.width || one.height !== envelope.height) {
            throw new Error('The shares do not have matching dimensions');
        }
        return {
            hashMatches,
            pixels: reconstruct(one.pixels, two.pixels),
            width: one.width,
            height: one.height,
        };
    }

    /* ------------------------------------------------- browser-only glue */

    /** Draw an image (data URL) and read it back as greyscale. */
    function imageToGray(src) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => {
                const width = image.naturalWidth;
                const height = image.naturalHeight;
                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                const context = canvas.getContext('2d', { willReadFrequently: true });
                context.drawImage(image, 0, 0);
                const { data } = context.getImageData(0, 0, width, height);
                resolve({ gray: toGrayscale(data, width, height), width, height });
            };
            image.onerror = () => reject(new Error('Could not read the image'));
            image.src = src;
        });
    }

    async function pngDataUrl(pixels, width, height) {
        return `data:image/png;base64,${bytesToBase64(await encodeGrayPng(pixels, width, height))}`;
    }

    window.revealxVC = {
        toGrayscale,
        split,
        reconstruct,
        encodeGrayPng,
        decodeGrayPng,
        prepareShare,
        openEnvelope,
        verifyAndReconstruct,
        sha256Hex,
        bytesToBase64,
        base64ToBytes,
        imageToGray,
        pngDataUrl,
    };
}());
