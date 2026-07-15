(function () {
    'use strict';

    // Relative tolerance. Browser and NumPy sum in different orders, so a
    // few ulps of drift is expected and harmless; anything larger means a
    // genuine formula mismatch. Note the model consumes float32 (~1e-7
    // relative), so this bar is far stricter than inference requires.
    const REL_TOL = 1e-9;
    const ABS_FLOOR = 1e-9;

    /** Deterministic PRNG so a failure can be reproduced exactly. */
    function mulberry32(seed) {
        return function () {
            seed |= 0; seed = seed + 0x6D2B79F5 | 0;
            let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
            t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
            return ((t ^ t >>> 14) >>> 0) / 4294967296;
        };
    }

    function makeCanvas(w, h) {
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        return c;
    }

    /** Random grayscale share, written as a canvas the server can decode. */
    function randomShare(w, h, seed, mutate) {
        const rand = mulberry32(seed);
        const canvas = makeCanvas(w, h);
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        const img = ctx.createImageData(w, h);
        for (let i = 0, p = 0; i < w * h; i++, p += 4) {
            const v = Math.floor(rand() * 256);
            img.data[p] = v; img.data[p + 1] = v; img.data[p + 2] = v; img.data[p + 3] = 255;
        }
        if (mutate) mutate(img, w, h, rand);
        ctx.putImageData(img, 0, 0);
        return canvas;
    }

    function setPixel(img, w, x, y, r, g, b) {
        const p = (y * w + x) * 4;
        img.data[p] = r; img.data[p + 1] = g; img.data[p + 2] = b; img.data[p + 3] = 255;
    }

    const CASES = [
        {
            name: '128×128 clean random share',
            why: 'The ordinary case — what a real Share 1 looks like.',
            build: () => randomShare(128, 128, 1001),
        },
        {
            name: '128×128 share with a black block',
            why: 'Block-replacement attack. Drives zero_ratio and the entropy drop.',
            build: () => randomShare(128, 128, 1002, (img, w) => {
                for (let y = 20; y < 60; y++) for (let x = 30; x < 70; x++) setPixel(img, w, x, y, 0, 0, 0);
            }),
        },
        {
            name: '128×128 share with a white scratch',
            why: 'Exercises sat_ratio and local structure.',
            build: () => randomShare(128, 128, 1003, (img, w) => {
                for (let i = 0; i < 120; i++) setPixel(img, w, i, (i * 0.7) | 0, 255, 255, 255);
            }),
        },
        {
            name: '96×96 brightness-shifted share',
            why: 'Shifts the mean away from 127.5 without touching the histogram shape much.',
            build: () => randomShare(96, 96, 1004, (img) => {
                for (let p = 0; p < img.data.length; p += 4) {
                    const v = Math.min(255, img.data[p] + 40);
                    img.data[p] = v; img.data[p + 1] = v; img.data[p + 2] = v;
                }
            }),
        },
        {
            name: '37×53 ragged share (partial blocks)',
            why: 'Neither side divides evenly by 16. Pins that partial edge blocks are '
               + 'included in block_mean_std / block_std_mean the way NumPy slicing does.',
            build: () => randomShare(37, 53, 1005),
        },
        {
            name: '1×64 single-column image',
            why: 'width = 1, so corr_h must short-circuit to 0.0 rather than produce NaN.',
            build: () => randomShare(1, 64, 1006),
        },
        {
            name: '64×1 single-row image',
            why: 'height = 1, the corr_v counterpart.',
            build: () => randomShare(64, 1, 1007),
        },
        {
            name: '48×48 constant grey (zero variance)',
            why: 'std = 0 on both neighbour sets — the guard against a 0/0 correlation.',
            build: () => randomShare(48, 48, 1008, (img) => {
                for (let p = 0; p < img.data.length; p += 4) {
                    img.data[p] = 128; img.data[p + 1] = 128; img.data[p + 2] = 128;
                }
            }),
        },
        {
            name: '32×32 all black',
            why: 'zero_ratio = 1, entropy = 0. Degenerate but must not divide by zero.',
            build: () => randomShare(32, 32, 1009, (img) => {
                for (let p = 0; p < img.data.length; p += 4) {
                    img.data[p] = 0; img.data[p + 1] = 0; img.data[p + 2] = 0;
                }
            }),
        },
        {
            name: '64×64 colour image (must be REFUSED)',
            expectDecline: true,
            why: 'R≠G≠B. OpenCV decodes colour PNGs through libpng, which lands ±1 away from '
               + 'the BT.601 result on a fraction of a percent of pixels — so no browser-side '
               + 'formula can guarantee the server’s exact pixels. The detector must therefore '
               + 'decline and defer to the server rather than score almost-right features. '
               + 'Shares are never colour, so nothing real is lost.',
            build: () => {
                const rand = mulberry32(1010);
                const canvas = makeCanvas(64, 64);
                const ctx = canvas.getContext('2d', { willReadFrequently: true });
                const img = ctx.createImageData(64, 64);
                for (let p = 0; p < img.data.length; p += 4) {
                    img.data[p] = Math.floor(rand() * 256);
                    img.data[p + 1] = Math.floor(rand() * 256);
                    img.data[p + 2] = Math.floor(rand() * 256);
                    img.data[p + 3] = 255;
                }
                ctx.putImageData(img, 0, 0);
                return canvas;
            },
        },
    ];

    function relDelta(a, b) {
        const diff = Math.abs(a - b);
        const scale = Math.max(Math.abs(a), Math.abs(b));
        if (scale < ABS_FLOOR) return diff;
        return diff / scale;
    }

    async function runCase(spec) {
        const canvas = spec.build();
        const dataUrl = canvas.toDataURL('image/png');

        const res = await fetch('/api/lab/features', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: dataUrl }),
        });
        const server = await res.json();
        if (!server.ok) throw new Error(server.error || 'server refused the image');

        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const internals = window.revealxTamperInternals;
        const { gray } = internals.toGrayscale(imageData);
        const jsFeatures = internals.extractFeatures(gray, canvas.width, canvas.height);

        const rows = server.feature_names.map((name, i) => {
            const py = server.features[i];
            const js = jsFeatures[i];
            const rel = relDelta(py, js);
            return { name, py, js, rel, pass: rel <= REL_TOL };
        });

        // End-to-end: does the browser's ONNX copy agree with server sklearn?
        let onnxProb = null, probDelta = null;
        const prediction = await window.revealxTamperML.analyse(dataUrl, { heatmap: false });
        if (prediction) {
            onnxProb = prediction.probability_tampered;
            probDelta = Math.abs(onnxProb - server.probability_tampered);
        }

        if (spec.expectDecline) {
            return {
                spec, server, onnxProb, probDelta, rows: [],
                declined: prediction === null,
                skipReason: window.revealxTamperML.lastSkipReason || '',
                shape: `${canvas.width}×${canvas.height}`,
            };
        }

        return { spec, rows, server, onnxProb, probDelta, declined: false,
                 shape: `${canvas.width}×${canvas.height}` };
    }

    function render(result) {
        if (result.spec.expectDecline) {
            const ok = result.declined;
            const el = document.createElement('section');
            el.className = 'case';
            el.innerHTML = `
                <h3>${result.spec.name}</h3>
                <p class="why">${result.spec.why}</p>
                <div class="verdict ${ok ? 'ok' : 'bad'}">
                    ${ok ? 'CORRECTLY REFUSED — deferred to the server'
                         : 'BAD — scored an image whose pixels it cannot match'}
                </div>
                <p class="muted">${ok
                    ? 'Reason given: ' + result.skipReason
                    : 'Expected analyse() to return null so the caller falls back.'}</p>`;
            return { el, allOk: ok };
        }

        const failed = result.rows.filter(r => !r.pass);
        // 1e-4 cannot flip a 0.5 verdict unless the sample was already on the fence.
        const probOk = result.probDelta === null || result.probDelta <= 1e-4;
        const allOk = !failed.length && probOk;

        const el = document.createElement('section');
        el.className = 'case';
        el.innerHTML = `
            <h3>${result.spec.name}</h3>
            <p class="why">${result.spec.why}</p>
            <div class="verdict ${allOk ? 'ok' : 'bad'}">
                ${allOk ? 'PARITY OK' : `MISMATCH (${failed.length} feature${failed.length === 1 ? '' : 's'})`}
            </div>
            <table class="parity">
                <thead><tr><th>Feature</th><th>Python (NumPy/OpenCV)</th>
                <th>Browser (JS)</th><th>Relative &Delta;</th><th></th></tr></thead>
                <tbody>
                    ${result.rows.map(r => `
                        <tr>
                            <td>${r.name}</td>
                            <td>${r.py.toPrecision(12)}</td>
                            <td>${r.js.toPrecision(12)}</td>
                            <td>${r.rel === 0 ? '0' : r.rel.toExponential(2)}</td>
                            <td class="${r.pass ? 'ok' : 'bad'}">${r.pass ? '✓' : '✗'}</td>
                        </tr>`).join('')}
                    <tr>
                        <td><strong>P(tampered)</strong></td>
                        <td>${result.server.probability_tampered.toFixed(6)} <span class="muted">sklearn</span></td>
                        <td>${result.onnxProb === null ? 'n/a' : result.onnxProb.toFixed(6)} <span class="muted">ONNX/wasm</span></td>
                        <td>${result.probDelta === null ? '-' : result.probDelta.toExponential(2)}</td>
                        <td class="${probOk ? 'ok' : 'bad'}">${probOk ? '✓' : '✗'}</td>
                    </tr>
                </tbody>
            </table>`;
        return { el, allOk };
    }

    document.getElementById('runBtn').addEventListener('click', async () => {
        const btn = document.getElementById('runBtn');
        const summary = document.getElementById('summary');
        const results = document.getElementById('results');
        btn.disabled = true;
        results.innerHTML = '';
        summary.textContent = 'Loading ONNX runtime…';

        await window.revealxTamperML.init();
        const engine = window.revealxTamperML.ready
            ? 'onnxruntime-web (wasm)'
            : `UNAVAILABLE — ${window.revealxTamperML.error || 'unknown'}`;

        let passed = 0;
        for (const spec of CASES) {
            summary.textContent = `Running: ${spec.name}…`;
            try {
                const result = await runCase(spec);
                const { el, allOk } = render(result);
                results.appendChild(el);
                if (allOk) passed++;
            } catch (err) {
                const el = document.createElement('section');
                el.className = 'case';
                el.innerHTML = `<h3>${spec.name}</h3>
                    <div class="verdict bad">ERROR — ${err.message}</div>`;
                results.appendChild(el);
            }
        }

        const all = passed === CASES.length;
        summary.innerHTML = `<span class="verdict ${all ? 'ok' : 'bad'}">
            ${passed} / ${CASES.length} cases at full parity</span>
            <div class="muted" style="margin-top:.4rem">
            Engine: ${engine} &middot; tolerance: ${REL_TOL.toExponential(0)} relative
            </div>`;
        btn.disabled = false;
        window.__parityResult = { passed, total: CASES.length, engine };
    });
})();
