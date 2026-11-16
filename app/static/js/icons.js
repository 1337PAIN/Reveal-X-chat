/**
 * Reveal-X: icons for buttons built or changed in script.
 *
 * The controls used emoji as icons -- a gear, a key, a moon, a microphone.
 * Emoji come from each platform's own font, so they looked different on
 * every device, and a colour emoji cannot follow the text colour into the
 * light theme. The icons are now one inline SVG sprite in index.html
 * (#iconSprite), stroked in currentColor; markup points into it with
 * <svg class="icon"><use href="#i-name">, and this builds the same thing for
 * script, with createElementNS rather than innerHTML.
 *
 * A classic script, like sanitize.js, so tests/js/icons.test.mjs loads the
 * shipped file through harness.mjs.
 */

(function () {
    const SVG = 'http://www.w3.org/2000/svg';
    // Every symbol in the sprite; tests/test_svg_icons.py keeps the two lists
    // in step.
    const NAMES = new Set(['settings', 'rebuild', 'moon', 'sun', 'camera', 'mic',
        'stop', 'close', 'key', 'react', 'more', 'lock', 'play', 'pause', 'send', 'trash',
        'shield-check', 'shield-alert', 'user-plus', 'user-minus']);

    function check(name) {
        // A typo would otherwise draw an empty square and nobody would notice.
        if (!NAMES.has(name)) throw new Error(`unknown icon: ${name}`);
    }

    /**
     * @param {Document} doc
     * @param {string} name     a symbol in the sprite, without the "i-"
     * @param {string} [extra]  another class, e.g. "theme-icon"
     * @returns {SVGSVGElement}
     */
    function icon(doc, name, extra) {
        check(name);
        const svg = doc.createElementNS(SVG, 'svg');
        svg.setAttribute('class', extra ? `icon ${extra}` : 'icon');
        // Decoration: the button around it carries the name.
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('focusable', 'false');
        const use = doc.createElementNS(SVG, 'use');
        use.setAttribute('href', `#i-${name}`);
        svg.appendChild(use);
        return svg;
    }

    /** Point an existing icon at another symbol (theme, record/stop). */
    function setIcon(svg, name) {
        check(name);
        const use = svg.children[0];
        if (use) use.setAttribute('href', `#i-${name}`);
    }

    window.revealxIcons = { icon, setIcon };
}());
