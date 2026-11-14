/**
 * Keeping the keyboard inside the open dialog (app/static/js/modal-focus.js).
 *
 * The dialogs said aria-modal="true", but that only tells a screen reader;
 * the keyboard was not told. Shift+Tab from the sign-in dialog's username
 * box went to "Rebuild an image" in the chat behind it, a chat the visitor
 * had not signed in to. Focus that lands outside the topmost open dialog is
 * now sent back in, so Tab and Shift+Tab cycle within it.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'modal-focus.js'), { window });
const { topDialog, redirect } = window.revealxModalFocus;

test('no dialog open: nothing is on top', () => {
    assert.equal(topDialog([false, false, false]), -1);
});

test('a dialog opened from another (confirm over settings) is the one on top', () => {
    // Later in the document is on top: confirmDialog and totpModal come after
    // the dialogs they are opened from.
    assert.equal(topDialog([false, true, false, true]), 3);
});

test('focus is left alone when no dialog is open', () => {
    assert.equal(redirect({ dialogOpen: false, insideDialog: false, insideLive: false, backwards: true }), null);
});

test('focus moving within the dialog is left alone', () => {
    assert.equal(redirect({ dialogOpen: true, insideDialog: true, insideLive: false, backwards: false }), null);
});

test('Tab out of the end of the dialog wraps to its first control', () => {
    assert.equal(redirect({ dialogOpen: true, insideDialog: false, insideLive: false, backwards: false }), 'first');
});

test('Shift+Tab out of the start wraps to its last control, not the page behind', () => {
    assert.equal(redirect({ dialogOpen: true, insideDialog: false, insideLive: false, backwards: true }), 'last');
});

// Opening and closing. Settings opened with focus left on the Settings
// button behind it, and closing it dropped focus to <body>, so the next Tab
// started again from the top of the page.

const { focusAction } = window.revealxModalFocus;

test('a dialog that opens without taking focus is given it', () => {
    assert.equal(focusAction({ shown: true, focusInside: false, focusNowhere: false, hasOpener: true }), 'enter');
});

test('a dialog that already focused something itself is left alone', () => {
    // askConfirm puts focus on Cancel; that choice is deliberate.
    assert.equal(focusAction({ shown: true, focusInside: true, focusNowhere: false, hasOpener: true }), null);
});

test('closing hands focus back to whatever opened the dialog', () => {
    assert.equal(focusAction({ shown: false, focusInside: false, focusNowhere: true, hasOpener: true }), 'return');
    assert.equal(focusAction({ shown: false, focusInside: true, focusNowhere: false, hasOpener: true }), 'return');
});

test('closing leaves focus alone when the code that closed it already placed it', () => {
    assert.equal(focusAction({ shown: false, focusInside: false, focusNowhere: false, hasOpener: true }), null);
});

test('a dialog open from the start (sign-in) has no opener to go back to', () => {
    assert.equal(focusAction({ shown: false, focusInside: false, focusNowhere: true, hasOpener: false }), null);
});
