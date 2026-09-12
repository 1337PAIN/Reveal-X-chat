/**
 * Escaping, sanitising and input validation.
 *
 * Lifted out of chat.js so it can be unit tested. These four functions are the
 * boundary between text somebody else typed and HTML this app builds, which
 * makes them the most security-relevant code in the front end and, until they
 * moved here, the least examinable -- buried at line 206 of a 2,889-line file
 * with no way to exercise them except by loading the page.
 *
 * Loaded before chat.js, so these stay ordinary globals. No behaviour changed
 * in the move.
 */

/**
 * Escape the five characters that let text become markup.
 *
 * `'` is escaped as well as `"`, because an unquoted or single-quoted attribute
 * is a perfectly good injection point, and this value is interpolated into
 * template literals that do not always quote consistently.
 */
function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text || '').replace(/[&<>"']/g, (match) => map[match]);
}

/**
 * Sanitise an HTML string, falling back to full escaping.
 *
 * DOMPurify comes from a CDN. If it is blocked, offline, or still loading, the
 * fallback escapes everything rather than passing the markup through -- the
 * failure mode is text that shows its own tags, not a live injection. A
 * sanitiser that fails open is worse than no sanitiser, because it is trusted.
 *
 * @param {string} html
 * @returns {string} sanitised, or fully escaped when DOMPurify is unavailable
 */
function cleanHTML(html) {
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html);
    }
    return escapeHtml(html);
}

/**
 * Client-side username validation, matching the server's rule.
 *
 * This is a convenience so the user hears about a bad name before a round trip.
 * The server re-validates; nothing here is a security control.
 */
function validateUsername(username) {
    const usernameRe = /^[A-Za-z0-9_.-]{3,30}$/;
    return usernameRe.test(username);
}

/** Call `func` once the caller has stopped calling for `delay` ms. */
function debounce(func, delay = 300) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => func.apply(this, args), delay);
    };
}

// Exposed for the test harness; the app itself uses the globals above.
if (typeof window !== 'undefined') {
    window.revealxSanitize = { escapeHtml, cleanHTML, validateUsername, debounce };
}
