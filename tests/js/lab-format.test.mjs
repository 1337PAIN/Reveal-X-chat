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

// "Run all attacks": the README's table of which attacks the ML and the HMAC
// catch, reproduced live on the uploaded image. These are its pure parts.

test('the attacks run in the README table\'s order, with its names', () => {
    assert.deepEqual(fmt.ATTACKS.map((a) => a.label), [
        'Block replacement', 'Blur region', 'Brightness shift', 'Crop + resize',
        'Scratch/line', 'JPEG damage', 'Random noise injection',
    ]);
    assert.deepEqual(fmt.ATTACKS.map((a) => a.value), [
        'block', 'blur', 'brightness', 'crop', 'scratch', 'jpeg', 'noise',
    ]);
});

test('a row says yes or no for each detector, with rounded quality', () => {
    const row = fmt.attackRow('Block replacement', {
        ml: { tampered: true },
        integrity: { exact_tamper_detected: true },
        metrics: { reconstructed: { psnr: 14.2611, ssim: 0.8412 } },
    });
    assert.deepEqual(row, {
        attack: 'Block replacement', ml: 'yes', hmac: 'yes',
        psnr: '14.26 dB', ssim: '0.84', missedByMl: false,
    });
});

test('an attack the HMAC catches and the ML does not is marked as missed', () => {
    // Random noise on a random share changes nothing the features measure.
    const row = fmt.attackRow('Random noise injection', {
        ml: { tampered: false },
        integrity: { exact_tamper_detected: true },
        metrics: { reconstructed: { psnr: 20, ssim: 0.9 } },
    });
    assert.equal(row.ml, 'no');
    assert.equal(row.hmac, 'yes');
    assert.equal(row.missedByMl, true);
});

test('the wait in a rate-limit answer is read back in seconds', () => {
    assert.equal(fmt.retryAfter('Too many requests. Try again in 12s.'), 12);
    assert.equal(fmt.retryAfter('Processing failed on the server'), null);
    assert.equal(fmt.retryAfter(undefined), null);
});

// Strength sweep: one attack at six strengths, drawn as an inline SVG (no
// chart library: nothing third-party may load) with the same numbers in a
// table. sweepChart() is the geometry; the page only draws what it returns.

const box = { width: 400, height: 200, pad: { top: 10, right: 10, bottom: 30, left: 40 } };
const points = [
    { strength: 0.75, ssim: 0.2, ml: 0.95 },
    { strength: 0.05, ssim: 0.95, ml: 0.1 },
    { strength: 0.35, ssim: 0.6, ml: 0.7 },
];

test('the sweep runs the strengths the server accepts, weakest first', () => {
    assert.deepEqual(fmt.SWEEP_STRENGTHS, [0.05, 0.15, 0.25, 0.35, 0.5, 0.75]);
});

test('strength runs left to right across the plot area', () => {
    const chart = fmt.sweepChart(points, box);
    // 0.35 is 30/70 of the way from 5% to 75%: 40 + (0.3 / 0.7) * 350 = 190.
    assert.deepEqual(chart.points.map((p) => Math.round(p.x)), [40, 190, 390]);
});

test('both series share a 0-to-1 scale, 1 at the top', () => {
    const chart = fmt.sweepChart(points, box);
    const first = chart.points[0];
    assert.equal(first.ssimY, 10 + (1 - 0.95) * 160);
    assert.equal(first.mlY, 10 + (1 - 0.1) * 160);
});

test('each series is one path through its points in order', () => {
    const chart = fmt.sweepChart(points, box);
    assert.match(chart.ssimPath, /^M40(\.0+)? \d/);
    assert.equal((chart.ssimPath.match(/L/g) || []).length, 2);
    assert.equal((chart.mlPath.match(/L/g) || []).length, 2);
});

test('axes are labelled: each strength as a percentage, 0 / 0.5 / 1 up the side', () => {
    const chart = fmt.sweepChart(points, box);
    assert.deepEqual(chart.xTicks.map((t) => t.label), ['5%', '35%', '75%']);
    assert.deepEqual(chart.yTicks.map((t) => t.label), ['0', '0.5', '1']);
    assert.deepEqual(chart.yTicks.map((t) => t.y), [170, 90, 10]);
});

test('a one-line summary for screen readers says where SSIM and the ML end up', () => {
    assert.equal(fmt.sweepSummary('Block replacement', points),
        'Block replacement from 5% to 75%: SSIM falls from 0.95 to 0.20; ML tamper probability goes from 0.10 to 0.95.');
});
