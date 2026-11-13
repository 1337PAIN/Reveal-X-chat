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
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { STATIC_JS, loadBrowserScript, makeWindow, makeIndexedDB } from './harness.mjs';

const E2EE = join(STATIC_JS, 'e2ee.js');
// The v1 client exactly as it shipped (git show 2c030ff:app/static/js/e2ee.js),
// so "old rows still decrypt" is tested against the real old code rather than
// a re-derivation of it that could share the new code's mistakes.
const E2EE_V1 = resolve(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'e2ee-v1.js');

/** A fresh session with its own key store, as a separate browser would have. */
async function newSession(userId, indexedDB = makeIndexedDB(), file = E2EE) {
    const window = makeWindow({ indexedDB });
    loadBrowserScript(file, { window, indexedDB, console });
    const session = window.revealxE2EE;
    const publicKey = await session.init(userId);
    return { id: userId, session, publicKey, indexedDB, window };
}

/** from -> to, the way chat.js sends. */
function send(from, to, text) {
    return from.session.encrypt(to.publicKey, text, { recipientId: to.id });
}

/** The context a receiver rebuilds from the stored row. */
function ctx(sent, senderId, recipientId) {
    return { v: sent.v, senderId, recipientId, msgUuid: sent.msg_uuid };
}

/** `by` opens a message that went from -> to. */
function open(by, from, to, sent, overrides = {}) {
    const peer = by.id === from.id ? to : from;
    const c = { ...ctx(sent, from.id, to.id), ...overrides };
    return by.session.decrypt(peer.publicKey, overrides.ciphertext ?? sent.ciphertext,
        overrides.iv ?? sent.iv, c);
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

    const sent = await send(alice, bob, 'the shares are in the vault');
    assert.ok(sent && sent.ciphertext && sent.iv, 'encrypt returned a payload');

    const read = await open(bob, alice, bob, sent);
    assert.equal(read, 'the shares are in the vault');
});

test('both directions work, and each side can read what it sent', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const fromBob = await send(bob, alice, 'acknowledged');
    assert.equal(await open(alice, bob, alice, fromBob), 'acknowledged');
    // The sender's own transcript is decrypted from the stored row too.
    assert.equal(await open(bob, bob, alice, fromBob), 'acknowledged');
});

test('a third party holding both public keys cannot read the message', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const eve = await newSession('eve');

    const sent = await send(alice, bob, 'secret');
    const c = ctx(sent, 'alice', 'bob');

    // Eve knows every public key -- they are published by design. Without a
    // private half, that has to be worth nothing.
    const asAlice = await eve.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv, c);
    const asBob = await eve.session.decrypt(bob.publicKey, sent.ciphertext, sent.iv, c);

    assert.equal(asAlice, null);
    assert.equal(asBob, null);
});

test('a tampered ciphertext is rejected, not silently mangled', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await send(alice, bob, 'transfer approved');

    const bytes = Buffer.from(sent.ciphertext, 'base64');
    bytes[0] ^= 0x01;                     // one bit
    const tampered = bytes.toString('base64');

    const read = await open(bob, alice, bob, sent, { ciphertext: tampered });
    assert.equal(read, null, 'AES-GCM authentication caught the change');
});

test('a tampered IV is rejected', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const sent = await send(alice, bob, 'transfer approved');
    const iv = Buffer.from(sent.iv, 'base64');
    iv[0] ^= 0x01;

    const read = await open(bob, alice, bob, sent, { iv: iv.toString('base64') });
    assert.equal(read, null);
});

test('each message gets a fresh IV', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const seen = new Set();
    for (let i = 0; i < 25; i++) {
        const sent = await send(alice, bob, 'same text every time');
        seen.add(sent.iv);
    }
    // A repeated IV under one AES-GCM key is a catastrophic failure, not a
    // cosmetic one: it leaks the XOR of the plaintexts.
    assert.equal(seen.size, 25, 'no IV was reused across 25 messages');
});

test('identical plaintext does not produce identical ciphertext', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const a = await send(alice, bob, 'yes');
    const b = await send(alice, bob, 'yes');

    assert.notEqual(a.ciphertext, b.ciphertext);
});

test('binary payloads survive the round trip byte for byte', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    // Voice notes go through encryptBytes; every byte value has to survive.
    const audio = new Uint8Array(1024);
    for (let i = 0; i < audio.length; i++) audio[i] = i % 256;

    const sent = await alice.session.encryptBytes(bob.publicKey, audio, { recipientId: 'bob' });
    const back = await bob.session.decryptBytes(alice.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'));

    assert.deepEqual(new Uint8Array(back), audio);
});

test('unicode survives the round trip', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const text = 'شیئر محفوظ ہے — 共有は安全です — 🔐';
    const sent = await send(alice, bob, text);
    assert.equal(await open(bob, alice, bob, sent), text);
});

test('encrypting to nobody returns null rather than sending in the clear', async () => {
    const alice = await newSession('alice');
    assert.equal(await alice.session.encrypt('', 'secret'), null);
    assert.equal(await alice.session.encrypt(null, 'secret'), null);
});

test('decrypt returns null on a missing ciphertext or iv', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const c = { v: 2, senderId: 'alice', recipientId: 'bob', msgUuid: 'x' };
    assert.equal(await bob.session.decrypt(alice.publicKey, '', 'aaaa', c), null);
    assert.equal(await bob.session.decrypt(alice.publicKey, 'aaaa', '', c), null);
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

    const sent = await send(alice, bob, 'before rotation');
    assert.equal(await open(bob, alice, bob, sent), 'before rotation');

    // Bob rotates. Alice's cached derivation is now stale.
    const rotated = await bob.session.init('bob', { regenerate: true });
    alice.session.forgetPeer(bob.publicKey);

    const after = await alice.session.encrypt(rotated, 'after rotation', { recipientId: 'bob' });
    assert.equal(await bob.session.decrypt(alice.publicKey, after.ciphertext, after.iv,
        ctx(after, 'alice', 'bob')), 'after rotation');
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


// ----------------------------------------------------------------------
// Protocol v2: direction-bound keys and associated data
// ----------------------------------------------------------------------

test('new messages are v2 and carry a random UUID', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const a = await send(alice, bob, 'one');
    const b = await send(alice, bob, 'two');
    assert.equal(a.v, 2);
    assert.match(a.msg_uuid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.notEqual(a.msg_uuid, b.msg_uuid);
});

test('a ciphertext sent A->B does not open as B->A (reflection)', async () => {
    // v1 used one key for both directions, so a server could hand Alice's
    // own message back to her as if Bob had said it, and it decrypted.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'I agree to the transfer');

    assert.equal(await open(alice, bob, alice, sent), null, 'reflected to the sender');
    assert.equal(await open(bob, bob, alice, sent), null, 'relabelled for the recipient');
});

for (const [field, value] of [
    ['senderId', 'mallory'],
    ['recipientId', 'mallory'],
    ['msgUuid', '00000000-0000-4000-8000-000000000000'],
]) {
    test(`changing the associated ${field} fails authentication`, async () => {
        const alice = await newSession('alice');
        const bob = await newSession('bob');
        const sent = await send(alice, bob, 'bound to its context');
        assert.equal(await open(bob, alice, bob, sent), 'bound to its context');
        assert.equal(await open(bob, alice, bob, sent, { [field]: value }), null);
    });
}

test('relabelling a v2 message as v1 does not open it (no downgrade)', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'v2 only');
    assert.equal(await open(bob, alice, bob, sent, { v: 1 }), null);
    assert.equal(await open(bob, alice, bob, sent, { v: undefined }), null);
});

test('rows written by the v1 client still decrypt', async () => {
    // Same browser, same key store: the user upgraded with messages from the
    // last hour still on the server.
    const aliceStore = makeIndexedDB();
    const bobStore = makeIndexedDB();
    const oldAlice = await newSession('alice', aliceStore, E2EE_V1);
    const oldBob = await newSession('bob', bobStore, E2EE_V1);
    const legacy = await oldAlice.session.encrypt(oldBob.publicKey, 'written before v2');
    assert.equal(legacy.v, undefined, 'the fixture really is the v1 client');

    const alice = await newSession('alice', aliceStore);
    const bob = await newSession('bob', bobStore);
    assert.equal(alice.publicKey, oldAlice.publicKey);

    const legacyCtx = { senderId: 'alice', recipientId: 'bob' };
    assert.equal(await bob.session.decrypt(alice.publicKey, legacy.ciphertext, legacy.iv, legacyCtx),
        'written before v2');
    assert.equal(await alice.session.decrypt(bob.publicKey, legacy.ciphertext, legacy.iv, legacyCtx),
        'written before v2', 'the sender still reads their own old message');
});

test('a v1 client cannot read a v2 message, so nothing silently falls back', async () => {
    const bobStore = makeIndexedDB();
    const alice = await newSession('alice');
    const bob = await newSession('bob', bobStore);
    const sent = await send(alice, bob, 'new format');
    const oldBob = await newSession('bob', bobStore, E2EE_V1);
    assert.equal(await oldBob.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv), null);
});
