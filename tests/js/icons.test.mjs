/**
 * Icons for buttons built in script (app/static/js/icons.js).
 *
 * The controls used emoji as icons -- a gear, a key, a moon, a microphone.
 * Emoji are drawn by each platform's own font, so they looked different on
 * every device, and they ignore the theme: a colour emoji cannot follow the
 * text colour into the light theme. The icons are now one inline SVG sprite
 * in index.html, stroked in currentColor; this helper builds the
 * <svg><use> that points into it, without touching innerHTML.
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { join } from 'node:path';

import { STATIC_JS, loadBrowserScript, makeWindow, makeDocument } from './harness.mjs';

const window = makeWindow();
loadBrowserScript(join(STATIC_JS, 'icons.js'), { window });
const { icon, setIcon } = window.revealxIcons;
const SVG = 'http://www.w3.org/2000/svg';

test('an icon is an SVG pointing into the sprite', () => {
    const doc = makeDocument();
    const svg = icon(doc, 'key');
    assert.equal(svg.namespaceURI, SVG);
    assert.equal(svg.tagName, 'svg');
    const use = svg.children[0];
    assert.equal(use.namespaceURI, SVG);
    assert.equal(use.getAttribute('href'), '#i-key');
    assert.equal(doc.innerHTMLWrites, 0);
});

test('it is decoration: hidden from screen readers and never focusable', () => {
    // The button around it carries the name (aria-label); the icon would
    // only add noise, and old Edge put SVGs in the tab order.
    const svg = icon(makeDocument(), 'mic');
    assert.equal(svg.getAttribute('aria-hidden'), 'true');
    assert.equal(svg.getAttribute('focusable'), 'false');
    assert.ok(svg.getAttribute('class').split(' ').includes('icon'));
});

test('an extra class can be added, for the theme icon', () => {
    const svg = icon(makeDocument(), 'moon', 'theme-icon');
    assert.deepEqual(svg.getAttribute('class').split(' '), ['icon', 'theme-icon']);
});

test('setIcon swaps the picture without rebuilding the button', () => {
    const svg = icon(makeDocument(), 'mic');
    setIcon(svg, 'stop');
    assert.equal(svg.children[0].getAttribute('href'), '#i-stop');
});

test('a name with no symbol is refused rather than drawing nothing', () => {
    assert.throws(() => icon(makeDocument(), 'gear '), /unknown icon/);
});
