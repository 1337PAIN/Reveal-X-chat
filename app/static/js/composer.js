/**
 * Reveal-X: the composer's character counter.
 *
 * The field's maxlength was 500 while the server and the client both allow
 * 2,000, so long messages were cut off silently at a quarter of the limit.
 * With the limit corrected, the counter appears for the last 200 characters
 * and is announced politely when a threshold is crossed -- 200, 100, 50, 20,
 * 10 left and the limit itself -- not on every keystroke, which would drown
 * out the typing it describes.
 *
 * A classic script, like sanitize.js, so tests/js/composer.test.mjs loads
 * the shipped file through harness.mjs.
 */

(function () {
    const SHOW_FROM = 200;
    const THRESHOLDS = [200, 100, 50, 20, 10, 0];

    function describe(left) {
        if (left <= 0) return 'Character limit reached';
        return `${left} character${left === 1 ? '' : 's'} left`;
    }

    /**
     * @param {number} length     characters typed now
     * @param {number} max        the field's limit
     * @param {number} [previous] the length before this change
     * @returns {{visible: boolean, text: string, level: string, announce: (string|null)}}
     */
    function counterState(length, max, previous) {
        const left = Math.max(max - length, 0);
        const visible = left <= SHOW_FROM;
        let announce = null;
        if (typeof previous === 'number' && length > previous) {
            const before = max - previous;
            const crossed = THRESHOLDS.find((t) => before > t && left <= t);
            if (crossed !== undefined) announce = describe(left);
        }
        return {
            visible,
            text: describe(left),
            level: left <= 0 ? 'full' : (left <= 100 ? 'near' : 'ok'),
            announce,
        };
    }

    window.revealxComposer = { counterState };
}());
