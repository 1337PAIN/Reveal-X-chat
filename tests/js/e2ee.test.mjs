/**
 * End-to-end encryption (app/static/js/e2ee.js).
 *
 * This is the half of the project's security story that runs in the browser,
 * and until now it had no automated test at all: ECDH P-256 to HKDF-SHA256 to
 * AES-GCM-256, verified by opening the app and looking at it.
 *
 * Two sessions are built here, given each other's public keys, and made to talk
 * -- which is the only way to catch the failure that matters. A key agreement
 * that is broken in a *symmetric* way still round-trips happily against itself.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow, makeIndexedDB } from './harness.mjs';

const E2EE = join(STATIC_JS, 'e2ee.js');

/** A fresh session with its own key store, as a separate browser would have. */
async function newSession(userId) {
    const indexedDB = makeIndexedDB();
    const window = makeWindow({ indexedDB });
    loadBrowserScript(E2EE, { window, indexedDB, console });
    const session = window.revealxE2EE;
    const publicKey = await session.init(userId);
    return { session, publicKey, indexedDB, window };
}

test('a session generates a usable key pair on first use', async () => {
    const alice = await newSession('alice');
    assert.ok(alice.session.available, 'crypto and a key store were detected');
    assert.ok(alice.session.isReady(), 'session reports ready');
    assert.ok(alice.publicKey.length > 0, 'a public key was published');
    // SPKI for P-256 is 91 bytes, which is 124 base64 characters.
    assert.equal(Buffer.from(alice.publicKey, 'base64').length, 91);
});

test('the key pair persists, so a reload does not orphan old messages', async () => {
    const indexedDB = makeIndexedDB();
    const window = makeWindow({ indexedDB });

    loadBrowserScript(E2EE, { window, indexedDB, console });
    const first = await window.revealxE2EE.init('alice');

    // A second load against the same store is what a page reload looks like.
    const window2 = makeWindow({ indexedDB });
    loadBrowserScript(E2EE, { window: window2, indexedDB, console });
    const second = await window2.revealxE2EE.init('alice');

    assert.equal(second, first, 'the same public key came back after reload');
});

test('regenerate: true replaces the key pair', async () => {
    const indexedDB = makeIndexedDB();
    const window = makeWindow({ indexedDB });
    loadBrowserScript(E2EE, { window, indexedDB, console });

    const first = await window.revealxE2EE.init('alice');
    const second = await window.revealxE2EE.init('alice', { regenerate: true });

    assert.notEqual(second, first, 'a new key pair was issued');
});

test('two sessions derive the same key and can read each other', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await alice.session.encrypt(bob.publicKey, 'the shares are in the vault');
    assert.ok(sent && sent.ciphertext && sent.iv, 'encrypt returned a payload');

    const read = await bob.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv);
    assert.equal(read, 'the shares are in the vault');
});

test('the conversation key is symmetric in both directions', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const fromBob = await bob.session.encrypt(alice.publicKey, 'acknowledged');
    const readByAlice = await alice.session.decrypt(bob.publicKey, fromBob.ciphertext, fromBob.iv);

    assert.equal(readByAlice, 'acknowledged');
});

test('a third party holding both public keys cannot read the message', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const eve = await newSession('eve');

    const sent = await alice.session.encrypt(bob.publicKey, 'secret');

    // Eve knows every public key -- they are published by design. Without a
    // private half, that has to be worth nothing.
    const asAlice = await eve.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv);
    const asBob = await eve.session.decrypt(bob.publicKey, sent.ciphertext, sent.iv);

    assert.equal(asAlice, null);
    assert.equal(asBob, null);
});

test('a tampered ciphertext is rejected, not silently mangled', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await alice.session.encrypt(bob.publicKey, 'transfer approved');

    const bytes = Buffer.from(sent.ciphertext, 'base64');
    bytes[0] ^= 0x01;                     // one bit
    const tampered = bytes.toString('base64');

    const read = await bob.session.decrypt(alice.publicKey, tampered, sent.iv);
    assert.equal(read, null, 'AES-GCM authentication caught the change');
});

test('a tampered IV is rejected', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await alice.session.encrypt(bob.publicKey, 'transfer approved');
    const iv = Buffer.from(sent.iv, 'base64');
    iv[0] ^= 0x01;

    const read = await bob.session.decrypt(alice.publicKey, sent.ciphertext, iv.toString('base64'));
    assert.equal(read, null);
});

test('each message gets a fresh IV', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const seen = new Set();
    for (let i = 0; i < 25; i++) {
        const sent = await alice.session.encrypt(bob.publicKey, 'same text every time');
        seen.add(sent.iv);
    }
    // A repeated IV under one AES-GCM key is a catastrophic failure, not a
    // cosmetic one: it leaks the XOR of the plaintexts.
    assert.equal(seen.size, 25, 'no IV was reused across 25 messages');
});

test('identical plaintext does not produce identical ciphertext', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const a = await alice.session.encrypt(bob.publicKey, 'yes');
    const b = await alice.session.encrypt(bob.publicKey, 'yes');

    assert.notEqual(a.ciphertext, b.ciphertext);
});

test('binary payloads survive the round trip byte for byte', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    // Voice notes go through encryptBytes; every byte value has to survive.
    const audio = new Uint8Array(1024);
    for (let i = 0; i < audio.length; i++) audio[i] = i % 256;

    const sent = await alice.session.encryptBytes(bob.publicKey, audio);
    const back = await bob.session.decryptBytes(alice.publicKey, sent.ciphertext, sent.iv);

    assert.deepEqual(new Uint8Array(back), audio);
});

test('unicode survives the round trip', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const text = 'شیئر محفوظ ہے — 共有は安全です — 🔐';
    const sent = await alice.session.encrypt(bob.publicKey, text);
    assert.equal(await bob.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv), text);
});

test('encrypting to nobody returns null rather than sending in the clear', async () => {
    const alice = await newSession('alice');
    assert.equal(await alice.session.encrypt('', 'secret'), null);
    assert.equal(await alice.session.encrypt(null, 'secret'), null);
});

test('decrypt returns null on a missing ciphertext or iv', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    assert.equal(await bob.session.decrypt(alice.publicKey, '', 'aaaa'), null);
    assert.equal(await bob.session.decrypt(alice.publicKey, 'aaaa', ''), null);
});

test('fingerprints identify a key and differ between keys', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const mine = await alice.session.fingerprint();
    const ofBob = await alice.session.fingerprint(bob.publicKey);

    assert.match(mine, /^[0-9A-F]{2}( [0-9A-F]{2}){7}$/, '8 bytes, hex, spaced');
    assert.notEqual(mine, ofBob);
    // Both sides must read the same value or out-of-band comparison is useless.
    assert.equal(ofBob, await bob.session.fingerprint());
});

test('forgetPeer drops the cached key so a rotated peer key is picked up', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await alice.session.encrypt(bob.publicKey, 'before rotation');
    assert.equal(await bob.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv), 'before rotation');

    // Bob rotates. Alice's cached derivation is now stale.
    const rotated = await bob.session.init('bob', { regenerate: true });
    alice.session.forgetPeer(bob.publicKey);

    const after = await alice.session.encrypt(rotated, 'after rotation');
    assert.equal(await bob.session.decrypt(alice.publicKey, after.ciphertext, after.iv), 'after rotation');
});

test('a session without Web Crypto degrades instead of throwing', async () => {
    // prefers-no-crypto is not a thing, but an insecure origin is: window.crypto
    // .subtle is undefined on plain http, and the app has to notice rather than
    // crash on load.
    const indexedDB = makeIndexedDB();
    const window = makeWindow({ indexedDB, crypto: undefined });
    loadBrowserScript(E2EE, { window, indexedDB, console });

    const session = window.revealxE2EE;
    assert.equal(session.available, false);
    assert.equal(await session.init('alice'), '');
    assert.equal(session.isReady(), false);
    assert.equal(await session.encrypt('anything', 'secret'), null);
});
