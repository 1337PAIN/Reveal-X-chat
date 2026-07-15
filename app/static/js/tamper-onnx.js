/**
 * In-browser tamper detection (milestone M2).
 *
 * The RandomForest is trained in Python but exported to ONNX and executed here
 * with onnxruntime-web, so a share can be checked without ever being uploaded.
 * That is the whole point: the server holds Share 1 already, but the *recipient*
 * can now independently verify what they were given rather than asking the same
 * server that stored it whether it is intact.
 *
 * The delicate part is not the inference -- it is the feature extraction. The
 * model was fitted on 13 statistics computed by NumPy/OpenCV, so these
 * functions have to reproduce those to float32 precision or the tree splits are
 * meaningless. Specific things that are easy to get wrong and are deliberate
 * here:
 *
 *   - std/var are POPULATION (divide by n), matching numpy's default ddof=0.
 *   - The Laplacian is OpenCV's ksize=1 kernel [[0,1,0],[1,-4,1],[0,1,0]] with
 *     BORDER_REFLECT_101, not the ksize=3 Sobel form and not zero padding.
 *   - Block statistics include the ragged partial blocks at the right/bottom
 *     edges, because Python's range(0, h, 16) slicing does.
 *   - unique_ratio divides by 256, not by the pixel count.
 *
 * tests/test_onnx_parity.py pins all of this against the Python implementation.
 */

(function () {
    'use strict';

    const ORT_BASE = '/static/vendor/onnxruntime/';
    const ORT_SCRIPT = ORT_BASE + 'ort.wasm.min.js';
    const MODEL_URL = '/static/models/tamper_detector.onnx';
    const META_URL = '/static/models/tamper_detector.meta.json';

    const FEATURE_NAMES = [
        'mean', 'std', 'entropy', 'hist_chi2', 'hist_l1_uniform',
        'corr_h', 'corr_v', 'laplacian_var', 'block_mean_std',
        'block_std_mean', 'unique_ratio', 'zero_ratio', 'sat_ratio',
    ];

    const BLOCK = 16;
    const HEATMAP_GRID = 16;

    /* ---------------------------------------------------------------- maths */

    /**
     * OpenCV's BORDER_REFLECT_101: gfedcb|abcdefgh|gfedcba (the edge pixel is
     * not repeated). Only correct for a one-pixel border, which is all the
     * 3x3 Laplacian needs.
     */
    function reflect101(i, n) {
        if (n <= 1) return 0;
        if (i < 0) return -i;
        if (i >= n) return 2 * n - i - 2;
        return i;
    }

    /** Population mean of a numeric array. */
    function meanOf(values) {
        if (!values.length) return 0;
        let sum = 0;
        for (let i = 0; i < values.length; i++) sum += values[i];
        return sum / values.length;
    }

    /** Population standard deviation (numpy default, ddof=0). */
    function stdOf(values) {
        if (!values.length) return 0;
        const mu = meanOf(values);
        let acc = 0;
        for (let i = 0; i < values.length; i++) {
            const d = values[i] - mu;
            acc += d * d;
        }
        return Math.sqrt(acc / values.length);
    }

    /**
     * Pearson correlation over paired samples, mirroring
     * TamperDetector._safe_corr: a zero-variance side yields 0.0 rather than
     * NaN, because a constant run is not evidence of correlation either way.
     */
    function pearson(a, b) {
        const n = a.length;
        if (n < 2) return 0;
        const ma = meanOf(a);
        const mb = meanOf(b);
        let sab = 0, saa = 0, sbb = 0;
        for (let i = 0; i < n; i++) {
            const da = a[i] - ma;
            const db = b[i] - mb;
            sab += da * db;
            saa += da * da;
            sbb += db * db;
        }
        if (saa === 0 || sbb === 0) return 0;
        return sab / Math.sqrt(saa * sbb);
    }

    /* ----------------------------------------------------------- grayscale */

    /**
     * Extract the grey channel, and report whether doing so was lossless.
     *
     * VC shares are written by cv2.imwrite from a 2-D array, so every pixel has
     * R === G === B and taking one channel reproduces what
     * cv2.imdecode(..., IMREAD_GRAYSCALE) sees, exactly. tests/lab parity
     * confirms this on nine different shapes.
     *
     * A genuinely coloured image is a different matter. OpenCV's PNG decoder
     * does not route colour through cvtColor -- it goes via libpng and lands
     * +/-1 away from the BT.601 result on a fraction of a percent of pixels.
     * There is therefore no formula this function can apply that is guaranteed
     * to reproduce the server's pixels, and feeding the model features derived
     * from *nearly* the right pixels is worse than not answering: the caller
     * cannot tell that the verdict was computed on different data.
     *
     * So we report `exact: false` and let the caller fall back to the server.
     * This costs nothing in practice, because the detector only ever runs on
     * shares, and shares are always single-channel.
     */
    function toGrayscale(imageData) {
        const { data, width, height } = imageData;
        const gray = new Uint8Array(width * height);
        let exact = true;
        for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
            const r = data[p], g = data[p + 1], b = data[p + 2];
            if (r !== g || g !== b) {
                exact = false;
                gray[i] = (r * 4899 + g * 9617 + b * 1868 + 8192) >> 14;
            } else {
                gray[i] = r;
            }
        }
        return { gray, exact };
    }

    /* ------------------------------------------------------------ features */

    function extractFeatures(gray, width, height) {
        const n = gray.length;
        const total = Math.max(n, 1);

        const hist = new Float64Array(256);
        for (let i = 0; i < n; i++) hist[gray[i]]++;

        // uint8 values sum exactly in float64 (well under 2^53), so mean is exact.
        let sum = 0;
        for (let i = 0; i < n; i++) sum += gray[i];
        const mean = sum / total;

        let sqAcc = 0;
        for (let i = 0; i < n; i++) {
            const d = gray[i] - mean;
            sqAcc += d * d;
        }
        const std = Math.sqrt(sqAcc / total);

        let entropy = 0;
        let uniqueCount = 0;
        const uniform = 1 / 256;
        let l1 = 0;
        const expected = total / 256;
        let chi2 = 0;
        for (let k = 0; k < 256; k++) {
            const count = hist[k];
            const p = count / total;
            if (p > 0) {
                entropy -= p * Math.log2(p);
                uniqueCount++;
            }
            l1 += Math.abs(p - uniform);
            const d = count - expected;
            chi2 += (d * d) / (expected + 1e-9);
        }
        chi2 /= 256;

        // Horizontal neighbours: image[:, :-1] against image[:, 1:].
        let corrH = 0;
        if (width > 1) {
            const count = height * (width - 1);
            const a = new Float64Array(count);
            const b = new Float64Array(count);
            let j = 0;
            for (let y = 0; y < height; y++) {
                const row = y * width;
                for (let x = 0; x < width - 1; x++) {
                    a[j] = gray[row + x];
                    b[j] = gray[row + x + 1];
                    j++;
                }
            }
            corrH = pearson(a, b);
        }

        // Vertical neighbours: image[:-1, :] against image[1:, :].
        let corrV = 0;
        if (height > 1) {
            const count = (height - 1) * width;
            const a = new Float64Array(count);
            const b = new Float64Array(count);
            let j = 0;
            for (let y = 0; y < height - 1; y++) {
                const row = y * width;
                const next = row + width;
                for (let x = 0; x < width; x++) {
                    a[j] = gray[row + x];
                    b[j] = gray[next + x];
                    j++;
                }
            }
            corrV = pearson(a, b);
        }

        // cv2.Laplacian(image, CV_64F) -- ksize=1, reflect-101 border.
        const lap = new Float64Array(n);
        for (let y = 0; y < height; y++) {
            const up = reflect101(y - 1, height) * width;
            const dn = reflect101(y + 1, height) * width;
            const row = y * width;
            for (let x = 0; x < width; x++) {
                const lf = reflect101(x - 1, width);
                const rt = reflect101(x + 1, width);
                lap[row + x] = gray[up + x] + gray[dn + x]
                    + gray[row + lf] + gray[row + rt]
                    - 4 * gray[row + x];
            }
        }
        const lapMean = meanOf(lap);
        let lapAcc = 0;
        for (let i = 0; i < n; i++) {
            const d = lap[i] - lapMean;
            lapAcc += d * d;
        }
        const lapVar = n ? lapAcc / n : 0;

        // Partial edge blocks are included, matching Python's slice semantics.
        const blockMeans = [];
        const blockStds = [];
        for (let y = 0; y < height; y += BLOCK) {
            for (let x = 0; x < width; x += BLOCK) {
                const yEnd = Math.min(y + BLOCK, height);
                const xEnd = Math.min(x + BLOCK, width);
                const size = (yEnd - y) * (xEnd - x);
                if (!size) continue;
                let bs = 0;
                for (let yy = y; yy < yEnd; yy++) {
                    const row = yy * width;
                    for (let xx = x; xx < xEnd; xx++) bs += gray[row + xx];
                }
                const bMean = bs / size;
                let bAcc = 0;
                for (let yy = y; yy < yEnd; yy++) {
                    const row = yy * width;
                    for (let xx = x; xx < xEnd; xx++) {
                        const d = gray[row + xx] - bMean;
                        bAcc += d * d;
                    }
                }
                blockMeans.push(bMean);
                blockStds.push(Math.sqrt(bAcc / size));
            }
        }

        return new Float64Array([
            mean,
            std,
            entropy,
            chi2,
            l1,
            corrH,
            corrV,
            lapVar,
            blockMeans.length ? stdOf(blockMeans) : 0,
            blockStds.length ? meanOf(blockStds) : 0,
            uniqueCount / 256,
            hist[0] / total,
            hist[255] / total,
        ]);
    }

    /* ------------------------------------------------------------- heatmap */

    /** OpenCV getGaussianKernel(n, sigma), normalised. */
    function gaussianKernel(size, sigma) {
        const kernel = new Float64Array(size);
        const scale = -0.5 / (sigma * sigma);
        const centre = (size - 1) * 0.5;
        let sum = 0;
        for (let i = 0; i < size; i++) {
            const x = i - centre;
            const v = Math.exp(scale * x * x);
            kernel[i] = v;
            sum += v;
        }
        for (let i = 0; i < size; i++) kernel[i] /= sum;
        return kernel;
    }

    /** Separable Gaussian blur with reflect-101 edges, as cv2.GaussianBlur does. */
    function gaussianBlur(src, width, height, sigma) {
        // cv2 derives ksize from sigma for 8-bit input: round(sigma*3*2 + 1) | 1.
        const size = (Math.round(sigma * 3 * 2 + 1) | 1);
        const kernel = gaussianKernel(size, sigma);
        const half = (size - 1) >> 1;
        const tmp = new Float64Array(width * height);
        const out = new Float64Array(width * height);

        for (let y = 0; y < height; y++) {
            const row = y * width;
            for (let x = 0; x < width; x++) {
                let acc = 0;
                for (let k = 0; k < size; k++) {
                    acc += kernel[k] * src[row + reflectN(x + k - half, width)];
                }
                tmp[row + x] = acc;
            }
        }
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                let acc = 0;
                for (let k = 0; k < size; k++) {
                    acc += kernel[k] * tmp[reflectN(y + k - half, height) * width + x];
                }
                out[y * width + x] = acc;
            }
        }
        return out;
    }

    /** Reflect-101 for an arbitrary out-of-range index, not just one pixel. */
    function reflectN(i, n) {
        if (n <= 1) return 0;
        const period = 2 * n - 2;
        let j = ((i % period) + period) % period;
        return j >= n ? period - j : j;
    }

    /**
     * Suspicious-region map, mirroring TamperDetector.generate_heatmap.
     * Low local entropy, an off-centre local mean, a local spread far from the
     * global one, or a glut of pure black/white pixels all read as suspicious.
     */
    function buildHeatmap(gray, width, height, globalStd) {
        const heat = new Float64Array(width * height);
        const gStd = Math.max(globalStd, 1);
        const patchHist = new Float64Array(256);

        for (let y = 0; y < height; y += HEATMAP_GRID) {
            for (let x = 0; x < width; x += HEATMAP_GRID) {
                const yEnd = Math.min(y + HEATMAP_GRID, height);
                const xEnd = Math.min(x + HEATMAP_GRID, width);
                const size = (yEnd - y) * (xEnd - x);
                if (size < 16) continue;

                patchHist.fill(0);
                let sum = 0;
                for (let yy = y; yy < yEnd; yy++) {
                    const row = yy * width;
                    for (let xx = x; xx < xEnd; xx++) {
                        const v = gray[row + xx];
                        patchHist[v]++;
                        sum += v;
                    }
                }
                const localMean = sum / size;
                let acc = 0;
                for (let yy = y; yy < yEnd; yy++) {
                    const row = yy * width;
                    for (let xx = x; xx < xEnd; xx++) {
                        const d = gray[row + xx] - localMean;
                        acc += d * d;
                    }
                }
                const localStd = Math.sqrt(acc / size);

                let entropy = 0;
                for (let k = 0; k < 256; k++) {
                    if (patchHist[k] > 0) {
                        const p = patchHist[k] / size;
                        entropy -= p * Math.log2(p);
                    }
                }

                let score = 0;
                score += Math.max(0, (7.4 - entropy) / 2);
                score += Math.min(1, Math.abs(localMean - 127.5) / 80);
                score += Math.min(1, Math.abs(localStd - gStd) / 60);
                if (patchHist[0] / size > 0.05 || patchHist[255] / size > 0.05) score += 0.6;
                score = Math.min(score, 2.5);

                for (let yy = y; yy < yEnd; yy++) {
                    const row = yy * width;
                    for (let xx = x; xx < xEnd; xx++) heat[row + xx] = score;
                }
            }
        }

        let max = 0;
        for (let i = 0; i < heat.length; i++) if (heat[i] > max) max = heat[i];
        const scaled = new Uint8Array(heat.length);
        if (max > 0) {
            for (let i = 0; i < heat.length; i++) {
                scaled[i] = Math.round((heat[i] / max) * 255) | 0;
            }
        }

        const blurred = gaussianBlur(scaled, width, height, 3);
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        const out = ctx.createImageData(width, height);
        for (let i = 0, p = 0; i < blurred.length; i++, p += 4) {
            const v = Math.max(0, Math.min(255, Math.round(blurred[i])));
            out.data[p] = v;
            out.data[p + 1] = v;
            out.data[p + 2] = v;
            out.data[p + 3] = 255;
        }
        ctx.putImageData(out, 0, 0);
        return canvas.toDataURL('image/png');
    }

    /* --------------------------------------------------------- explanation */

    /** Word-for-word the same reasons the server gives, so the two agree. */
    function explain(named, probability) {
        const reasons = [];
        if (named.entropy < 7.6) {
            reasons.push('Entropy is lower than a valid random VC share, suggesting inserted structure or edited pixels.');
        }
        if (Math.abs(named.mean - 127.5) > 8) {
            reasons.push('Pixel mean is shifted away from the expected random-share centre.');
        }
        if (named.hist_l1_uniform > 0.25) {
            reasons.push('Histogram distribution deviates from uniform random noise.');
        }
        if (Math.abs(named.corr_h) > 0.05 || Math.abs(named.corr_v) > 0.05) {
            reasons.push('Neighbouring pixels show unusual correlation for a random share.');
        }
        if (named.zero_ratio > 0.02 || named.sat_ratio > 0.02) {
            reasons.push('Large numbers of pure black/white pixels indicate block replacement or clipping.');
        }
        if (!reasons.length) {
            reasons.push('Statistical pattern is consistent with a clean random-looking VC share.');
        }
        reasons.push(`Model probability of tampering: ${(probability * 100).toFixed(1)}%.`);
        return reasons.slice(0, 5);
    }

    /* -------------------------------------------------------------- runner */

    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[src="${src}"]`);
            if (existing) {
                if (existing.dataset.loaded === '1') return resolve();
                existing.addEventListener('load', () => resolve());
                existing.addEventListener('error', () => reject(new Error('ORT script failed')));
                return;
            }
            const el = document.createElement('script');
            el.src = src;
            el.async = true;
            el.addEventListener('load', () => { el.dataset.loaded = '1'; resolve(); });
            el.addEventListener('error', () => reject(new Error('ORT script failed')));
            document.head.appendChild(el);
        });
    }

    class BrowserTamperDetector {
        constructor() {
            this.session = null;
            this.meta = null;
            this.inputName = 'features';
            this.status = 'idle';
            this.error = null;
            this._initPromise = null;
        }

        get ready() {
            return this.session !== null;
        }

        /**
         * Load the runtime and model. Safe to call repeatedly; concurrent
         * callers share one in-flight promise so a busy chat cannot start six
         * downloads of an 11 MB wasm binary.
         */
        init() {
            if (this._initPromise) return this._initPromise;
            this._initPromise = this._init().catch((err) => {
                this.status = 'unavailable';
                this.error = err && err.message ? err.message : String(err);
                // Deliberately not rethrown: callers fall back to the server
                // path, and a browser without wasm should degrade, not break.
                return null;
            });
            return this._initPromise;
        }

        async _init() {
            this.status = 'loading';
            await loadScript(ORT_SCRIPT);
            if (!window.ort) throw new Error('onnxruntime-web did not register');

            window.ort.env.wasm.wasmPaths = ORT_BASE;
            // Single-threaded on purpose: multi-threaded wasm needs
            // SharedArrayBuffer, which needs COOP/COEP headers that would break
            // the rest of the app. This model runs in well under a millisecond.
            window.ort.env.wasm.numThreads = 1;
            window.ort.env.wasm.proxy = false;
            window.ort.env.logLevel = 'error';

            try {
                const res = await fetch(META_URL, { cache: 'force-cache' });
                if (res.ok) this.meta = await res.json();
            } catch (_) {
                this.meta = null;  // cosmetic only; inference does not need it
            }

            this.session = await window.ort.InferenceSession.create(MODEL_URL, {
                executionProviders: ['wasm'],
                graphOptimizationLevel: 'all',
            });
            this.inputName = this.session.inputNames[0] || 'features';
            this.status = 'ready';
            return this.session;
        }

        /** Decode any drawable source into grayscale pixels. */
        async _toGray(source) {
            let bitmap = source;
            if (typeof source === 'string') {
                bitmap = await new Promise((resolve, reject) => {
                    const img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.onload = () => resolve(img);
                    img.onerror = () => reject(new Error('Could not load share image'));
                    img.src = source;
                });
            }
            const width = bitmap.naturalWidth || bitmap.width;
            const height = bitmap.naturalHeight || bitmap.height;
            if (!width || !height) throw new Error('Share image has no dimensions');

            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            // willReadFrequently keeps Chrome on a software path, which avoids
            // GPU readback rounding differences on some drivers.
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            ctx.drawImage(bitmap, 0, 0);
            const imageData = ctx.getImageData(0, 0, width, height);
            const { gray, exact } = toGrayscale(imageData);
            return { gray, width, height, exact };
        }

        /**
         * Classify an image entirely in the browser.
         * Returns null when the runtime is unavailable, so callers can fall
         * back to the server endpoint rather than showing nothing.
         */
        async analyse(source, options) {
            const opts = options || {};
            await this.init();
            if (!this.session) return null;

            const t0 = performance.now();
            const { gray, width, height, exact } = await this._toGray(source);
            if (!exact && !opts.allowApproximate) {
                // Not an error -- a refusal. The server can still answer, and
                // its answer will be computed on the pixels the model expects.
                this.lastSkipReason = 'colour source: grayscale conversion would not match the server';
                return null;
            }
            const features = extractFeatures(gray, width, height);
            const t1 = performance.now();

            const tensor = new window.ort.Tensor('float32', Float32Array.from(features), [1, features.length]);
            const output = await this.session.run({ [this.inputName]: tensor });
            const probs = output.probabilities || output[this.session.outputNames[1]];
            const probabilityTampered = Number(probs.data[1]);
            const t2 = performance.now();

            const named = {};
            FEATURE_NAMES.forEach((name, i) => { named[name] = features[i]; });

            let heatmap = '';
            if (opts.heatmap !== false) {
                heatmap = buildHeatmap(gray, width, height, features[1]);
            }
            const t3 = performance.now();

            const tampered = probabilityTampered >= 0.5;
            return {
                label: tampered ? 'Tampered Share' : 'Clean Share',
                tampered,
                confidence: Math.max(probabilityTampered, 1 - probabilityTampered),
                probability_tampered: probabilityTampered,
                features: named,
                model_name: (this.meta && this.meta.model_name
                    ? this.meta.model_name
                    : 'RandomForest VC share tamper detector') + ' (ONNX, in-browser)',
                heatmap_data_url: heatmap,
                explanation: explain(named, probabilityTampered),
                engine: 'onnx-web',
                accuracy: this.meta ? this.meta.accuracy : null,
                timing_ms: {
                    features: +(t1 - t0).toFixed(2),
                    inference: +(t2 - t1).toFixed(2),
                    heatmap: +(t3 - t2).toFixed(2),
                    total: +(t3 - t0).toFixed(2),
                },
            };
        }
    }

    window.revealxTamperML = new BrowserTamperDetector();
    // Exposed for tests/test_onnx_parity.py, which drives these from a headless
    // browser and compares against the Python implementation.
    window.revealxTamperInternals = { extractFeatures, toGrayscale, FEATURE_NAMES };
})();
