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
    // The count is said in the button's name; the badge itself is hidden,
    // since aria-label on a plain <span> is not allowed and may be ignored.
    const badge = li.querySelectorAll('span').find((s) => s.className === 'unread-badge');
    assert.equal(badge.getAttribute('aria-hidden'), 'true');
    assert.equal(badge.getAttribute('aria-label'), null);
    assert.equal(spokenName(button), 'alice, Online, 3 unread messages');
});

/**
 * The name a screen reader computes from content: text, minus anything under
 * aria-hidden. (The harness document does not compute names itself.)
 */
function spokenName(el) {
    if (el.getAttribute && el.getAttribute('aria-hidden') === 'true') return '';
    return (el._text || '') + el.children.map(spokenName).join('');
}

test('the name and status are read as separate words', () => {
    // Read from content, the name and status line ran together: a screen
    // reader said "e2ebobOffline". A visually hidden ", " now separates them.
    const { button } = row({ ...alice, online: false, status: 'offline' }, { statusLabel: 'Offline' });
    assert.equal(spokenName(button), 'alice, Offline');
});

test('the name comes from the content, not an aria-label', () => {
    // An aria-label of "alice, Online" left out the avatar initial that is
    // visibly part of the button, so the name did not contain the visible
    // text (axe: label-content-name-mismatch). Speech-input users say what
    // they see; the name must contain it.
    const { button } = row({ ...alice }, { statusLabel: 'Online' });
    assert.equal(button.getAttribute('aria-label'), null);
});

test('a bio stands in for the status, as it does on screen', () => {
    const { button } = row({ ...alice, bio: 'at the desk' });
    assert.equal(spokenName(button), 'alice, at the desk');
});
