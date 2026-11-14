/**
 * Reveal-X lab: how results are worded and formatted on /lab.
 *
 * Pure functions, kept out of rx_lab.js (which drives the page) so that
 * tests/js/lab-format.test.mjs can load the shipped file through harness.mjs.
 */

(function () {
    /**
     * Title and caption for the "Enhanced output" card.
     *
     * When enhancement would have lowered SSIM the server reports the plain
     * reconstruction in that slot (metrics.quality_safe_fallback). The card
     * used to keep saying "Denoised and sharpened output" regardless, which
     * described an image the page was not showing.
     */
    function enhancedCaption(metrics) {
        const m = metrics || {};
        if (!m.quality_safe_fallback) {
            return {
                title: 'Enhanced output',
                caption: 'Denoised and sharpened output for quality comparison.',
            };
        }
        let candidate = '?';
        let plain = '?';
        if (m.enhanced_candidate && m.reconstructed) {
            [candidate, plain] = metricPair(m.enhanced_candidate.ssim, m.reconstructed.ssim);
        }
        return {
            title: 'Enhanced output (not applied)',
            caption: `Showing the plain reconstruction: enhancement scored SSIM ${candidate} `
                + `against ${plain} without it, so it would have made the image worse.`,
        };
    }

    /**
     * A metric for display: two decimals, with its unit if it has one.
     *
     * The server sends four decimals and /lab printed them as sent, so the
     * dashboard showed digits no comparison depends on, in columns that did
     * not line up. Display only; the API keeps its precision.
     */
    function metric(value, unit) {
        const suffix = unit ? ` ${unit}` : '';
        if (value === Infinity) return `∞${suffix}`;
        if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) {
            return '-';
        }
        return `${Number(value).toFixed(2)}${suffix}`;
    }

    /**
     * Two values being compared, each with as many decimals as it takes to
     * tell them apart -- two at least, four (what the server sends) at most.
     * Rounded to two, "scored SSIM 0.86 against 0.86 ... so it would have made
     * the image worse" compared two numbers that looked the same.
     */
    function metricPair(a, b) {
        for (let digits = 2; digits <= 4; digits += 1) {
            const pair = [Number(a).toFixed(digits), Number(b).toFixed(digits)];
            if (pair[0] !== pair[1] || digits === 4) return pair;
        }
        return [metric(a), metric(b)];
    }

    window.revealxLabFormat = { enhancedCaption, metric, metricPair };
}());
