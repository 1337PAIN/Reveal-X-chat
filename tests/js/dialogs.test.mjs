/**
 * In-app confirmation and message menus (app/static/js/dialogs.js).
 *
 * Deleting a message used window.confirm(): unstyled, blocking, impossible to
 * theme, and announced by some screen readers as a browser alert unrelated to
 * the app. The delete button was also a bare 16px "x" beside the timestamp,
 * an easy mis-tap on a phone. Delete now lives in a per-message menu and asks
 * through an in-app dialog, both driven by this file.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

let doc;

function element(id) {
    const listeners = {};
    const classes = new Set(id === 'confirmDialog' ? ['modal', 'hidden'] : []);
    return {
        id,
        textContent: '',
        attributes: {},
        hidden: false,
        classList: {
            add: (c) => classes.add(c),
            remove: (c) => classes.delete(c),
            contains: (c) => classes.has(c),
        },
        setAttribute(n, v) { this.attributes[n] = String(v); },
        getAttribute(n) { return n in this.attributes ? this.attributes[n] : null; },
        addEventListener(kind, fn) { (listeners[kind] ||= new Set()).add(fn); },
        removeEventListener(kind, fn) { if (listeners[kind]) listeners[kind].delete(fn); },
        dispatch(kind, event = {}) {
            [...(listeners[kind] || [])].forEach((fn) => fn({
                preventDefault() {}, stopPropagation() {}, target: this, ...event,
            }));
        },
        focus() { doc.activeElement = this; },
        contains(other) { return other === this; },
        querySelector() { return null; },
    };
}

function setup() {
    const nodes = {};
    for (const id of ['confirmDialog', 'confirmDialogTitle', 'confirmDialogBody',
        'confirmDialogOk', 'confirmDialogCancel']) {
        nodes[id] = element(id);
    }
    const docListeners = {};
    doc = {
        activeElement: null,
        getElementById: (id) => nodes[id] || null,
        addEventListener(kind, fn) { (docListeners[kind] ||= new Set()).add(fn); },
        removeEventListener(kind, fn) { if (docListeners[kind]) docListeners[kind].delete(fn); },
        key(key) {
            [...(docListeners.keydown || [])].forEach((fn) => fn({ key, preventDefault() {} }));
        },
    };
    const window = makeWindow();
    loadBrowserScript(join(STATIC_JS, 'dialogs.js'), { window, document: doc });
    return { api: window.revealxDialogs, nodes };
}

test('askConfirm shows the question as text and resolves true on confirm', async () => {
    const { api, nodes } = setup();
    const answer = api.askConfirm({ title: 'Delete?', body: '<b>gone</b>', confirmLabel: 'Delete' });
    assert.equal(nodes.confirmDialog.classList.contains('hidden'), false);
    assert.equal(nodes.confirmDialogBody.textContent, '<b>gone</b>');
    assert.equal(nodes.confirmDialogOk.textContent, 'Delete');
    nodes.confirmDialogOk.dispatch('click');
    assert.equal(await answer, true);
    assert.equal(nodes.confirmDialog.classList.contains('hidden'), true);
});

test('cancel and Escape both resolve false', async () => {
    const { api, nodes } = setup();
    const a = api.askConfirm({ title: 't', body: 'b' });
    nodes.confirmDialogCancel.dispatch('click');
    assert.equal(await a, false);
    const b = api.askConfirm({ title: 't', body: 'b' });
    doc.key('Escape');
    assert.equal(await b, false);
});

test('focus starts on Cancel, the safe choice, and returns to the opener', async () => {
    const { api, nodes } = setup();
    const opener = element('opener');
    doc.activeElement = opener;
    const answer = api.askConfirm({ title: 't', body: 'b' });
    assert.equal(doc.activeElement, nodes.confirmDialogCancel);
    nodes.confirmDialogCancel.dispatch('click');
    await answer;
    assert.equal(doc.activeElement, opener);
});

test('a second question while one is open does not leave the first hanging', async () => {
    const { api, nodes } = setup();
    const first = api.askConfirm({ title: 'one', body: 'b' });
    const second = api.askConfirm({ title: 'two', body: 'b' });
    assert.equal(await first, false, 'the first is answered "no" when replaced');
    nodes.confirmDialogOk.dispatch('click');
    assert.equal(await second, true);
});

test('a message menu opens, reports its state and closes on Escape', () => {
    const { api } = setup();
    const trigger = element('trigger');
    const menu = element('menu');
    menu.hidden = true;
    const item = element('item');
    menu.querySelector = () => item;
    api.wireMenu(trigger, menu);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    trigger.dispatch('click');
    assert.equal(menu.hidden, false);
    assert.equal(trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(doc.activeElement, item, 'focus moves into the menu');
    doc.key('Escape');
    assert.equal(menu.hidden, true);
    assert.equal(trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(doc.activeElement, trigger, 'and back to the button');
});

// ----------------------------------------------------------------------
// Toasts and notices: what alert() was used for
// ----------------------------------------------------------------------

function toastSetup() {
    const created = [];
    const body = { children: [], appendChild(el) { this.children.push(el); return el; } };
    const nodes = {};
    for (const id of ['confirmDialog', 'confirmDialogTitle', 'confirmDialogBody',
        'confirmDialogOk', 'confirmDialogCancel']) nodes[id] = element(id);
    const docListeners = {};
    doc = {
        activeElement: null,
        body,
        getElementById: (id) => nodes[id] || created.find((e) => e.id === id) || null,
        createElement: () => { const el = element(''); created.push(el); return el; },
        addEventListener(kind, fn) { (docListeners[kind] ||= new Set()).add(fn); },
        removeEventListener(kind, fn) { if (docListeners[kind]) docListeners[kind].delete(fn); },
    };
    const window = makeWindow({ setTimeout: () => 0, clearTimeout: () => {} });
    loadBrowserScript(join(STATIC_JS, 'dialogs.js'), { window, document: doc, setTimeout: () => 0, clearTimeout: () => {} });
    return { api: window.revealxDialogs, nodes, created };
}

test('a toast is text in a live region: polite for notices, assertive for errors', () => {
    const { api, created } = toastSetup();
    api.toast('<b>Saved</b>');
    const region = created.find((e) => e.id === 'appToast');
    assert.ok(region, 'no toast region');
    assert.equal(region.textContent, '<b>Saved</b>');
    assert.equal(region.getAttribute('role'), 'status');
    assert.equal(region.getAttribute('aria-live'), 'polite');
    api.toast('Could not send', { kind: 'error' });
    assert.equal(region.getAttribute('role'), 'alert');
    assert.equal(region.getAttribute('aria-live'), 'assertive');
});

test('a notice has only an OK button and resolves when it is pressed', async () => {
    const { api, nodes } = toastSetup();
    const seen = api.showNotice({ title: 'Signed out', body: 'An administrator ended this session.' });
    assert.equal(nodes.confirmDialogCancel.hidden, true, 'nothing to cancel');
    assert.equal(nodes.confirmDialogOk.textContent, 'OK');
    nodes.confirmDialogOk.dispatch('click');
    assert.equal(await seen, true);
    assert.equal(nodes.confirmDialogCancel.hidden, false, 'restored for the next question');
});
