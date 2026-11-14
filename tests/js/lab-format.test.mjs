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

// Metrics to two decimals. The server sends four (SSIM 0.9812, PSNR
// 31.4467 dB, MSE 46.3521) and /lab printed them as sent, so the dashboard
// showed digits the comparison never depends on and columns that did not
// line up. Rounding happens here, for display only; the API is unchanged.

test('a metric is shown to two decimals', () => {
    assert.equal(fmt.metric(31.4467), '31.45');
    assert.equal(fmt.metric(0.9812), '0.98');
    assert.equal(fmt.metric(46.3521), '46.35');
    assert.equal(fmt.metric(1), '1.00');
});

test('a unit follows the number when given', () => {
    assert.equal(fmt.metric(31.4467, 'dB'), '31.45 dB');
});

test('infinity and missing values say so instead of printing NaN', () => {
    // The server caps an exact reconstruction's PSNR at 99 dB, but a value
    // that is not a finite number must never reach the page as "NaN".
    assert.equal(fmt.metric(Infinity, 'dB'), '∞ dB');
    assert.equal(fmt.metric(undefined), '-');
    assert.equal(fmt.metric(null), '-');
    assert.equal(fmt.metric('n/a'), '-');
});

test('the not-applied caption rounds its SSIMs too', () => {
    const { caption } = fmt.enhancedCaption({
        quality_safe_fallback: true,
        enhanced_candidate: { ssim: 0.4123 },
        reconstructed: { ssim: 0.8811 },
    });
    assert.match(caption, /SSIM 0\.41 against 0\.88/);
});

// Two decimals can hide the very difference a sentence is about: the
// enhancement-skipped caption read "scored SSIM 0.86 against 0.86 ... so it
// would have made the image worse". A pair being compared gets as many
// decimals as it takes to tell them apart, up to the four the server sends.

test('a compared pair keeps two decimals when that already separates them', () => {
    assert.deepEqual(fmt.metricPair(0.41, 0.88), ['0.41', '0.88']);
});

test('and adds decimals until the two differ', () => {
    assert.deepEqual(fmt.metricPair(0.8551, 0.8649), ['0.855', '0.865']);
    assert.deepEqual(fmt.metricPair(0.86412, 0.86448), ['0.8641', '0.8645']);
});

test('the skipped-enhancement caption never compares two equal-looking numbers', () => {
    const { caption } = fmt.enhancedCaption({
        quality_safe_fallback: true,
        enhanced_candidate: { ssim: 0.8551 },
        reconstructed: { ssim: 0.8649 },
    });
    assert.match(caption, /SSIM 0\.855 against 0\.865/);
});
