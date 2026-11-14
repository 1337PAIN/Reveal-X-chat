/**
 * When may a text or voice message leave the browser, and in what form?
 * (app/static/js/send-policy.js)
 *
 * Both send paths in chat.js used to wrap encryption in try/catch and, on any
 * error, log it and send the message in plaintext. An encryption failure is
 * the one moment the user most needs the message *not* to go out, and the
 * fallback turned it into a silent downgrade: the server stored and relayed
 * the words in the clear, and the only trace was a console line. A peer with
 * no published key was handled the same way -- straight to plaintext, with a
 * label after the fact.
 *
 * The decision now lives in one pure function so it can be tested here
 * against every combination, instead of being re-derived at each call site.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'send-policy.js'), { window });
const { decideSend, afterEncrypt } = window.revealxSendPolicy;

const base = { e2eeReady: true, peerHasKey: true, pinStatus: 'match', plaintextConfirmed: false };

test('both keys present: encrypt', () => {
    assert.equal(decideSend(base).action, 'encrypt');
    assert.equal(decideSend({ ...base, pinStatus: 'new' }).action, 'encrypt');
});

test('a changed key blocks the send outright', () => {
    for (const plaintextConfirmed of [false, true]) {
        const d = decideSend({ ...base, pinStatus: 'changed', plaintextConfirmed });
        assert.equal(d.action, 'blocked');
        assert.equal(d.reason, 'key-changed');
    }
});

test('a peer without a key needs explicit consent before plaintext', () => {
    const d = decideSend({ ...base, peerHasKey: false, pinStatus: 'none' });
    assert.equal(d.action, 'confirm-plaintext');
    assert.equal(d.reason, 'peer-has-no-key');
});

test('consent, once given for this conversation, allows plaintext', () => {
    const d = decideSend({ ...base, peerHasKey: false, pinStatus: 'none', plaintextConfirmed: true });
    assert.equal(d.action, 'plaintext');
});

test('this browser without E2EE also needs consent, never a silent downgrade', () => {
    const d = decideSend({ ...base, e2eeReady: false });
    assert.equal(d.action, 'confirm-plaintext');
    assert.equal(d.reason, 'e2ee-unavailable');
    assert.equal(decideSend({ ...base, e2eeReady: false, plaintextConfirmed: true }).action, 'plaintext');
});

test('consent does not override encryption when encryption is possible', () => {
    // Consent was given while the peer had no key. Once they publish one,
    // messages are encrypted again rather than staying plaintext for good.
    assert.equal(decideSend({ ...base, plaintextConfirmed: true }).action, 'encrypt');
});

test('an encryption failure is an error, never plaintext', () => {
    assert.equal(afterEncrypt(null).action, 'error');
    assert.equal(afterEncrypt(undefined).action, 'error');
    assert.equal(afterEncrypt(new Error('boom')).action, 'error');
    assert.equal(afterEncrypt({ ciphertext: 'x' }).action, 'error', 'no iv');
    assert.equal(afterEncrypt({ ciphertext: 'x', iv: 'y', v: 3, msg_uuid: 'u', sent_at: 1 }).action, 'send');
});

test('missing or odd inputs fail toward not sending in the clear', () => {
    assert.notEqual(decideSend({}).action, 'plaintext');
    assert.notEqual(decideSend(undefined).action, 'plaintext');
    assert.notEqual(decideSend({ peerHasKey: true }).action, 'plaintext');
});
