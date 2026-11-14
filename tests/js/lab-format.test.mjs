/**
 * Display rules for /lab (app/static/js/lab-format.js).
 *
 * When enhancement would have lowered SSIM the server reports the plain
 * reconstruction in the "Enhanced output" slot (metrics.quality_safe_fallback)
 * -- but the card above it still said "Denoised and sharpened output", so the
 * page described an image it was not showing.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'lab-format.js'), { window });
const fmt = window.revealxLabFormat;

test('the enhanced card describes enhancement when it was used', () => {
    const { title, caption } = fmt.enhancedCaption({ quality_safe_fallback: false });
    assert.equal(title, 'Enhanced output');
    assert.match(caption, /Denoised and sharpened/);
});

test('and says plainly when it shows the plain reconstruction instead', () => {
    const { title, caption } = fmt.enhancedCaption({
        quality_safe_fallback: true,
        enhanced_candidate: { ssim: 0.41 },
        reconstructed: { ssim: 0.88 },
    });
    assert.equal(title, 'Enhanced output (not applied)');
    assert.match(caption, /plain reconstruction/);
    assert.match(caption, /0\.41/);
    assert.match(caption, /0\.88/);
    assert.doesNotMatch(caption, /Denoised and sharpened/);
});

test('missing metrics fall back to the neutral wording', () => {
    assert.equal(fmt.enhancedCaption(undefined).title, 'Enhanced output');
});
