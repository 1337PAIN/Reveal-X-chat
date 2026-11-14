/**
 * The one status chip every message carries (app/static/js/chips.js).
 *
 * Encryption state was a bare padlock icon in the header (no words), a red
 * "[not encrypted]" badge, and on shares a red "ONE-TIME ACCESS SHARE"
 * notice (3.34:1, failing even AA) plus a separate expiry line. Red is now
 * kept for tamper and error states only; every message gets one chip that
 * says what it is in words.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'chips.js'), { window });
const { messageChip } = window.revealxChips;

test('encrypted text and voice say E2EE, with the padlock kept as decoration', () => {
    for (const type of ['text', 'voice']) {
        const chip = messageChip({ type, encrypted: true });
        assert.equal(chip.text, 'E2EE');
        assert.equal(chip.kind, 'secure');
        assert.equal(chip.icon, '🔒');
        assert.match(chip.title, /end-to-end encrypted/i);
    }
});

test('plaintext says so in words', () => {
    const chip = messageChip({ type: 'text', encrypted: false });
    assert.equal(chip.text, 'Not encrypted');
    assert.equal(chip.kind, 'warning');
});

test('a share says it opens once and how long is left', () => {
    assert.equal(messageChip({ type: 'share', minutesLeft: 37 }).text, 'Opens once - 37 min left');
    assert.equal(messageChip({ type: 'share', minutesLeft: 1 }).text, 'Opens once - 1 min left');
    assert.equal(messageChip({ type: 'share', minutesLeft: 0 }).text, 'Expired');
    assert.equal(messageChip({ type: 'share', minutesLeft: 12 }).kind, 'neutral');
});

test('no chip is ever the danger colour; red is for tamper and errors', () => {
    const cases = [
        { type: 'text', encrypted: true }, { type: 'text', encrypted: false },
        { type: 'voice', encrypted: false }, { type: 'share', minutesLeft: 5 },
        { type: 'share', minutesLeft: 0 },
    ];
    for (const c of cases) assert.notEqual(messageChip(c).kind, 'danger');
});
