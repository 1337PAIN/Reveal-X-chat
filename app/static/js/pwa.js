/**
 * Install and update handling for the Progressive Web App.
 *
 * chat.js registers the service worker and owns the online/offline indicator.
 * This file owns the two things a user can act on:
 *
 *   1. Installing the app to the home screen.
 *   2. Taking a new version once one is ready.
 *
 * Both are deliberately opt-in. An app that installs itself is a pop-up, and
 * an app that reloads itself mid-message loses the message.
 *
 * The two platforms differ in a way that matters here. Android Chrome fires
 * `beforeinstallprompt` and lets the page call `prompt()`. iOS Safari fires
 * nothing and exposes no API at all: the only route is Share -> Add to Home
 * Screen, which the user has to be told about. Hiding the button on iOS would
 * mean iPhone users never discover that the app installs.
 */

(function () {
    'use strict';

    const isStandalone = () =>
        window.matchMedia('(display-mode: standalone)').matches ||
        window.navigator.standalone === true;   // iOS reports it here instead

    const isIOS = () =>
        /iphone|ipad|ipod/i.test(navigator.userAgent) ||
        // iPadOS 13+ claims to be a Mac; the touch points give it away.
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    const DISMISS_KEY = 'revealx-install-dismissed';

    function dismissed() {
        try {
            return localStorage.getItem(DISMISS_KEY) === '1';
        } catch (err) {
            return false;   // private mode: offer it, rather than never
        }
    }

    function remember() {
        try {
            localStorage.setItem(DISMISS_KEY, '1');
        } catch (err) {
            /* storage blocked: the button simply reappears next visit */
        }
    }

    // ----------------------------------------------------------------- install

    let deferredPrompt = null;
    const installBtn = document.getElementById('installAppBtn');
    const iosHint = document.getElementById('iosInstallHint');
    const iosHintClose = document.getElementById('iosInstallHintClose');

    function showInstallButton() {
        if (installBtn) installBtn.classList.remove('hidden');
    }

    function hideInstallButton() {
        if (installBtn) installBtn.classList.add('hidden');
    }

    window.addEventListener('beforeinstallprompt', (event) => {
        // Chrome shows its own mini-infobar unless this is called.
        event.preventDefault();
        deferredPrompt = event;
        if (!isStandalone() && !dismissed()) showInstallButton();
    });

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        hideInstallButton();
        if (iosHint) iosHint.classList.add('hidden');
        if (window.announceToScreenReader) {
            window.announceToScreenReader('Reveal-X was installed.');
        }
    });

    if (installBtn) {
        installBtn.addEventListener('click', async () => {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                let outcome = 'dismissed';
                try {
                    ({ outcome } = await deferredPrompt.userChoice);
                } catch (err) {
                    /* the prompt was closed without an answer */
                }
                deferredPrompt = null;
                hideInstallButton();
                if (outcome !== 'accepted') remember();
                return;
            }
            // iOS, or a browser that never fired the event: explain the manual
            // route rather than doing nothing on click.
            if (iosHint) iosHint.classList.toggle('hidden');
        });
    }

    if (iosHintClose && iosHint) {
        iosHintClose.addEventListener('click', () => {
            iosHint.classList.add('hidden');
            remember();
            hideInstallButton();
        });
    }

    // iOS never fires beforeinstallprompt, so the button has to be offered on
    // the strength of the platform check alone.
    if (isIOS() && !isStandalone() && !dismissed()) showInstallButton();

    // ------------------------------------------------------------------ update

    const updateBanner = document.getElementById('updateBanner');
    const updateAcceptBtn = document.getElementById('updateAcceptBtn');
    const updateDismissBtn = document.getElementById('updateDismissBtn');
    let waitingWorker = null;

    function offerUpdate(worker) {
        if (!worker || !updateBanner) return;
        waitingWorker = worker;
        updateBanner.classList.remove('hidden');
        if (window.announceToScreenReader) {
            window.announceToScreenReader('A new version of Reveal-X is ready.');
        }
    }

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.ready.then((reg) => {
            // A worker may already be waiting from a previous visit.
            if (reg.waiting && navigator.serviceWorker.controller) {
                offerUpdate(reg.waiting);
            }

            reg.addEventListener('updatefound', () => {
                const installing = reg.installing;
                if (!installing) return;
                installing.addEventListener('statechange', () => {
                    // `controller` tells the first install apart from an update.
                    // Without that check the banner appears on a first visit,
                    // offering to update to the version already running.
                    if (installing.state === 'installed' && navigator.serviceWorker.controller) {
                        offerUpdate(installing);
                    }
                });
            });
        }).catch(() => {
            /* no worker on this origin: nothing to update */
        });
    }

    if (updateAcceptBtn) {
        updateAcceptBtn.addEventListener('click', () => {
            if (!waitingWorker) return;
            updateAcceptBtn.disabled = true;
            updateAcceptBtn.textContent = 'Updating...';
            // chat.js reloads the page on controllerchange, so this is all the
            // click has to do.
            waitingWorker.postMessage({ type: 'SKIP_WAITING' });
        });
    }

    if (updateDismissBtn && updateBanner) {
        updateDismissBtn.addEventListener('click', () => {
            updateBanner.classList.add('hidden');
            // Not remembered: the update is still waiting and should be offered
            // again next load, because running a stale version is the problem
            // this banner exists to solve.
        });
    }

    // Exposed for the console and for tests; nothing in the app reads it.
    window.revealxPWA = { isStandalone, isIOS };
}());
