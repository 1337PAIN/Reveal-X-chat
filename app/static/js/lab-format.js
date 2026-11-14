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
        const candidate = m.enhanced_candidate ? m.enhanced_candidate.ssim : '?';
        const plain = m.reconstructed ? m.reconstructed.ssim : '?';
        return {
            title: 'Enhanced output (not applied)',
            caption: `Showing the plain reconstruction: enhancement scored SSIM ${candidate} `
                + `against ${plain} without it, so it would have made the image worse.`,
        };
    }

    window.revealxLabFormat = { enhancedCaption };
}());
