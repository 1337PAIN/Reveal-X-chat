/**
 * How an image share is drawn in the chat (app/static/js/share-ui.js).
 *
 * A share was a grey box of text over a green box listing every check. It is
 * now a sealed tile with a caption, a one-line verdict and the evidence folded
 * underneath. These test the rules behind that -- when the verdict says
 * "tampered", which copy each check describes, that bad news opens its own
 * evidence, that nothing is built from markup -- not how it looks.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeDocument } from './harness.mjs';

const window = {};
loadBrowserScript(join(STATIC_JS, 'share-ui.js'), { window });
const ui = window.revealxShareUI;

function serverSays({ hmac = true, exact = false, tampered = false } = {}) {
    return {
        integrity: { hmac_match: hmac, exact_tamper_detected: exact },
        ml: {
            tampered,
            label: tampered ? 'Tampered Share' : 'Clean Share',
            confidence: 0.958,
            model_name: 'RandomForest VC share tamper detector',
            heatmap_data_url: 'data:image/png;base64,server',
            explanation: ['server reasoning'],
        },
        processing_ms: 46,
    };
}

function browserSays(tampered) {
    return {
        tampered,
        label: tampered ? 'Tampered Share' : 'Clean Share',
        confidence: 0.9,
        timing_ms: { total: 12 },
        heatmap_data_url: 'data:image/png;base64,local',
        explanation: ['local reasoning'],
    };
}

function walk(node, out = []) {
    out.push(node);
    (node.children || []).forEach((child) => walk(child, out));
    return out;
}

const byClass = (root, name) => walk(root).filter((n) => (n.className || '').split(' ').includes(name));

test('every check passing is "verified intact"', () => {
    const s = ui.verdictSummary(serverSays(), null, null);
    assert.equal(s.tampered, false);
    assert.equal(s.title, 'Share 1 verified intact');
    assert.equal(s.passed, 2);
    assert.equal(s.checks.length, 2);
});

test('one failing check is enough to say "tampered"', () => {
    for (const s of [
        ui.verdictSummary(serverSays({ hmac: false }), null, null),
        ui.verdictSummary(serverSays({ tampered: true }), null, null),
        ui.verdictSummary(serverSays(), browserSays(true), null),
        ui.verdictSummary(serverSays(), null, { matches: false }),
    ]) {
        assert.equal(s.tampered, true);
        assert.equal(s.title, 'Tampering detected in Share 1');
    }
});

test('the exact-tamper flag alone also says "tampered"', () => {
    assert.equal(ui.verdictSummary(serverSays({ exact: true }), null, null).tampered, true);
});

test('with a browser verdict, each ML row says which copy it scored', () => {
    const s = ui.verdictSummary(serverSays(), browserSays(false), { matches: true });
    const labels = s.checks.map((c) => c.label);
    assert.deepEqual(labels, ['HMAC-SHA256', 'Server ML (share as stored)',
        'In-browser ML (bytes you received)', 'End-to-end SHA-256']);
    assert.equal(s.passed, 4);
});

test('server and browser disagreeing is its own failing check', () => {
    const s = ui.verdictSummary(serverSays(), browserSays(true), null);
    const disagreement = s.checks.find((c) => c.label === 'Disagreement');
    assert.ok(disagreement);
    assert.equal(disagreement.ok, false);
    assert.match(disagreement.text, /Treat it as tampered/);
});

test('the heatmap and reasoning come from the bytes the recipient holds when known', () => {
    assert.equal(ui.verdictSummary(serverSays(), null, null).heatmap, 'data:image/png;base64,server');
    const s = ui.verdictSummary(serverSays(), browserSays(false), null);
    assert.equal(s.heatmap, 'data:image/png;base64,local');
    assert.equal(s.heatmapLocal, true);
    assert.deepEqual(s.explanation, ['local reasoning']);
});

test('bad news opens its evidence; good news keeps it folded', () => {
    for (const [summary, open] of [
        [ui.verdictSummary(serverSays(), null, null), false],
        [ui.verdictSummary(serverSays({ hmac: false }), null, null), true],
    ]) {
        const doc = makeDocument();
        const panel = doc.createElement('div');
        ui.drawVerdict(doc, panel, summary);
        const details = walk(panel).find((n) => n.tagName === 'DETAILS');
        assert.equal(details.hasAttribute('open'), open);
        assert.equal(panel.className, `tamper-panel ${open ? 'danger' : 'success'}`);
        assert.equal(doc.innerHTMLWrites, 0);
    }
});

test('the verdict panel lists one row per check, marked ok or bad', () => {
    const doc = makeDocument();
    const panel = doc.createElement('div');
    ui.drawVerdict(doc, panel, ui.verdictSummary(serverSays({ hmac: false }), null, { matches: true }));
    const rows = byClass(panel, 'tamper-row');
    assert.deepEqual(rows.map((r) => r.className), ['tamper-row bad', 'tamper-row ok', 'tamper-row ok']);
    assert.match(panel.textContent, /HMAC-SHA256: MISMATCH/);
    assert.match(panel.textContent, /2 of 3 passed/);
});

test('the sealed tile is built from elements, its decoration hidden from screen readers', () => {
    for (const sent of [true, false]) {
        const doc = makeDocument();
        const card = ui.buildShareCard(doc, { sent });
        assert.equal(doc.innerHTMLWrites, 0);
        assert.equal(card.caption.textContent, sent ? 'Image sent as two shares' : 'Image share');
        // render.js writes who can rebuild it into the status element.
        assert.ok(card.detail.className.split(' ').includes('share-status'));
        for (const name of ['share-grain', 'share-lock']) {
            assert.equal(byClass(card.root, name)[0].getAttribute('aria-hidden'), 'true');
        }
    }
});

test('a rebuilt image replaces the seal and says it is not kept', () => {
    const doc = makeDocument();
    const card = ui.buildShareCard(doc, {});
    ui.showImage(doc, card, 'data:image/png;base64,abc');
    assert.ok(card.root.className.includes('revealed'));
    const img = card.tile.children[0];
    assert.equal(img.tagName, 'IMG');
    assert.equal(img.alt, 'The rebuilt image');
    assert.equal(card.tile.children.length, 1);
    assert.match(card.detail.textContent, /gone when you reload/);
});

test('sizes are written for people', () => {
    assert.equal(ui.formatBytes(512), '512 B');
    assert.equal(ui.formatBytes(15 * 1024), '15 KB');
    assert.equal(ui.formatBytes(1.5 * 1024 * 1024), '1.5 MB');
    assert.equal(ui.formatBytes(NaN), '');
});

test('a data URL is measured without decoding it', () => {
    assert.equal(ui.dataUrlBytes('data:image/jpeg;base64,' + Buffer.from('hello').toString('base64')), 5);
    assert.equal(ui.dataUrlBytes('data:image/jpeg;base64,' + Buffer.from('hell').toString('base64')), 4);
    assert.equal(ui.dataUrlBytes('not a data url'), 0);
});

test('the preview line names the file, the size that will be split, and the bytes', () => {
    assert.equal(ui.previewMeta({ name: 'cat.jpg', width: 1200, height: 900, bytes: 186000 }),
        'cat.jpg · 1200 × 900 · 182 KB');
    assert.equal(ui.previewMeta({ name: 'cat.jpg' }), 'cat.jpg');
});
