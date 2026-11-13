/**
 * Reveal-X: may this text or voice message be sent, and in what form?
 *
 * Both send paths used to try to encrypt and, on any error, log it and send
 * the message in plaintext. An encryption failure is exactly when the message
 * must not go out, and the fallback made it a silent downgrade -- the server
 * stored the words in the clear and the only trace was a console line. A
 * peer with no published key went straight to plaintext too, labelled only
 * after it had been sent.
 *
 * The rules, in one pure function so they are tested once
 * (tests/js/send-policy.test.mjs) rather than re-derived at each call site:
 *
 *   - A peer whose key changed since it was pinned: blocked, whatever else
 *     is true, until the change is reviewed.
 *   - Both sides have keys: encrypt. Nothing else.
 *   - Otherwise plaintext is possible only after the user has explicitly
 *     agreed to it for this conversation.
 *   - An encryption attempt that fails is an error. It is never retried in
 *     plaintext.
 */

(function () {
    /**
     * @param {object} context
     * @param {boolean} context.e2eeReady   this browser has a usable key pair
     * @param {boolean} context.peerHasKey  the peer has published a key
     * @param {string}  context.pinStatus   from e2ee.js checkPeerKey
     * @param {boolean} context.plaintextConfirmed  consent for this conversation
     * @returns {{action: 'encrypt'|'plaintext'|'confirm-plaintext'|'blocked', reason: string}}
     */
    function decideSend(context) {
        const c = context || {};
        if (c.pinStatus === 'changed') {
            return { action: 'blocked', reason: 'key-changed' };
        }
        if (c.e2eeReady === true && c.peerHasKey === true) {
            return { action: 'encrypt', reason: 'both-keys' };
        }
        const reason = c.e2eeReady === true ? 'peer-has-no-key' : 'e2ee-unavailable';
        if (c.plaintextConfirmed === true) {
            return { action: 'plaintext', reason };
        }
        return { action: 'confirm-plaintext', reason };
    }

    /**
     * What to do with the result of an encryption attempt: `sealed` is what
     * e2ee.js returned, or the error it threw.
     * @returns {{action: 'send'|'error'}}
     */
    function afterEncrypt(sealed) {
        const ok = Boolean(sealed) && !(sealed instanceof Error)
            && typeof sealed.ciphertext === 'string' && sealed.ciphertext !== ''
            && typeof sealed.iv === 'string' && sealed.iv !== '';
        return { action: ok ? 'send' : 'error' };
    }

    window.revealxSendPolicy = { decideSend, afterEncrypt };
}());
