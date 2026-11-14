/**
 * One row of the contact list (app/static/js/contact-row.js).
 *
 * Rows were <li> elements with a click listener: not focusable, not
 * announced as controls, unreachable by keyboard. Each now carries a real
 * <button type="button">, so Tab reaches it and Enter or Space opens the chat
 * with no extra key handling, and the open chat is marked aria-current.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow, makeDocument } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'contact-row.js'), { window });
const { buildContactRow } = window.revealxContactRow;

const alice = { id: 'a1', username: 'alice', status: 'online', online: true, bio: '' };

function row(user = alice, options = {}) {
    const doc = makeDocument();
    const li = buildContactRow(doc, user, { selected: false, unread: 0, statusLabel: 'Online', ...options });
    return { doc, li, button: li.querySelectorAll('button')[0] };
}

test('each account is a button inside its list item', () => {
    const { li, button } = row();
    assert.equal(li.tagName, 'LI');
    assert.ok(button, 'no button');
    assert.equal(button.getAttribute('type'), 'button');
    assert.match(button.textContent, /alice/);
});

test('only the open chat is aria-current', () => {
    assert.equal(row(alice, { selected: true }).button.getAttribute('aria-current'), 'true');
    assert.equal(row(alice, { selected: false }).button.getAttribute('aria-current'), null);
});

test('activating the button opens that chat', () => {
    const opened = [];
    const { button } = row(alice, { onSelect: (id) => opened.push(id) });
    button.click();
    assert.deepEqual(opened, ['a1']);
});

test('a photo is an img with .src set, and nothing goes through innerHTML', () => {
    const user = { ...alice, profile_image: 'data:image/png;base64,AAAA' };
    const { doc, li } = row(user);
    const img = li.querySelectorAll('img')[0];
    assert.equal(img.src, 'data:image/png;base64,AAAA');
    assert.equal(img.alt, '');
    assert.equal(doc.innerHTMLWrites, 0);
});

test('text from other users is set as text', () => {
    const user = { ...alice, username: '<b>x</b>', bio: '<img src=x>' };
    const { doc, button } = row(user, { statusLabel: 'Online' });
    assert.match(button.textContent, /<b>x<\/b>/);
    assert.equal(doc.innerHTMLWrites, 0);
});

test('unread count, presence and state are exposed', () => {
    const { li, button } = row({ ...alice, online: false, status: 'away' }, { unread: 3 });
    assert.ok(li.classList.contains('offline'));
    assert.equal(li.dataset.status, 'away');
    assert.match(button.textContent, /3/);
    const badge = li.querySelectorAll('span').find((s) => s.className === 'unread-badge');
    assert.equal(badge.getAttribute('aria-label'), '3 unread messages');
});
