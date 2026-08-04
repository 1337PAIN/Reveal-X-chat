/**
 * WCAG contrast audit (milestone M6).
 *
 * A development tool, not part of the app. No page loads it; it is served so it
 * can be injected on demand:
 *
 *   const s = document.createElement('script');
 *   s.src = '/static/js/a11y-audit.js';
 *   document.head.appendChild(s);
 *   // then
 *   console.table(revealxContrastAudit().filter(r => !r.passAAA));
 *
 * It exists because "the contrast is fine" is not a checkable claim. Three
 * things it does that a naive implementation gets wrong, each of which produced
 * a false result while this codebase was being audited:
 *
 *   1. **Gradients.** getComputedStyle().backgroundColor is transparent on an
 *      element whose fill comes from linear-gradient(), so a naive audit reads
 *      straight through to the page behind it. That made solid buttons look
 *      like white-on-white (1.05:1) when they were actually white on indigo.
 *      Every gradient stop is evaluated here and the worst is reported.
 *
 *   2. **Gradient text.** With background-clip: text and a transparent
 *      -webkit-text-fill-color, the gradient IS the ink and `color` is not
 *      rendered at all. Treating it as a background inverts the measurement.
 *
 *   3. **Alpha stacking.** Translucent surfaces have to be composited down to
 *      the first opaque ancestor before the ratio means anything. This is what
 *      a glassmorphism design makes hard, and where its real failures hide.
 *
 * Known limit: elements using backdrop-filter are flagged `blur: true`. Their
 * true backdrop is whatever pixels sit behind them, which cannot be read from
 * the CSSOM; the composited layer colours are the standard approximation and
 * are what automated tooling uses.
 */

(function () {
    'use strict';

    const parse = (s) => {
        const m = (s || '').match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const p = m[1].split(/[,\s/]+/).filter(Boolean).map(parseFloat);
        return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
    };
    const parseAll = (s) => (!s || s === 'none')
        ? []
        : [...s.matchAll(/rgba?\([^)]+\)/g)].map((m) => parse(m[0])).filter(Boolean);

    const over = (f, b) => {
        const a = f[3];
        return [f[0] * a + b[0] * (1 - a), f[1] * a + b[1] * (1 - a), f[2] * a + b[2] * (1 - a), 1];
    };
    const lum = (c) => {
        const a = [c[0], c[1], c[2]].map((v) => {
            v /= 255;
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
    };
    const ratio = (a, b) => {
        const l1 = lum(a), l2 = lum(b);
        return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    };
    const hex = (c) => '#' + c.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, '0')).join('');

    /** Every background colour this element could be sitting on. */
    function backdrops(startAt, skipSelf) {
        const layers = [];
        let n = skipSelf ? startAt.parentElement : startAt;
        let blur = false;
        while (n && n.nodeType === 1) {
            const cs = getComputedStyle(n);
            if (cs.backdropFilter && cs.backdropFilter !== 'none') blur = true;
            const bc = parse(cs.backgroundColor);
            const clip = cs.backgroundClip || cs.webkitBackgroundClip;
            const stops = clip === 'text' ? [] : parseAll(cs.backgroundImage);
            let opaque = false;
            if (bc && bc[3] > 0) { layers.push({ solid: bc }); if (bc[3] === 1) opaque = true; }
            if (stops.length) { layers.push({ stops }); if (stops.every((s) => s[3] === 1)) opaque = true; }
            if (opaque) break;
            n = n.parentElement;
        }
        let bases = [[255, 255, 255, 1]];
        for (let i = layers.length - 1; i >= 0; i--) {
            const L = layers[i];
            if (L.solid) {
                bases = bases.map((b) => over(L.solid, b));
            } else {
                const next = [];
                bases.forEach((b) => L.stops.forEach((s) => next.push(over(s, b))));
                bases = next.slice(0, 24);   // guard against many-stop gradients
            }
        }
        return { bases, blur };
    }

    function ownsText(el) {
        for (const n of el.childNodes) {
            if (n.nodeType === 3 && n.textContent.trim().length > 1) return true;
        }
        return false;
    }

    /**
     * Is this element painted over by something unrelated -- an open modal, a
     * dimming overlay?
     *
     * Without this the audit reports the whole app chrome sitting behind a
     * login dialog, which nobody can see and which no one should be asked to
     * restyle. Sampling several points rather than just the centre avoids
     * calling an element hidden because a child icon happens to sit under the
     * midpoint.
     */
    function occluded(el) {
        const r = el.getBoundingClientRect();
        if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth) return false;
        const pts = [
            [r.left + r.width * 0.5, r.top + r.height * 0.5],
            [r.left + Math.min(8, r.width * 0.2), r.top + r.height * 0.5],
            [r.right - Math.min(8, r.width * 0.2), r.top + r.height * 0.5],
        ];
        let checked = 0;
        for (const [x, y] of pts) {
            if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
            checked++;
            const top = document.elementFromPoint(x, y);
            // Reachable if we hit the element, a descendant, or an ancestor
            // (an ancestor means the point lies in its own padding, not that
            // something is covering us).
            if (!top || top === el || el.contains(top) || top.contains(el)) return false;
        }
        return checked > 0;
    }

    window.revealxContrastAudit = function () {
        const rows = [];
        document.querySelectorAll('*').forEach((el) => {
            if (!ownsText(el)) return;
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return;
            const box = el.getBoundingClientRect();
            if (box.width < 1 || box.height < 1) return;
            if (occluded(el)) return;

            const size = parseFloat(cs.fontSize);
            const weight = parseInt(cs.fontWeight, 10) || 400;
            // WCAG "large text": >=18pt, or >=14pt bold.
            const large = size >= 24 || (size >= 18.66 && weight >= 700);

            const clip = cs.backgroundClip || cs.webkitBackgroundClip;
            const fill = parse(cs.webkitTextFillColor || '');
            const gradientText = clip === 'text' && fill && fill[3] === 0;

            let inks, bg;
            if (gradientText) {
                inks = parseAll(cs.backgroundImage);
                bg = backdrops(el, true);
            } else {
                const own = parse(cs.color);
                if (!own) return;
                inks = [own];
                bg = backdrops(el, false);
            }
            if (!inks.length) return;

            let worst = Infinity, wInk = null, wBg = null;
            bg.bases.forEach((b) => inks.forEach((ink) => {
                const c = ratio(over(ink, b), b);
                if (c < worst) { worst = c; wInk = over(ink, b); wBg = b; }
            }));
            if (worst === Infinity) return;

            const aa = large ? 3 : 4.5;
            const aaa = large ? 4.5 : 7;
            rows.push({
                selector: el.tagName.toLowerCase()
                    + (el.id ? '#' + el.id : '')
                    + (typeof el.className === 'string' && el.className.trim()
                        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''),
                text: el.textContent.trim().slice(0, 40),
                ratio: +worst.toFixed(2),
                needAA: aa,
                needAAA: aaa,
                passAA: worst >= aa,
                passAAA: worst >= aaa,
                fontPx: size,
                weight,
                large,
                gradientText: !!gradientText,
                blur: bg.blur,
                ink: hex(wInk),
                background: hex(wBg),
            });
        });
        return rows;
    };

    /** Convenience: summary across both themes and an open settings panel. */
    window.revealxContrastSweep = async function () {
        const worst = {};
        const record = (where) => window.revealxContrastAudit().forEach((r) => {
            const k = r.selector + '|' + r.text;
            if (!worst[k] || r.ratio < worst[k].ratio) worst[k] = Object.assign({ where }, r);
        });
        const root = document.documentElement;
        const previous = document.body.getAttribute('data-theme');
        for (const theme of ['dark', 'light']) {
            root.setAttribute('data-theme', theme);
            document.body.setAttribute('data-theme', theme);
            await new Promise((r) => setTimeout(r, 600));
            record('chat-' + theme);
            const open = document.getElementById('settingsBtn');
            if (open) {
                open.click();
                await new Promise((r) => setTimeout(r, 700));
                record('settings-' + theme);
                document.querySelectorAll('#settingsModal .close-btn, #closeSettingsBtn, #closeSettings')
                    .forEach((b) => b.click());
                await new Promise((r) => setTimeout(r, 400));
            }
        }
        if (previous) { root.setAttribute('data-theme', previous); document.body.setAttribute('data-theme', previous); }
        const all = Object.values(worst);
        return {
            checked: all.length,
            failAA: all.filter((r) => !r.passAA),
            failAAA: all.filter((r) => !r.passAAA),
            lowest: all.slice().sort((a, b) => a.ratio - b.ratio).slice(0, 5),
        };
    };
})();
