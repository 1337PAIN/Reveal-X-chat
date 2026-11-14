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
// The v1, v2 and v3 clients exactly as they shipped (git show 2c030ff,
// 3178e7b and 273c7f7:app/static/js/e2ee.js), so "old payloads are refused"
// is tested against what the old code really produced, not a re-derivation.
const FIXTURES = resolve(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const E2EE_V1 = join(FIXTURES, 'e2ee-v1.js');
const E2EE_V2 = join(FIXTURES, 'e2ee-v2.js');
const E2EE_V3 = join(FIXTURES, 'e2ee-v3.js');
const MINUTE = 60 * 1000;

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
    return {
        v: sent.v, conversation: 'dm', senderId, recipientId,
        msgUuid: sent.msg_uuid, sentAt: sent.sent_at, epk: sent.epk,
    };
}

/** `by` opens a message through the full check: crypto, freshness, replay. */
function openFull(by, from, to, sent, messageId, overrides = {}, options = {}) {
    const peer = by.id === from.id ? to : from;
    return by.session.openMessage(peer.publicKey, sent.ciphertext, sent.iv,
        { ...ctx(sent, from.id, to.id), ...overrides }, { messageId, ...options });
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
    // The sender reads their own message from the copy kept in this page;
    // the stored ciphertext no longer opens for them (forward secrecy, v4).
    assert.equal((await openFull(bob, bob, alice, fromBob, 'row-1')).text, 'acknowledged');
    assert.equal(await open(bob, bob, alice, fromBob), null);
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
    const c = { v: 4, conversation: 'dm', senderId: 'alice', recipientId: 'bob', msgUuid: 'x', sentAt: Date.now(),
        epk: alice.publicKey };
    assert.equal(await bob.session.decrypt(alice.publicKey, '', 'aaaa', c), null);
    assert.equal(await bob.session.decrypt(alice.publicKey, 'aaaa', '', c), null);
});

test('the safety number is 60 digits and both sides compute the same one', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const fromAlice = await alice.session.safetyNumber(bob.publicKey);
    const fromBob = await bob.session.safetyNumber(alice.publicKey);

    assert.match(fromAlice, /^\d{5}( \d{5}){11}$/, '12 groups of 5 digits');
    // Out-of-band comparison is useless unless both screens show one number.
    assert.equal(fromAlice, fromBob);
});

test('the safety number changes when either key changes', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const carol = await newSession('carol');

    const before = await alice.session.safetyNumber(bob.publicKey);
    assert.notEqual(await alice.session.safetyNumber(carol.publicKey), before);

    const rotated = await bob.session.init('bob', { regenerate: true });
    assert.notEqual(await alice.session.safetyNumber(rotated), before);
});

test('the 64-bit fingerprint is gone', async () => {
    // Eight bytes of SHA-256 is a 2^32 birthday bound for a key an attacker
    // gets to choose. The safety number replaces it.
    const alice = await newSession('alice');
    assert.equal(typeof alice.session.fingerprint, 'undefined');
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
// Protocol v4: a fresh sender key per message, mixed with the static pair
// ----------------------------------------------------------------------

const P256_SPKI_PREFIX = '3059301306072a8648ce3d020106082a8648ce3d030107034200';

test('new messages are v4 and carry a random UUID, the sender\'s clock and a fresh key', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const before = Date.now();
    const a = await send(alice, bob, 'one');
    const b = await send(alice, bob, 'two');
    assert.equal(a.v, 4);
    assert.match(a.msg_uuid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.notEqual(a.msg_uuid, b.msg_uuid);
    assert.ok(Number.isSafeInteger(a.sent_at), 'sent_at is integer milliseconds');
    assert.ok(a.sent_at >= before && a.sent_at <= Date.now());
    // The ephemeral public key: P-256 SPKI, never the sender's static key,
    // and different for every message.
    const epk = Buffer.from(a.epk, 'base64');
    assert.equal(epk.length, 91);
    assert.equal(epk.subarray(0, 26).toString('hex'), P256_SPKI_PREFIX);
    assert.notEqual(a.epk, alice.publicKey);
    assert.notEqual(a.epk, b.epk);
});

test('a v4 round trip through openMessage', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'over v4');
    const opened = await openFull(bob, alice, bob, sent, 'row-1');
    assert.equal(opened.status, 'ok');
    assert.equal(opened.text, 'over v4');
});

test('a ciphertext sent A->B does not open as B->A (reflection)', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'I agree to the transfer');

    assert.equal(await open(alice, bob, alice, sent), null, 'reflected to the sender');
    assert.equal(await open(bob, bob, alice, sent), null, 'relabelled for the recipient');
    assert.equal((await openFull(alice, bob, alice, sent, 'row-1')).status, 'failed');
});

for (const [field, value] of [
    ['conversation', 'group:g1'],
    ['senderId', 'mallory'],
    ['recipientId', 'mallory'],
    ['msgUuid', '00000000-0000-4000-8000-000000000000'],
    ['sentAt', 'shift'],
    ['epk', 'other-epk'],
]) {
    test(`changing the associated ${field} fails authentication`, async () => {
        const alice = await newSession('alice');
        const bob = await newSession('bob');
        const sent = await send(alice, bob, 'bound to its context');
        assert.equal(await open(bob, alice, bob, sent), 'bound to its context');
        let changed = value;
        if (value === 'shift') changed = sent.sent_at + 1;
        if (value === 'other-epk') changed = (await send(alice, bob, 'another')).epk;
        assert.equal(await open(bob, alice, bob, sent, { [field]: changed }), null);
    });
}

test('a field containing the separator is refused, not encoded ambiguously', async () => {
    // "a|b" + "c" and "a" + "b|c" would otherwise be the same associated data.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    assert.equal(await alice.session.encrypt(bob.publicKey, 'x', { recipientId: 'bob|eve' }), null);
    const sent = await send(alice, bob, 'x');
    assert.equal(await open(bob, alice, bob, sent, { conversation: 'dm|x' }), null);
});

// ----------------------------------------------------------------------
// Forward secrecy for the sender's key, and what it does not cover
// ----------------------------------------------------------------------

test('the sender\'s long-term key alone cannot open a message already sent', async () => {
    // Everything Alice's browser keeps -- her non-extractable private key in
    // IndexedDB -- is what an attacker who takes her device gets. The
    // per-message key is gone once the message is sealed, so it is not enough.
    const aliceStore = makeIndexedDB();
    const alice = await newSession('alice', aliceStore);
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'sent before the device was taken');

    const thief = await newSession('alice', aliceStore);
    assert.equal(thief.publicKey, alice.publicKey, 'same long-term key');
    assert.equal(await thief.session.decrypt(bob.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob')), null);
});

test('the recipient\'s long-term key does open it: this is sender-side forward secrecy only', async () => {
    // Documented, not hidden: protecting the recipient's side needs a
    // ratchet (docs/SECURITY.md).
    const bobStore = makeIndexedDB();
    const alice = await newSession('alice');
    const bob = await newSession('bob', bobStore);
    const sent = await send(alice, bob, 'readable with bob\'s key');
    const thief = await newSession('bob', bobStore);
    assert.equal(await thief.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob')), 'readable with bob\'s key');
});

test('the sender is still authenticated: a forged "from Alice" does not open', async () => {
    // A fresh key alone would let anyone holding Bob's public key -- the
    // server included -- write to him as Alice. The static pair is mixed in,
    // so only Alice's private key can produce a message that opens as hers.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const mallory = await newSession('mallory');
    const forged = await mallory.session.encrypt(bob.publicKey, 'pay mallory', { recipientId: 'bob' });
    assert.equal(await bob.session.decrypt(alice.publicKey, forged.ciphertext, forged.iv,
        { ...ctx(forged, 'alice', 'bob') }), null);
});

test('the sender reads their own message for the life of the page, from memory', async () => {
    const aliceStore = makeIndexedDB();
    const alice = await newSession('alice', aliceStore);
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'my own words');
    const mine = await openFull(alice, alice, bob, sent, 'row-1');
    assert.equal(mine.status, 'ok');
    assert.equal(mine.text, 'my own words');

    // After a reload the copy is gone and nothing on disk can rebuild it.
    const reloaded = await newSession('alice', aliceStore);
    const later = await reloaded.session.openMessage(bob.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'), { messageId: 'row-1' });
    assert.equal(later.status, 'own-sealed');
    assert.equal(later.text, undefined);
});

test('the in-memory copy is only handed back for the exact ciphertext it was made for', async () => {
    // A server that swaps the stored body under Alice's own msg_uuid must
    // not get her browser to display the original text against it.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'original');
    const other = await send(alice, bob, 'other');
    const swapped = await alice.session.openMessage(bob.publicKey, other.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'), { messageId: 'row-1' });
    assert.equal(swapped.status, 'failed');
});

test('signing in as someone else clears the in-memory copies', async () => {
    const store = makeIndexedDB();
    const alice = await newSession('alice', store);
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'private to this page');
    await alice.session.init('carol');
    await alice.session.init('alice');
    const opened = await alice.session.openMessage(bob.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'), { messageId: 'row-1' });
    assert.equal(opened.status, 'own-sealed');
});

// ----------------------------------------------------------------------
// Older protocols are refused, never decrypted
// ----------------------------------------------------------------------

test('v1 payloads are refused, not decrypted', async () => {
    const aliceStore = makeIndexedDB();
    const bobStore = makeIndexedDB();
    const oldAlice = await newSession('alice', aliceStore, E2EE_V1);
    const oldBob = await newSession('bob', bobStore, E2EE_V1);
    const legacy = await oldAlice.session.encrypt(oldBob.publicKey, 'written by v1');
    assert.equal(legacy.v, undefined, 'the fixture really is the v1 client');

    const alice = await newSession('alice', aliceStore);
    const bob = await newSession('bob', bobStore);
    assert.equal(alice.publicKey, oldAlice.publicKey);
    for (const v of [undefined, 1]) {
        const c = { v, conversation: 'dm', senderId: 'alice', recipientId: 'bob', msgUuid: 'u', sentAt: Date.now() };
        assert.equal(await bob.session.decrypt(alice.publicKey, legacy.ciphertext, legacy.iv, c), null);
        const opened = await bob.session.openMessage(alice.publicKey, legacy.ciphertext, legacy.iv, c, { messageId: 'r' });
        assert.equal(opened.status, 'legacy');
    }
});

for (const [version, file] of [[2, E2EE_V2], [3, E2EE_V3]]) {
    test(`v${version} payloads are refused, even relabelled as v4`, async () => {
        const aliceStore = makeIndexedDB();
        const bobStore = makeIndexedDB();
        const oldAlice = await newSession('alice', aliceStore, file);
        const oldBob = await newSession('bob', bobStore, file);
        const legacy = await oldAlice.session.encrypt(oldBob.publicKey, `written by v${version}`, { recipientId: 'bob' });
        assert.equal(legacy.v, version, `the fixture really is the v${version} client`);

        const alice = await newSession('alice', aliceStore);
        const bob = await newSession('bob', bobStore);
        const base = {
            conversation: 'dm', senderId: 'alice', recipientId: 'bob',
            msgUuid: legacy.msg_uuid, sentAt: legacy.sent_at || Date.now(),
        };
        const asOld = await bob.session.openMessage(alice.publicKey, legacy.ciphertext, legacy.iv,
            { ...base, v: version }, { messageId: 'r' });
        assert.equal(asOld.status, 'legacy');
        // Relabelled as v4 it needs an epk; give it the sender's own static
        // key, the most plausible forgery. It still does not authenticate.
        assert.equal(await bob.session.decrypt(alice.publicKey, legacy.ciphertext, legacy.iv,
            { ...base, v: 4, epk: alice.publicKey }), null);
    });
}

test('an old client cannot read a v4 message, so nothing silently falls back', async () => {
    const bobStore = makeIndexedDB();
    const alice = await newSession('alice');
    const bob = await newSession('bob', bobStore);
    const sent = await send(alice, bob, 'new format');
    const v3Bob = await newSession('bob', bobStore, E2EE_V3);
    assert.equal(await v3Bob.session.decrypt(alice.publicKey, sent.ciphertext, sent.iv,
        { v: 3, conversation: 'dm', senderId: 'alice', recipientId: 'bob', msgUuid: sent.msg_uuid,
          sentAt: sent.sent_at }), null);
});

// ----------------------------------------------------------------------
// Freshness: the authenticated sent_at must be recent
// ----------------------------------------------------------------------

for (const [label, offset, expected] of [
    ['64 minutes old', -64 * MINUTE, 'ok'],
    ['66 minutes old', -66 * MINUTE, 'unverifiable'],
    ['4 minutes in the future', 4 * MINUTE, 'ok'],
    ['6 minutes in the future', 6 * MINUTE, 'unverifiable'],
]) {
    test(`a message whose sent_at is ${label} is ${expected}`, async () => {
        const alice = await newSession('alice');
        const bob = await newSession('bob');
        const sent = await alice.session.encrypt(bob.publicKey, 'timed', {
            recipientId: 'bob', sentAt: Date.now() + offset });
        const opened = await openFull(bob, alice, bob, sent, 'row-1');
        assert.equal(opened.status, expected);
        if (expected !== 'ok') assert.equal(opened.text, undefined, 'nothing is handed back');
    });
}

test('freshness is judged on the authenticated time, not a relabelled one', async () => {
    // A server that rewrites sent_at to look fresh breaks the tag instead.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await alice.session.encrypt(bob.publicKey, 'old', {
        recipientId: 'bob', sentAt: Date.now() - 90 * MINUTE });
    const relabelled = await openFull(bob, alice, bob, sent, 'row-1', { sentAt: Date.now() });
    assert.equal(relabelled.status, 'failed');
});

// ----------------------------------------------------------------------
// Replay: remembered in IndexedDB, so a reload does not forget it
// ----------------------------------------------------------------------

test('the same ciphertext under a second row id is a replay', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'pay 10');
    assert.equal((await openFull(bob, alice, bob, sent, 'row-1')).status, 'ok');
    assert.equal((await openFull(bob, alice, bob, sent, 'row-1')).status, 'ok', 'the same row again is not a replay');
    assert.equal((await openFull(bob, alice, bob, sent, 'row-2')).status, 'replay');
});

test('a replay is still caught after reloading the module', async () => {
    const bobStore = makeIndexedDB();
    const alice = await newSession('alice');
    const bob = await newSession('bob', bobStore);
    const sent = await send(alice, bob, 'pay 10');
    assert.equal((await openFull(bob, alice, bob, sent, 'row-1')).status, 'ok');

    // A page reload: fresh script, fresh memory, same IndexedDB.
    const reloaded = await newSession('bob', bobStore);
    assert.equal((await openFull(reloaded, alice, reloaded, sent, 'row-2')).status, 'replay');
    assert.equal((await openFull(reloaded, alice, reloaded, sent, 'row-1')).status, 'ok',
        'history still shows the original');
});

test('concurrent opens of two rows with one uuid let only one through', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'pay 10');
    const results = await Promise.all([
        openFull(bob, alice, bob, sent, 'row-1'),
        openFull(bob, alice, bob, sent, 'row-2'),
    ]);
    assert.deepEqual(results.map((r) => r.status).sort(), ['ok', 'replay']);
});

test('replay entries expire at sent_at + 65 minutes and are pruned on load', async () => {
    const bobStore = makeIndexedDB();
    const alice = await newSession('alice');
    const bob = await newSession('bob', bobStore);
    const sentAt = Date.now() - 64 * MINUTE;
    const sent = await alice.session.encrypt(bob.publicKey, 'nearly stale', { recipientId: 'bob', sentAt });
    assert.equal((await openFull(bob, alice, bob, sent, 'row-1')).status, 'ok');

    const seen = () => new Promise((resolve) => {
        const req = bobStore.open('revealx-e2ee-replay');
        req.onsuccess = () => {
            const get = req.result.transaction('seen', 'readonly').objectStore('seen').getAll();
            get.onsuccess = () => resolve(get.result);
        };
    });
    const [entry] = await seen();
    assert.equal(entry.expiresAt, sentAt + 65 * MINUTE);
    assert.ok(entry.key.startsWith('dm|alice|bob|'), entry.key);

    // Reload two minutes later: the entry has expired and goes on load. That
    // opens no window, because the message itself is now stale and refused.
    const realNow = Date.now;
    const later = class extends Date { static now() { return realNow() + 2 * MINUTE; } };
    const window = makeWindow({ indexedDB: bobStore });
    loadBrowserScript(E2EE, { window, indexedDB: bobStore, console, Date: later });
    await window.revealxE2EE.init('bob');
    await window.revealxE2EE.pruneReplayStore();
    assert.deepEqual(await seen(), []);
    const again = await window.revealxE2EE.openMessage(alice.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'), { messageId: 'row-2' });
    assert.equal(again.status, 'unverifiable');
});

test('without IndexedDB the replay check still works for the page\'s lifetime', async () => {
    // Private windows can refuse IndexedDB. The check then degrades to memory
    // rather than letting every replay through.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'pay 10');
    bob.session.replayStoreBroken = true;
    assert.equal((await openFull(bob, alice, bob, sent, 'row-1')).status, 'ok');
    assert.equal((await openFull(bob, alice, bob, sent, 'row-2')).status, 'replay');
});


// ----------------------------------------------------------------------
// Key pinning: trust on first use, and a loud change after that
// ----------------------------------------------------------------------

test('a peer key is pinned the first time it is seen', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');

    const first = await alice.session.checkPeerKey('bob', bob.publicKey);
    assert.equal(first.status, 'new');
    assert.equal(first.verified, false);

    const again = await alice.session.checkPeerKey('bob', bob.publicKey);
    assert.equal(again.status, 'match');
});

test('a different key for a pinned peer is reported as changed, not adopted', async () => {
    // The server hands out public keys. Without a pin, swapping Bob's key
    // for one the server holds is invisible: Alice encrypts to the server.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const mallory = await newSession('mallory');

    await alice.session.checkPeerKey('bob', bob.publicKey);
    const swapped = await alice.session.checkPeerKey('bob', mallory.publicKey);
    assert.equal(swapped.status, 'changed');
    assert.equal(swapped.previous, bob.publicKey);

    // Asking again does not quietly accept it.
    assert.equal((await alice.session.checkPeerKey('bob', mallory.publicKey)).status, 'changed');
    assert.equal((await alice.session.checkPeerKey('bob', bob.publicKey)).status, 'match');
});

test('accepting a changed key re-pins it and clears verification', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    await alice.session.checkPeerKey('bob', bob.publicKey);
    assert.equal(await alice.session.setPeerVerified('bob', bob.publicKey, true), true);
    assert.equal((await alice.session.checkPeerKey('bob', bob.publicKey)).verified, true);

    const rotated = await bob.session.init('bob', { regenerate: true });
    await alice.session.acceptPeerKey('bob', rotated);

    const now = await alice.session.checkPeerKey('bob', rotated);
    assert.equal(now.status, 'match');
    assert.equal(now.verified, false, 'verification belonged to the old key');
});

test('verification can only be recorded for the pinned key', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const mallory = await newSession('mallory');
    await alice.session.checkPeerKey('bob', bob.publicKey);

    assert.equal(await alice.session.setPeerVerified('bob', mallory.publicKey, true), false);
    assert.equal((await alice.session.checkPeerKey('bob', bob.publicKey)).verified, false);
});

test('pins survive a reload and belong to the local account', async () => {
    const store = makeIndexedDB();
    const alice = await newSession('alice', store);
    const bob = await newSession('bob');
    const mallory = await newSession('mallory');
    await alice.session.checkPeerKey('bob', bob.publicKey);

    const reloaded = await newSession('alice', store);
    assert.equal((await reloaded.session.checkPeerKey('bob', mallory.publicKey)).status, 'changed');

    // Another account signing in on the same browser has its own pins.
    const other = await newSession('dave', store);
    assert.equal((await other.session.checkPeerKey('bob', mallory.publicKey)).status, 'new');
});

test('a peer with no key is reported as such and nothing is pinned', async () => {
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    assert.equal((await alice.session.checkPeerKey('bob', '')).status, 'none');
    assert.equal((await alice.session.checkPeerKey('bob', bob.publicKey)).status, 'new');
});

test('signing out forgets the copies of sent messages', async () => {
    // Sign-out does not reload the page, so the copies would otherwise stay in
    // memory for whoever uses the browser next.
    const alice = await newSession('alice');
    const bob = await newSession('bob');
    const sent = await send(alice, bob, 'gone at sign-out');
    alice.session.forgetSentCopies();
    const opened = await alice.session.openMessage(bob.publicKey, sent.ciphertext, sent.iv,
        ctx(sent, 'alice', 'bob'), { messageId: 'row-1' });
    assert.equal(opened.status, 'own-sealed');
});
