/**
 * Reveal-X: what to say when the microphone cannot be opened.
 *
 * Every getUserMedia failure used to read "Microphone access was denied." On a
 * PC with no microphone the browser refuses at once without asking, so the
 * user was told "denied" with no prompt anywhere and nothing they could
 * allow. The causes need different fixes, so they get different words:
 *
 *   - no device:         plug one in (no prompt can appear without one)
 *   - blocked for site:  the browser remembered a "Block"; it will not ask
 *                        again until that is changed in the site settings
 *   - blocked by system: Windows or macOS privacy settings refuse the browser
 *   - busy or failing:   another app has it, or the driver failed
 *   - insecure page:     browsers only allow the microphone over HTTPS
 *
 * A classic script, like send-policy.js, so tests/js/mic-errors.test.mjs
 * loads the shipped file through harness.mjs.
 */

(function () {
    /**
     * @param {unknown} error what getUserMedia rejected with
     * @returns {string} one sentence the user can act on
     */
    function micErrorMessage(error) {
        const name = error && typeof error === 'object' ? String(error.name || '') : '';
        const detail = error && typeof error === 'object' ? String(error.message || '') : '';

        if (name === 'NotFoundError' || name === 'DevicesNotFoundError' || name === 'OverconstrainedError') {
            return 'No microphone was found. Connect a microphone or headset, '
                + 'or record from a phone or laptop that has one.';
        }
        if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
            // Chrome's wording when the operating system, not the site
            // setting, refused it.
            if (/system/i.test(detail)) {
                return 'The microphone is blocked by the system. Allow your browser in '
                    + 'Windows Settings > Privacy & security > Microphone (on a Mac: System '
                    + 'Settings > Privacy & Security > Microphone), then try again.';
            }
            return 'The microphone is blocked for this site, so the browser will not ask. '
                + 'Click the icon left of the address bar, set Microphone to Allow, '
                + 'then try again.';
        }
        if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') {
            return 'The microphone could not be started. Another app may be using it; '
                + 'close it and try again.';
        }
        if (name === 'SecurityError') {
            return 'Browsers only allow the microphone on a secure (HTTPS) page. '
                + 'Open this site with https://.';
        }
        return 'The microphone could not be opened. Check that one is connected and '
            + 'allowed for this site, then try again.';
    }

    window.revealxMicErrors = { micErrorMessage };
}());
