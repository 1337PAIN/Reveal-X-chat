/**
 * Reveal-X: what the connection banner says.
 *
 * A dropped socket used to show only as a retry count tucked into the header
 * ("(retrying 3...)"), and a session the server no longer recognised showed
 * nothing until the sign-in dialog appeared. The banner says what is going
 * on in words: "Reconnecting..." while the socket is down, and "Signed out -
 * sign in again" when the server answered auth_required and the session
 * could not be resumed. It disappears once the session is back.
 *
 * A classic script, like sanitize.js, so tests/js/connection.test.mjs loads
 * the shipped file through harness.mjs.
 */

(function () {
    /**
     * @param {{connected: boolean, signedOut: boolean}} status
     * @returns {{visible: boolean, text: string, kind: string}}
     */
    function bannerFor(status) {
        const s = status || {};
        if (s.signedOut) {
            return { visible: true, text: 'Signed out - sign in again', kind: 'warning' };
        }
        if (!s.connected) {
            return { visible: true, text: 'Reconnecting...', kind: 'info' };
        }
        return { visible: false, text: '', kind: 'info' };
    }

    window.revealxConnection = { bannerFor };
}());
