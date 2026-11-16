/**
 * Adding contacts by username (app/static/js/contacts-ui.js).
 *
 * The sidebar used to list every account. It now lists contacts, added by
 * username and accepted by the other person. These test the rules of the
 * client side -- what the field sends, what each answer says, which requests
 * are new, and that rows are built from elements with named buttons -- not
 * how it looks.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeDocument } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'contacts-ui.js'), { window });
const ui = window.revealxContactsUI;

function walk(node, out = []) {
    out.push(node);
    (node.children || []).forEach((child) => walk(child, out));
    return out;
}

test('the field forgives spaces and a leading @', () => {
    assert.equal(ui.normaliseUsername('  @bob '), 'bob');
    assert.equal(ui.normaliseUsername('@@Bob'), 'Bob');
});

test('nothing usable is sent as nothing', () => {
    for (const value of ['', '   ', '@', 'two words', 'x'.repeat(65), null, undefined]) {
        assert.equal(ui.normaliseUsername(value), '', String(value));
    }
});

test('each answer says what happens next', () => {
    assert.match(ui.resultMessage({ ok: true, status: 'requested', username: 'bob' }).text,
        /Request sent to bob\. You can chat once they accept\./);
    assert.match(ui.resultMessage({ ok: true, status: 'accepted', username: 'bob' }).text, /now contacts/);
    assert.match(ui.resultMessage({ ok: true, status: 'already_requested', username: 'bob' }).text, /already asked/);
    assert.match(ui.resultMessage({ ok: true, status: 'already_contacts', username: 'bob' }).text, /already one of your contacts/);
    assert.equal(ui.resultMessage({ ok: true, status: 'requested', username: 'bob' }).kind, 'info');
});

test("a refusal shows the server's words as an error", () => {
    const shown = ui.resultMessage({ ok: false, error: 'No account with that username.' });
    assert.deepEqual(shown, { text: 'No account with that username.', kind: 'error' });
    assert.equal(ui.resultMessage(null).kind, 'error');
});

test('only requests that were not there before are new', () => {
    const before = { incoming: [{ id: 'a' }] };
    const after = { incoming: [{ id: 'a' }, { id: 'b' }] };
    assert.deepEqual(ui.newIncoming(before, after).map((r) => r.id), ['b']);
    assert.deepEqual(ui.newIncoming(null, after).map((r) => r.id), ['a', 'b']);
    assert.deepEqual(ui.newIncoming(after, { incoming: [] }), []);
});

test('an incoming request offers Accept and Decline, named for the person', () => {
    const doc = makeDocument();
    const answered = [];
    const row = ui.buildRequestRow(doc, { id: 'u1', username: '<b>bob</b>' }, {
        incoming: true,
        onAccept: (e) => answered.push(['accept', e.id]),
        onDecline: (e) => answered.push(['decline', e.id]),
    });
    assert.equal(doc.innerHTMLWrites, 0);
    const buttons = walk(row).filter((n) => n.tagName === 'BUTTON');
    assert.deepEqual(buttons.map((b) => b.textContent), ['Accept', 'Decline']);
    assert.deepEqual(buttons.map((b) => b.getAttribute('aria-label')), ['Accept <b>bob</b>', 'Decline <b>bob</b>']);
    buttons.forEach((b) => b.click());
    assert.deepEqual(answered, [['accept', 'u1'], ['decline', 'u1']]);
    // The name went in as text.
    assert.match(row.textContent, /<b>bob<\/b>wants to add you/);
});

test('your own request can be cancelled, and nothing else', () => {
    const doc = makeDocument();
    const cancelled = [];
    const row = ui.buildRequestRow(doc, { id: 'u2', username: 'carol' }, {
        onCancel: (e) => cancelled.push(e.id),
    });
    const buttons = walk(row).filter((n) => n.tagName === 'BUTTON');
    assert.deepEqual(buttons.map((b) => b.textContent), ['Cancel']);
    assert.equal(buttons[0].getAttribute('aria-label'), 'Cancel request to carol');
    buttons[0].click();
    assert.deepEqual(cancelled, ['u2']);
    assert.match(row.textContent, /waiting for them to accept/);
});

test('the initial is decoration', () => {
    const doc = makeDocument();
    const row = ui.buildRequestRow(doc, { id: 'u3', username: 'dave' }, { incoming: true });
    const avatar = walk(row).find((n) => (n.className || '').includes('request-avatar'));
    assert.equal(avatar.getAttribute('aria-hidden'), 'true');
    assert.equal(avatar.textContent, 'D');
});
