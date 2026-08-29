/**
 * Show/hide control for password fields.
 *
 * Applied to every input[type=password] rather than wired field by field, so a
 * field added later gets one without anybody remembering to.
 *
 * Two deliberate behaviours:
 *
 *   * It reverts to hidden on blur. A revealed password left on screen is a
 *     shoulder-surfing problem, and the person has usually stopped looking at
 *     the field by the time they tab away.
 *   * The control is a real <button type="button">. Inside a form, a bare
 *     <button> submits, which would sign the user in halfway through typing.
 */

(function () {
    'use strict';

    const HIDDEN = 'Show password';
    const SHOWN = 'Hide password';

    // Inline so the control does not depend on an icon font loading.
    const EYE = 'M12 5c-5 0-9 4.5-9 7s4 7 9 7 9-4.5 9-7-4-7-9-7zm0 11.5A4.5 4.5 0 1 1 12 7.5a4.5 4.5 0 0 1 0 9zm0-7a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z';
    const EYE_OFF = 'M2.1 3.5 3.5 2.1l18.4 18.4-1.4 1.4-3.3-3.3A10.6 10.6 0 0 1 12 19c-5 0-9-4.5-9-7a9.6 9.6 0 0 1 3.4-4.9L2.1 3.5zM12 7.5a4.5 4.5 0 0 1 4.5 4.5c0 .6-.1 1.1-.3 1.6l-5.8-5.8c.5-.2 1-.3 1.6-.3zm-4.2 2.1 5.6 5.6a4.5 4.5 0 0 1-5.6-5.6zM12 5c5 0 9 4.5 9 7 0 .9-.5 2-1.4 3.1l-1.5-1.5c.5-.6.8-1.2.9-1.6-.5-1.4-3.4-5-7-5-.4 0-.8 0-1.2.1L9.4 5.3C10.2 5.1 11.1 5 12 5z';

    function icon(path) {
        return '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
             + '<path fill="currentColor" d="' + path + '"></path></svg>';
    }

    function attach(input) {
        if (input.dataset.hasReveal === '1') return;
        input.dataset.hasReveal = '1';

        const wrap = document.createElement('div');
        wrap.className = 'password-field';
        // Carry the field's own layout role, so wrapping does not collapse it
        // inside a flex row.
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        const button = document.createElement('button');
        button.type = 'button';           // never submit the surrounding form
        button.className = 'password-reveal';
        button.innerHTML = icon(EYE);
        button.setAttribute('aria-label', HIDDEN);
        button.setAttribute('aria-pressed', 'false');
        button.title = HIDDEN;
        button.tabIndex = -1;             // keep Tab going input -> submit

        function setShown(shown) {
            input.type = shown ? 'text' : 'password';
            button.innerHTML = icon(shown ? EYE_OFF : EYE);
            button.setAttribute('aria-label', shown ? SHOWN : HIDDEN);
            button.setAttribute('aria-pressed', shown ? 'true' : 'false');
            button.title = shown ? SHOWN : HIDDEN;
        }

        button.addEventListener('click', () => {
            const shown = input.type === 'text';
            setShown(!shown);
            // Return focus so typing continues where it left off.
            input.focus();
        });

        // Never leave a password on screen once the person has moved on.
        input.addEventListener('blur', () => {
            // Ignore the blur caused by clicking the control itself.
            setTimeout(() => {
                if (document.activeElement !== input && document.activeElement !== button) {
                    setShown(false);
                }
            }, 0);
        });

        wrap.appendChild(button);
    }

    function attachAll(root) {
        (root || document).querySelectorAll('input[type="password"]').forEach(attach);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => attachAll());
    } else {
        attachAll();
    }

    // Exposed so a field rendered later can be given one.
    window.revealxAttachPasswordToggles = attachAll;
})();
