/**
 * What to tell the user when the microphone cannot be opened
 * (app/static/js/mic-errors.js).
 *
 * Every getUserMedia failure used to read "Microphone access was denied." On a
 * PC with no microphone at all -- the server this project is demonstrated
 * from -- the browser refuses at once without asking, so the user saw
 * "denied" with no prompt anywhere and nothing they could allow. Each cause
 * needs its own words, because each has a different fix.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'mic-errors.js'), { window });
const { micErrorMessage } = window.revealxMicErrors;

function failure(name, message = '') {
    const error = new Error(message);
    error.name = name;
    return error;
}

test('no microphone connected says so, not "denied"', () => {
    for (const name of ['NotFoundError', 'DevicesNotFoundError', 'OverconstrainedError']) {
        const text = micErrorMessage(failure(name));
        assert.match(text, /No microphone was found/, name);
        assert.doesNotMatch(text, /denied/i, name);
    }
});

test('a site-level block points at the site settings, since no prompt will appear', () => {
    const text = micErrorMessage(failure('NotAllowedError', 'Permission denied'));
    assert.match(text, /blocked for this site/);
    assert.match(text, /address bar/);
});

test('an operating-system block points at the system settings', () => {
    // Chrome's wording when Windows or macOS privacy settings refuse it.
    const text = micErrorMessage(failure('NotAllowedError', 'Permission denied by system'));
    assert.match(text, /system/);
    assert.match(text, /Privacy/);
    assert.doesNotMatch(text, /address bar/);
});

test('a busy or failing device is not called a permission problem', () => {
    for (const name of ['NotReadableError', 'TrackStartError', 'AbortError']) {
        const text = micErrorMessage(failure(name));
        assert.match(text, /could not be started/, name);
        assert.doesNotMatch(text, /denied|blocked for this site/i, name);
    }
});

test('an insecure page is explained, since browsers only allow the microphone over HTTPS', () => {
    assert.match(micErrorMessage(failure('SecurityError')), /HTTPS/);
});

test('anything unrecognised still gets a sentence, never undefined', () => {
    for (const error of [failure('WeirdError'), null, undefined, 'a string']) {
        const text = micErrorMessage(error);
        assert.equal(typeof text, 'string');
        assert.ok(text.length > 10);
    }
});
