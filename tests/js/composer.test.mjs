/**
 * The composer's character counter (app/static/js/composer.js).
 *
 * The field's maxlength was 500 while the server and the client both allow
 * 2,000, so long messages were cut off silently at a quarter of the limit.
 * The counter appears near the limit and is announced politely at a few
 * thresholds -- not on every keystroke, which would drown out typing.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'composer.js'), { window });
const { counterState } = window.revealxComposer;

test('hidden until the last 200 characters', () => {
    assert.equal(counterState(0, 2000).visible, false);
    assert.equal(counterState(1799, 2000).visible, false);
    assert.equal(counterState(1800, 2000).visible, true);
});

test('says how many are left, in words', () => {
    assert.equal(counterState(1850, 2000).text, '150 characters left');
    assert.equal(counterState(1999, 2000).text, '1 character left');
    assert.equal(counterState(2000, 2000).text, 'Character limit reached');
});

test('announces only when a threshold is crossed', () => {
    assert.equal(counterState(1799, 2000, 1798).announce, null);
    assert.match(counterState(1800, 2000, 1799).announce, /200 characters left/);
    assert.equal(counterState(1801, 2000, 1800).announce, null, 'no announcement per keystroke');
    assert.match(counterState(1950, 2000, 1949).announce, /50 characters left/);
    assert.match(counterState(2000, 2000, 1999).announce, /limit reached/i);
    assert.equal(counterState(1990, 2000, 1995).announce, null, 'deleting does not announce');
});

test('marks the near-limit and at-limit states for styling', () => {
    assert.equal(counterState(1500, 2000).level, 'ok');
    assert.equal(counterState(1900, 2000).level, 'near');
    assert.equal(counterState(2000, 2000).level, 'full');
});
