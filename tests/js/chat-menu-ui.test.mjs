/**
 * The chat menu and the list it reorders (app/static/js/chat-menu-ui.js).
 *
 * The ⋮ beside a contact's name offers Contact info, Mute, Pin, Archive,
 * Lock, Clear chat, Block and Remove contact; pin, archive, mute and lock
 * then decide where the chat sits in the list and whether it may make a
 * sound. These test those rules, not how it looks.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeDocument } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'chat-menu-ui.js'), { window });
const ui = window.revealxChatMenuUI;

const plain = { id: 'b', username: 'bob', pinned: false, archived: false, muted: false, locked: false };

test('the menu offers everything, the destructive ones last and marked', () => {
    const items = ui.menuItems(plain);
    assert.deepEqual(items.map((i) => i.label), ['Contact info', 'Mute notifications', 'Pin chat',
        'Archive chat', 'Lock chat', 'Clear chat', 'Block', 'Remove contact']);
    assert.deepEqual(items.filter((i) => i.danger).map((i) => i.action), ['clear', 'block', 'remove']);
});

test('each toggle offers the way back', () => {
    const set = { ...plain, pinned: true, archived: true, muted: true, locked: true };
    const labels = ui.menuItems(set).map((i) => i.label);
    for (const label of ['Unmute notifications', 'Unpin chat', 'Unarchive chat', 'Unlock chat']) {
        assert.ok(labels.includes(label), label);
    }
    assert.deepEqual(ui.menuItems(null), []);
});

test('mute, pin and archive flip the setting', () => {
    assert.deepEqual(ui.prefChange('pin', plain), { peer_id: 'b', pref: 'pinned', value: true });
    assert.deepEqual(ui.prefChange('mute', { ...plain, muted: true }), { peer_id: 'b', pref: 'muted', value: false });
    assert.equal(ui.prefChange('lock', plain), null);    // locking has its own path (the PIN)
});

test('pinned chats come first, the latest pin on top', () => {
    const users = [
        { ...plain, id: 'a', username: 'amy' },
        { ...plain, id: 'b', username: 'bob', pinned: true, pinned_at: '2026-11-17T10:00:00' },
        { ...plain, id: 'c', username: 'cat', pinned: true, pinned_at: '2026-11-17T11:00:00' },
        { ...plain, id: 'd', username: 'dan' },
    ];
    assert.deepEqual(ui.organise(users).main.map((u) => u.username), ['cat', 'bob', 'amy', 'dan']);
});

test('archived chats leave the main list', () => {
    const users = [plain, { ...plain, id: 'z', username: 'zed', archived: true, pinned: true }];
    const groups = ui.organise(users);
    assert.deepEqual(groups.main.map((u) => u.username), ['bob']);
    assert.deepEqual(groups.archived.map((u) => u.username), ['zed']);
});

test('locked chats are out of sight until the PIN is entered', () => {
    const users = [plain, { ...plain, id: 'l', username: 'lou', locked: true }];
    const closed = ui.organise(users, { lockOpen: false });
    assert.deepEqual(closed.main.map((u) => u.username), ['bob']);
    assert.equal(closed.lockedHidden, 1);
    const open = ui.organise(users, { lockOpen: true });
    assert.deepEqual(open.main.map((u) => u.username), ['bob', 'lou']);
    assert.equal(open.lockedHidden, 0);
});

test('a chat is hidden when locked and closed, or archived and folded', () => {
    assert.equal(ui.isHidden({ ...plain, locked: true }, { lockOpen: false }), true);
    assert.equal(ui.isHidden({ ...plain, locked: true }, { lockOpen: true }), false);
    assert.equal(ui.isHidden({ ...plain, archived: true }, { showArchived: false }), true);
    assert.equal(ui.isHidden(plain, {}), false);
});

test('a muted chat is silent and a locked one names nobody', () => {
    assert.equal(ui.notification({ ...plain, muted: true }, 'bob', 'hi'), null);
    assert.deepEqual(ui.notification({ ...plain, locked: true }, 'bob', 'hi'),
        { title: 'Reveal-X', body: 'New message', sound: true });
    assert.deepEqual(ui.notification(plain, 'bob', 'hi'),
        { title: 'New message from bob', body: 'hi', sound: true });
});

test('the row says in words what the icons show', () => {
    assert.equal(ui.flagWords(plain), '');
    assert.equal(ui.flagWords({ ...plain, pinned: true, muted: true }), ', pinned, muted');
});

test('a PIN is four to eight digits', () => {
    for (const ok of ['1234', '12345678']) assert.equal(ui.pinProblem(ok), '');
    for (const bad of ['123', '123456789', '12a4', '', null]) assert.notEqual(ui.pinProblem(bad), '', String(bad));
});

test('the menu is built as named menu items', () => {
    const doc = makeDocument();
    const menu = doc.createElement('span');
    const picked = [];
    ui.buildMenu(doc, menu, ui.menuItems(plain), (action) => picked.push(action));
    assert.equal(doc.innerHTMLWrites, 0);
    assert.equal(menu.children.length, 8);
    assert.ok(menu.children.every((b) => b.getAttribute('role') === 'menuitem'));
    assert.equal(menu.children[7].className, 'message-menu-item danger');
    menu.children[2].click();
    assert.deepEqual(picked, ['pin']);
});
