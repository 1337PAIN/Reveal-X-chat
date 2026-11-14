/**
 * Reveal-X: in-app confirmation and small action menus.
 *
 * window.confirm() blocks the page, cannot be themed, and some screen readers
 * announce it as a browser alert unrelated to the app. askConfirm() asks the
 * same question in the app's own dialog (#confirmDialog in index.html):
 * focus starts on Cancel, the safe choice; Escape and Cancel answer no; and
 * focus returns to whatever opened it.
 *
 * wireMenu() drives a per-message "More actions" button: aria-expanded tracks
 * the menu, focus moves into it when it opens, and Escape or a click outside
 * closes it and returns focus to the button.
 *
 * Text goes in as textContent, never markup. A classic script, like
 * sanitize.js, so tests/js/dialogs.test.mjs loads the shipped file.
 */

(function () {
    let pending = null; // {resolve, cleanup} for the question on screen

    function askConfirm(options) {
        const opts = options || {};
        const dialog = document.getElementById('confirmDialog');
        const title = document.getElementById('confirmDialogTitle');
        const body = document.getElementById('confirmDialogBody');
        const ok = document.getElementById('confirmDialogOk');
        const cancel = document.getElementById('confirmDialogCancel');
        if (!dialog || !ok || !cancel) return Promise.resolve(false);

        // A new question replaces one still on screen; the old one is
        // answered "no" rather than left waiting for ever.
        if (pending) pending.finish(false);

        const opener = document.activeElement;
        title.textContent = opts.title || 'Are you sure?';
        body.textContent = opts.body || '';
        ok.textContent = opts.confirmLabel || 'Confirm';

        return new Promise((resolve) => {
            const onOk = () => finish(true);
            const onCancel = () => finish(false);
            const onKey = (event) => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    finish(false);
                }
            };
            function finish(answer) {
                ok.removeEventListener('click', onOk);
                cancel.removeEventListener('click', onCancel);
                document.removeEventListener('keydown', onKey);
                dialog.classList.add('hidden');
                pending = null;
                if (opener && typeof opener.focus === 'function') opener.focus();
                resolve(answer);
            }
            ok.addEventListener('click', onOk);
            cancel.addEventListener('click', onCancel);
            document.addEventListener('keydown', onKey);
            pending = { finish };
            dialog.classList.remove('hidden');
            cancel.focus();
        });
    }

    function wireMenu(trigger, menu) {
        trigger.setAttribute('aria-expanded', 'false');
        menu.hidden = true;

        const onKey = (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                close(true);
            }
        };
        const onOutside = (event) => {
            if (!menu.contains(event.target) && !trigger.contains(event.target)) close(false);
        };
        function open() {
            menu.hidden = false;
            trigger.setAttribute('aria-expanded', 'true');
            document.addEventListener('keydown', onKey);
            document.addEventListener('click', onOutside);
            const first = menu.querySelector('[role="menuitem"]');
            if (first) first.focus();
        }
        function close(returnFocus) {
            menu.hidden = true;
            trigger.setAttribute('aria-expanded', 'false');
            document.removeEventListener('keydown', onKey);
            document.removeEventListener('click', onOutside);
            if (returnFocus) trigger.focus();
        }
        trigger.addEventListener('click', (event) => {
            event.stopPropagation();
            if (menu.hidden) open(); else close(true);
        });
        return { open, close };
    }

    window.revealxDialogs = { askConfirm, wireMenu };
}());
