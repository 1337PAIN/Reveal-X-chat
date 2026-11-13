/**
 * Visual cryptography in the browser (app/static/js/vc-client.js).
 *
 * The image used to be uploaded in the clear and split on the server, so the
 * server saw every picture anyone sent. Now the sender's browser splits it:
 * Share 1 is fresh random bytes and the only thing uploaded, Share 2 is the
 * image XOR Share 1 and travels inside an E2EE v2 message the server cannot
 * read, alongside the SHA-256 of Share 1's PNG bytes so the recipient can tell
 * whether the Share 1 they download is the one that was sent.
 *
 * These run the shipped file. The last test runs the whole path with the
 * shipped e2ee.js as well: split, seal, open, verify, reconstruct.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { STATIC_JS, loadBrowserScript, makeWindow, makeIndexedDB } from './harness.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = JSON.parse(readFileSync(resolve(HERE, 'fixtures', 'png-gray.json'), 'utf8'));

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'vc-client.js'), { window });
const vc = window.revealxVC;

function structuredImage(width, height) {
    const pixels = new Uint8Array(width * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let v = (x * 3 + y * 2) & 0xff;
            if (x > width / 4 && x < width / 2 && y > height / 4 && y < height / 2) v = 240;
            pixels[y * width + x] = v;
        }
    }
    return pixels;
}

function correlation(a, b) {
    const n = a.length;
    let ma = 0, mb = 0;
    for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
    ma /= n; mb /= n;
    let sab = 0, saa = 0, sbb = 0;
    for (let i = 0; i < n; i++) {
        const da = a[i] - ma, db = b[i] - mb;
        sab += da * db; saa += da * da; sbb += db * db;
    }
    return sab / Math.sqrt(saa * sbb);
}

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

// ----------------------------------------------------------------------
// The split
// ----------------------------------------------------------------------

test('split then reconstruct is bit-exact', () => {
    for (const [w, h] of [[1, 1], [7, 3], [64, 64], [301, 257]]) {
        const image = structuredImage(w, h);
        const { share1, share2 } = vc.split(image);
        assert.deepEqual(vc.reconstruct(share1, share2), image, `${w}x${h}`);
    }
});

test('Share 1 is filled even past the 65,536-byte getRandomValues limit', () => {
    // getRandomValues throws above 65,536 bytes per call. An implementation
    // that ignored that would fail, or worse, leave the tail of Share 1 zero
    // -- which makes the tail of Share 2 the image itself.
    const image = new Uint8Array(400 * 300).fill(200);
    const { share1, share2 } = vc.split(image);
    const tail = share1.subarray(share1.length - 4096);
    assert.ok(tail.some((v) => v !== 0), 'the end of Share 1 is not random');
    assert.notDeepEqual(share2.subarray(share2.length - 4096), image.subarray(image.length - 4096));
});

test('neither share alone correlates with the image', () => {
    // The XOR-secrecy test the server split had, moved to where the split is.
    const image = structuredImage(128, 128);
    const { share1, share2 } = vc.split(image);
    assert.ok(Math.abs(correlation(image, share1)) < 0.05, 'Share 1 correlates with the image');
    assert.ok(Math.abs(correlation(image, share2)) < 0.05, 'Share 2 correlates with the image');
});

test('each split uses a fresh mask', () => {
    const image = structuredImage(32, 32);
    assert.notDeepEqual(vc.split(image).share1, vc.split(image).share1);
});

test('grayscale uses the same BT.601 integer weights as tamper-onnx.js', () => {
    const rgba = new Uint8ClampedArray([
        255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 12, 200, 99, 255, 77, 77, 77, 255,
    ]);
    const gray = vc.toGrayscale(rgba, 5, 1);
    const expect = (r, g, b) => (r * 4899 + g * 9617 + b * 1868 + 8192) >> 14;
    assert.deepEqual(Array.from(gray), [expect(255, 0, 0), expect(0, 255, 0), expect(0, 0, 255),
        expect(12, 200, 99), 77]);
    const onnxSource = readFileSync(join(STATIC_JS, 'tamper-onnx.js'), 'utf8');
    assert.match(onnxSource, /r \* 4899 \+ g \* 9617 \+ b \* 1868 \+ 8192\) >> 14/,
        'tamper-onnx.js changed its conversion; keep the two in step');
});

// ----------------------------------------------------------------------
// PNG
// ----------------------------------------------------------------------

test('a share is written as a single-channel 8-bit PNG and reads back exactly', async () => {
    const image = structuredImage(45, 33);
    const png = await vc.encodeGrayPng(image, 45, 33);
    assert.deepEqual(Array.from(png.subarray(0, 8)), [137, 80, 78, 71, 13, 10, 26, 10]);
    // IHDR: bit depth 8, colour type 0 (greyscale), no interlace.
    assert.equal(png[24], 8);
    assert.equal(png[25], 0);
    assert.equal(png[28], 0);
    const back = await vc.decodeGrayPng(png);
    assert.equal(back.width, 45);
    assert.equal(back.height, 33);
    assert.deepEqual(back.pixels, image);
});

for (const fixture of FIXTURES) {
    test(`the decoder reads a PNG it did not write: ${fixture.name}`, async () => {
        const bytes = Uint8Array.from(Buffer.from(fixture.png_b64, 'base64'));
        const { pixels, width, height } = await vc.decodeGrayPng(bytes);
        assert.equal(width, fixture.width);
        assert.equal(height, fixture.height);
        assert.equal(sha256(pixels), fixture.pixels_sha256);
    });
}

test('the decoder refuses what is not a greyscale PNG', async () => {
    const good = await vc.encodeGrayPng(structuredImage(8, 8), 8, 8);
    const notPng = good.slice();
    notPng[1] = 0;
    await assert.rejects(vc.decodeGrayPng(notPng), /PNG/);

    const colour = good.slice();
    colour[25] = 2; // colour type 2, RGB
    await assert.rejects(vc.decodeGrayPng(colour));

    const corrupt = good.slice();
    corrupt[corrupt.length - 20] ^= 0xff; // inside IDAT; its CRC no longer matches
    await assert.rejects(vc.decodeGrayPng(corrupt));
});

// ----------------------------------------------------------------------
// The envelope and its hash
// ----------------------------------------------------------------------

test('prepareShare produces Share 1 to upload and an envelope for Share 2', async () => {
    const image = structuredImage(40, 30);
    const prepared = await vc.prepareShare(image, 40, 30);
    assert.equal(prepared.share1Sha256, sha256(prepared.share1Png));

    const envelope = vc.openEnvelope(prepared.envelope);
    assert.equal(envelope.width, 40);
    assert.equal(envelope.height, 30);
    assert.equal(envelope.share1_sha256, prepared.share1Sha256);

    // Share 2 is in the envelope; nothing about the image is in Share 1.
    const s1 = (await vc.decodeGrayPng(prepared.share1Png)).pixels;
    const s2 = (await vc.decodeGrayPng(Uint8Array.from(Buffer.from(envelope.share2_png_b64, 'base64')))).pixels;
    assert.deepEqual(vc.reconstruct(s1, s2), image);
});

test('a modified Share 1 is caught by the end-to-end hash', async () => {
    const image = structuredImage(24, 24);
    const prepared = await vc.prepareShare(image, 24, 24);
    const envelope = vc.openEnvelope(prepared.envelope);

    const intact = await vc.verifyAndReconstruct(prepared.share1Png, envelope);
    assert.equal(intact.hashMatches, true);
    assert.deepEqual(intact.pixels, image);

    // Re-encode the same Share 1 with one pixel changed: still a valid PNG,
    // which is exactly what the HMAC row cannot see if the server did it.
    const pixels = (await vc.decodeGrayPng(prepared.share1Png)).pixels;
    pixels[100] ^= 1;
    const altered = await vc.encodeGrayPng(pixels, 24, 24);
    const result = await vc.verifyAndReconstruct(altered, envelope);
    assert.equal(result.hashMatches, false);
});

test('openEnvelope refuses malformed envelopes', () => {
    const valid = { share2_png_b64: 'iVBORw0KGgo=', share1_sha256: 'a'.repeat(64), width: 2, height: 2 };
    assert.ok(vc.openEnvelope(JSON.stringify(valid)));
    for (const bad of [
        'not json',
        JSON.stringify({ ...valid, share1_sha256: 'short' }),
        JSON.stringify({ ...valid, width: 0 }),
        JSON.stringify({ ...valid, height: 100000 }),
        JSON.stringify({ ...valid, share2_png_b64: 42 }),
        JSON.stringify([]),
    ]) {
        assert.throws(() => vc.openEnvelope(bad), `accepted ${bad.slice(0, 40)}`);
    }
});

test('the whole path: split, seal with e2ee.js v2, open, verify, reconstruct', async () => {
    async function session(id) {
        const indexedDB = makeIndexedDB();
        const w = makeWindow({ indexedDB });
        loadBrowserScript(join(STATIC_JS, 'e2ee.js'), { window: w, indexedDB, console });
        const s = w.revealxE2EE;
        return { id, s, pub: await s.init(id) };
    }
    const alice = await session('alice');
    const bob = await session('bob');

    const image = structuredImage(50, 40);
    const prepared = await vc.prepareShare(image, 50, 40);
    const sealed = await alice.s.encrypt(bob.pub, prepared.envelope, { recipientId: 'bob' });

    // What the server holds: Share 1 and a ciphertext. Neither is the image.
    assert.ok(!Buffer.from(sealed.ciphertext, 'base64').includes(Buffer.from(image.subarray(0, 64))));

    const opened = await bob.s.decrypt(alice.pub, sealed.ciphertext, sealed.iv,
        { v: sealed.v, senderId: 'alice', recipientId: 'bob', msgUuid: sealed.msg_uuid });
    const result = await vc.verifyAndReconstruct(prepared.share1Png, vc.openEnvelope(opened));
    assert.equal(result.hashMatches, true);
    assert.deepEqual(result.pixels, image);
});
