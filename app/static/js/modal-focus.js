/**
 * Reveal-X: keep the keyboard inside the dialog that is open.
 *
 * Every dialog says aria-modal="true", but that only tells a screen reader.
 * The keyboard was not told: Shift+Tab from the sign-in dialog's username box
 * went to "Rebuild an image" in the chat behind it. Now, when focus lands
 * outside the topmost open dialog, it is sent back in -- to the dialog's last
 * control when the user was tabbing backwards, its first otherwise -- so Tab
 * and Shift+Tab cycle within the dialog.
 *
 * Why not `inert` on the rest of the page: the dialogs open and close in many
 * places, and the only way to follow all of them is a MutationObserver, which
 * runs after the code that closed a dialog has already called
 * trigger.focus() -- on an element still inert, so focus was lost. Checking
 * at the moment focus moves has no such gap.
 *
 * A classic script, like sanitize.js, so tests/js/modal-focus.test.mjs loads
 * the shipped file through harness.mjs.
 */

(function () {
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
        + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    /**
     * @param {boolean[]} open whether each aria-modal dialog is showing, in
     *        document order
     * @returns {number} the index of the one on top, or -1
     */
    function topDialog(open) {
        // Later in the document is on top: the confirm and TOTP dialogs come
        // after the dialogs they are opened from.
        return open.lastIndexOf(true);
    }

    /**
     * Where focus should go when it moves.
     * @param {{dialogOpen: boolean, insideDialog: boolean, insideLive: boolean,
     *          backwards: boolean}} move
     * @returns {'first'|'last'|null} null to leave it where it is
     */
    function redirect(move) {
        if (!move.dialogOpen || move.insideDialog || move.insideLive) return null;
        return move.backwards ? 'last' : 'first';
    }

    function isShown(el) {
        if (el.hidden) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function watch() {
        let backwards = false;
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Tab') backwards = event.shiftKey;
        }, true);
        document.addEventListener('mousedown', () => { backwards = false; }, true);

        document.addEventListener('focusin', (event) => {
            const dialogs = Array.from(document.querySelectorAll('[aria-modal="true"]'));
            const top = dialogs[topDialog(dialogs.map(isShown))];
            const target = event.target;
            const where = redirect({
                dialogOpen: Boolean(top),
                insideDialog: Boolean(top && top.contains(target)),
                // The connection banner and toasts announce; they never hold
                // focus, but nothing is gained by fighting one that does.
                insideLive: Boolean(target.closest && target.closest('[aria-live], [role="status"], [role="alert"]')),
                backwards,
            });
            if (!where) return;
            // getClientRects is empty for anything not rendered, including a
            // control whose section is hidden (the Firebase sign-in when it
            // is not configured); focusing one of those silently does nothing.
            const inside = Array.from(top.querySelectorAll(FOCUSABLE))
                .filter((el) => el.getClientRects().length > 0);
            const goal = where === 'last' ? inside[inside.length - 1] : inside[0];
            if (goal) goal.focus();
            else {
                // A dialog with nothing to focus still takes focus itself.
                if (!top.hasAttribute('tabindex')) top.setAttribute('tabindex', '-1');
                top.focus();
            }
        });
    }

    window.revealxModalFocus = { topDialog, redirect };

    if (window.document && typeof document.addEventListener === 'function') watch();
}());
