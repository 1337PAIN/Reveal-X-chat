/**
 * Reveal-X: the one status chip every message carries.
 *
 * Encryption state used to be a bare padlock icon in the message header (no
 * words), a red "[not encrypted]" badge, and on image shares a red
 * "ONE-TIME ACCESS SHARE" notice -- 3.34:1, failing even AA -- plus a
 * separate expiry line. Red is kept for tamper and error states, where it
 * means something; every message now gets exactly one chip saying, in words,
 * what it is. The glyph is decoration and is marked aria-hidden where drawn.
 *
 * A classic script, like sanitize.js, so tests/js/chips.test.mjs loads the
 * shipped file through harness.mjs.
 */

(function () {
    /**
     * @param {object} message
     * @param {string} message.type         'text' | 'voice' | 'share'
     * @param {boolean} [message.encrypted]  text and voice
     * @param {number} [message.minutesLeft] shares: whole minutes to expiry
     * @returns {{text: string, kind: string, icon: string, title: string}}
     */
    function messageChip(message) {
        const m = message || {};
        if (m.type === 'share') {
            const left = Math.max(0, Math.round(Number(m.minutesLeft) || 0));
            if (left === 0) {
                return { text: 'Expired', kind: 'neutral', icon: '⏳', title: 'This image share has expired.' };
            }
            return {
                text: `Opens once - ${left} min left`,
                kind: 'neutral',
                icon: '⏳',
                title: 'Share 1 can be downloaded once; the share expires after an hour.',
            };
        }
        if (m.encrypted === true) {
            return {
                text: 'E2EE',
                kind: 'secure',
                icon: '🔒',
                title: 'End-to-end encrypted: ECDH P-256, HKDF-SHA256, AES-GCM.',
            };
        }
        return {
            text: 'Not encrypted',
            kind: 'warning',
            icon: '⚠',
            title: 'Sent without end-to-end encryption; the server could read it.',
        };
    }

    window.revealxChips = { messageChip };
}());
