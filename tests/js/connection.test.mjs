/**
 * What the connection banner says (app/static/js/connection.js).
 *
 * A dropped socket was shown only as a retry count tucked into the header
 * ("(retrying 3...)"), and a session the server no longer recognised showed
 * nothing at all until the sign-in dialog appeared. The banner now says
 * "Reconnecting..." while the socket is down and "Signed out - sign in
 * again" when the server answered auth_required and the session could not
 * be resumed, and goes away once the session is back.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'connection.js'), { window });
const { bannerFor } = window.revealxConnection;

test('nothing to say while connected and signed in', () => {
    assert.equal(bannerFor({ connected: true, signedOut: false }).visible, false);
});

test('a dropped socket says it is reconnecting', () => {
    const banner = bannerFor({ connected: false, signedOut: false });
    assert.equal(banner.visible, true);
    assert.equal(banner.text, 'Reconnecting...');
    assert.equal(banner.kind, 'info');
});

test('a session the server no longer knows says so plainly', () => {
    const banner = bannerFor({ connected: true, signedOut: true });
    assert.equal(banner.visible, true);
    assert.equal(banner.text, 'Signed out - sign in again');
    assert.equal(banner.kind, 'warning');
});

test('signed out wins over reconnecting: there is nothing to reconnect to', () => {
    assert.equal(bannerFor({ connected: false, signedOut: true }).text, 'Signed out - sign in again');
});
