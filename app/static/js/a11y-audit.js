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
 *   4. **Decorative page backdrops.** Walking up the DOM stops at body's
 *      background-color -- but this design paints its aurora into a fixed
 *      body::before at z-index -1. Every translucent panel therefore sits on
 *      that, not on --bg-primary, and CSSOM-only auditing over-reports the
 *      contrast of light ink by a wide margin. The header wordmark measured
 *      5.27:1 that way against #1b1f2a, and 2.3:1 against the pixels actually
 *      behind it. Those layers are now rasterised and sampled at the audited
 *      element's own position; `backdropSource` says which route was used.
 *
 * Known limit: backdrop-filter blur is approximated as identity. saturate()
 * and brightness() are applied, but a blur is only a no-op when the backdrop is
 * locally smooth -- true of a gradient field, not of a photo. Rows carrying one
 * are still flagged `blur: true`.
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

    /* ---------------------------------------------------------------------
       Rasterising the page backdrop.

       Only the gradient forms this codebase actually uses are parsed. Anything
       else makes the raster report itself incomplete rather than quietly return
       a wrong number -- an audit that guesses is worse than one that admits it
       cannot see, because the guess is what ends up in the report.
       --------------------------------------------------------------------- */

    /** Split on `sep`, ignoring separators nested inside parentheses. */
    function splitTop(s, sep) {
        const out = [];
        let depth = 0, cur = '';
        for (let i = 0; i < s.length; i++) {
            const ch = s[i];
            if (ch === '(') depth++;
            else if (ch === ')') depth--;
            if (ch === sep && depth === 0) { out.push(cur.trim()); cur = ''; }
            else cur += ch;
        }
        if (cur.trim()) out.push(cur.trim());
        return out;
    }

    function parseGradient(text) {
        const m = text.match(/^(radial|linear)-gradient\(([\s\S]*)\)$/);
        if (!m) return null;
        const args = splitTop(m[2], ',');
        // A leading argument that is not a colour describes the shape or angle.
        const shape = (args.length && !/^rgba?\(/.test(args[0])) ? args.shift() : null;
        const stops = args.map((a) => {
            const c = parse(a);
            if (!c) return null;
            const at = a.match(/([\d.]+)%\s*$/);
            return { c, at: at ? parseFloat(at[1]) / 100 : null };
        });
        if (stops.length < 2 || stops.some((s) => !s)) return null;

        // Unpositioned stops: the ends anchor at 0 and 1, the rest spread evenly
        // between their positioned neighbours (CSS Images 3, 3.4.3).
        if (stops[0].at === null) stops[0].at = 0;
        if (stops[stops.length - 1].at === null) stops[stops.length - 1].at = 1;
        for (let i = 1; i < stops.length - 1; i++) {
            if (stops[i].at !== null) continue;
            let j = i;
            while (stops[j].at === null) j++;
            const step = (stops[j].at - stops[i - 1].at) / (j - i + 1);
            for (let k = i; k < j; k++) stops[k].at = stops[i - 1].at + step * (k - i + 1);
        }

        // A fully transparent stop carries no colour of its own: CSS interpolates
        // gradients premultiplied, so `transparent` fades the *neighbouring* hue
        // out rather than fading it through black. Canvas agrees, but only once
        // the RGB is spelled out -- so copy it in from the nearest visible stop.
        for (let i = 0; i < stops.length; i++) {
            if (stops[i].c[3] !== 0) continue;
            let from = null;
            for (let d = 1; d < stops.length && !from; d++) {
                const before = stops[i - d], after = stops[i + d];
                if (before && before.c[3] > 0) from = before.c;
                else if (after && after.c[3] > 0) from = after.c;
            }
            if (from) stops[i].c = [from[0], from[1], from[2], 0];
        }
        return { kind: m[1], shape, stops };
    }

    function paintGradient(ctx, g, box) {
        const colour = (c) =>
            'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',' + c[3] + ')';
        const addStops = (grad) => g.stops.forEach((s) =>
            grad.addColorStop(Math.min(1, Math.max(0, s.at)), colour(s.c)));

        if (g.kind === 'radial') {
            // e.g. "608px 544px at 18% 18%"
            const s = (g.shape || '').match(/^([\d.]+)px\s+([\d.]+)px(?:\s+at\s+([\d.]+)%\s+([\d.]+)%)?$/);
            if (!s) return false;
            const rx = parseFloat(s[1]), ry = parseFloat(s[2]);
            if (!(rx > 0) || !(ry > 0)) return false;
            const cx = box.x + box.w * (s[3] === undefined ? 50 : parseFloat(s[3])) / 100;
            const cy = box.y + box.h * (s[4] === undefined ? 50 : parseFloat(s[4])) / 100;
            ctx.save();
            // Canvas radial gradients are circular; squash the space to get the
            // ellipse CSS asked for.
            ctx.translate(cx, cy);
            ctx.scale(1, ry / rx);
            ctx.translate(-cx, -cy);
            const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, rx);
            addStops(grad);
            ctx.fillStyle = grad;
            ctx.fillRect(box.x - box.w, box.y - box.h, box.w * 3, box.h * 3);
            ctx.restore();
            return true;
        }

        // Linear: "<angle>deg", or nothing -- which means "to bottom", 180deg.
        let deg = 180;
        if (g.shape) {
            const a = g.shape.match(/^(-?[\d.]+)deg$/);
            if (!a) return false;
            deg = parseFloat(a[1]);
        }
        const rad = (deg - 90) * Math.PI / 180;
        const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
        const len = Math.abs(box.w * Math.cos(rad)) + Math.abs(box.h * Math.sin(rad));
        const grad = ctx.createLinearGradient(
            cx - Math.cos(rad) * len / 2, cy - Math.sin(rad) * len / 2,
            cx + Math.cos(rad) * len / 2, cy + Math.sin(rad) * len / 2);
        addStops(grad);
        ctx.fillStyle = grad;
        ctx.fillRect(box.x, box.y, box.w, box.h);
        return true;
    }

    /**
     * The decorative layers html/body paint behind all content, flattened into a
     * viewport-sized bitmap. Returns null when there are none, and marks
     * `ok: false` when something in them could not be parsed.
     */
    function buildPageBackdrop() {
        const cv = document.createElement('canvas');
        cv.width = Math.max(1, innerWidth);
        cv.height = Math.max(1, innerHeight);
        const ctx = cv.getContext('2d', { willReadFrequently: true });

        let base = [255, 255, 255, 1];
        for (const host of [document.documentElement, document.body]) {
            const bc = parse(getComputedStyle(host).backgroundColor);
            if (bc && bc[3] > 0) base = over(bc, base);
        }
        ctx.fillStyle = 'rgb(' + base.slice(0, 3).map(Math.round).join(',') + ')';
        ctx.fillRect(0, 0, cv.width, cv.height);

        let painted = 0, ok = true;
        for (const host of [document.documentElement, document.body]) {
            for (const pseudo of ['::before', '::after']) {
                const cs = getComputedStyle(host, pseudo);
                if (!cs || cs.content === 'none' || cs.display === 'none') continue;
                if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
                if (!cs.backgroundImage || cs.backgroundImage === 'none') continue;
                if (parseFloat(cs.opacity) === 0) continue;

                const box = {
                    x: parseFloat(cs.left), y: parseFloat(cs.top),
                    w: parseFloat(cs.width), h: parseFloat(cs.height),
                };
                if (![box.x, box.y, box.w, box.h].every(Number.isFinite)) { ok = false; continue; }

                ctx.save();
                // The aurora is mid-animation almost all the time; the computed
                // matrix is where it is *now*, which is what the eye sees.
                const t = (cs.transform || 'none').match(/matrix\(([^)]+)\)/);
                if (t) {
                    const m = t[1].split(',').map(parseFloat);
                    const o = (cs.transformOrigin || '').split(/\s+/).map(parseFloat);
                    const ox = box.x + (Number.isFinite(o[0]) ? o[0] : box.w / 2);
                    const oy = box.y + (Number.isFinite(o[1]) ? o[1] : box.h / 2);
                    ctx.translate(ox, oy);
                    ctx.transform(m[0], m[1], m[2], m[3], m[4], m[5]);
                    ctx.translate(-ox, -oy);
                }
                const layers = splitTop(cs.backgroundImage, ',');
                // CSS paints the first background layer on top, so go backwards.
                for (let i = layers.length - 1; i >= 0; i--) {
                    const g = parseGradient(layers[i]);
                    if (g && paintGradient(ctx, g, box)) painted++;
                    else ok = false;
                }
                ctx.restore();
            }
        }
        return painted ? { ctx, ok } : null;
    }

    let pageBackdrop;   // rebuilt at the start of every audit run

    /** The colour actually painted behind this box, or null. */
    function sampleBackdrop(box) {
        if (!pageBackdrop) return null;
        const x = Math.round(Math.min(innerWidth - 1, Math.max(0, box.left + box.width / 2)));
        const y = Math.round(Math.min(innerHeight - 1, Math.max(0, box.top + box.height / 2)));
        const d = pageBackdrop.ctx.getImageData(x, y, 1, 1).data;
        return [d[0], d[1], d[2], 1];
    }

    /** saturate()/brightness() from a backdrop-filter, applied to a colour. */
    function applyBackdropFilter(c, filter) {
        let out = [c[0], c[1], c[2]];
        const sat = filter.match(/saturate\(([\d.]+)(%?)\)/);
        if (sat) {
            const s = parseFloat(sat[1]) / (sat[2] ? 100 : 1);
            const [r, g, b] = out;
            out = [
                (0.213 + 0.787 * s) * r + (0.715 - 0.715 * s) * g + (0.072 - 0.072 * s) * b,
                (0.213 - 0.213 * s) * r + (0.715 + 0.285 * s) * g + (0.072 - 0.072 * s) * b,
                (0.213 - 0.213 * s) * r + (0.715 - 0.715 * s) * g + (0.072 + 0.928 * s) * b,
            ];
        }
        const bri = filter.match(/brightness\(([\d.]+)(%?)\)/);
        if (bri) {
            const k = parseFloat(bri[1]) / (bri[2] ? 100 : 1);
            out = out.map((v) => v * k);
        }
        return out.map((v) => Math.min(255, Math.max(0, v))).concat(1);
    }

    /** Every background colour this element could be sitting on. */
    function backdrops(startAt, skipSelf) {
        const layers = [];
        let n = skipSelf ? startAt.parentElement : startAt;
        let blur = false;
        let filters = '';
        let reachedPage = false;
        while (n && n.nodeType === 1) {
            // The raster already accounts for html/body and everything they
            // paint, so stop before counting their backgrounds a second time.
            if (pageBackdrop && (n === document.body || n === document.documentElement)) {
                reachedPage = true;
                break;
            }
            const cs = getComputedStyle(n);
            if (cs.backdropFilter && cs.backdropFilter !== 'none') {
                blur = true;
                filters += ' ' + cs.backdropFilter;
            }
            const bc = parse(cs.backgroundColor);
            const clip = cs.backgroundClip || cs.webkitBackgroundClip;
            const stops = clip === 'text' ? [] : parseAll(cs.backgroundImage);
            let opaque = false;
            if (bc && bc[3] > 0) { layers.push({ solid: bc }); if (bc[3] === 1) opaque = true; }
            if (stops.length) { layers.push({ stops }); if (stops.every((s) => s[3] === 1)) opaque = true; }
            if (opaque) break;
            n = n.parentElement;
        }

        let source = 'cssom';
        let bases = [[255, 255, 255, 1]];
        if (reachedPage) {
            const painted = sampleBackdrop(startAt.getBoundingClientRect());
            if (painted) {
                bases = [filters ? applyBackdropFilter(painted, filters) : painted];
                source = pageBackdrop.ok ? 'painted' : 'painted-partial';
            }
        }
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
        return { bases, blur, source };
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
        pageBackdrop = buildPageBackdrop();
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
                backdropSource: bg.source,
                ink: hex(wInk),
                background: hex(wBg),
            });
        });
        return rows;
    };

    /**
     * Convenience: summary across both themes and an open settings panel.
     *
     * Transitions are frozen for the duration. Switching theme animates
     * `background` on every control that has a transition, and getComputedStyle
     * mid-flight returns the interpolated value -- so a sweep run in a
     * background tab, where frames are throttled and the interpolation never
     * advances, reads the *previous* theme's surfaces under the new theme's
     * ink. That produced ten confident AA failures that did not exist.
     */
    window.revealxContrastSweep = async function () {
        const freeze = document.createElement('style');
        freeze.textContent = '*, *::before, *::after { transition: none !important; }';
        document.head.appendChild(freeze);
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
        freeze.remove();
        const all = Object.values(worst);
        return {
            checked: all.length,
            failAA: all.filter((r) => !r.passAA),
            failAAA: all.filter((r) => !r.passAAA),
            lowest: all.slice().sort((a, b) => a.ratio - b.ratio).slice(0, 5),
        };
    };
})();
