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
    // The prompt gets its own key. Turning down a prompt means "stop asking",
    // not "remove the feature", so dismissing it must not also hide the
    // header button the way DISMISS_KEY does.
    const BANNER_KEY = 'revealx-install-prompt-dismissed';

    function readFlag(key) {
        try {
            return localStorage.getItem(key) === '1';
        } catch (err) {
            return false;   // private mode: offer it, rather than never
        }
    }

    function writeFlag(key) {
        try {
            localStorage.setItem(key, '1');
        } catch (err) {
            /* storage blocked: the offer simply reappears next visit */
        }
    }

    const dismissed = () => readFlag(DISMISS_KEY);
    const remember = () => writeFlag(DISMISS_KEY);
    const promptDismissed = () => readFlag(BANNER_KEY);
    const rememberPrompt = () => writeFlag(BANNER_KEY);

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

    const installBanner = document.getElementById('installBanner');
    const installAcceptBtn = document.getElementById('installBannerAcceptBtn');
    const installDismissBtn = document.getElementById('installBannerDismissBtn');

    // Both banners are fixed to the same corner, so showing two stacks them
    // one on top of the other. The update banner wins: running stale code is
    // a worse problem than not having installed yet.
    function updateBannerShowing() {
        const banner = document.getElementById('updateBanner');
        return Boolean(banner) && !banner.classList.contains('hidden');
    }

    // Long enough that it does not land on top of the page settling, short
    // enough to still be connected to opening the app.
    const OFFER_DELAY_MS = 2500;

    function offerInstall() {
        if (!installBanner) return;
        if (isStandalone() || dismissed() || promptDismissed()) return;
        if (updateBannerShowing()) return;
        installBanner.classList.remove('hidden');
        if (window.announceToScreenReader) {
            window.announceToScreenReader('Reveal-X can be installed on this device.');
        }
    }

    function hideInstallBanner() {
        if (installBanner) installBanner.classList.add('hidden');
    }

    // Shared by the header button and the prompt, so the two cannot drift.
    async function startInstall() {
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
            hideInstallBanner();
            if (outcome !== 'accepted') remember();
            return;
        }
        // iOS, or a browser that never fired the event: explain the manual
        // route rather than doing nothing on click.
        hideInstallBanner();
        if (iosHint) iosHint.classList.toggle('hidden');
    }

    window.addEventListener('beforeinstallprompt', (event) => {
        // Chrome shows its own mini-infobar unless this is called.
        event.preventDefault();
        deferredPrompt = event;
        if (!isStandalone() && !dismissed()) {
            showInstallButton();
            window.setTimeout(offerInstall, OFFER_DELAY_MS);
        }
    });

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        hideInstallButton();
        hideInstallBanner();
        if (iosHint) iosHint.classList.add('hidden');
        if (window.announceToScreenReader) {
            window.announceToScreenReader('Reveal-X was installed.');
        }
    });

    if (installBtn) {
        installBtn.addEventListener('click', startInstall);
    }

    if (installAcceptBtn) {
        installAcceptBtn.addEventListener('click', startInstall);
    }

    if (installDismissBtn) {
        installDismissBtn.addEventListener('click', () => {
            hideInstallBanner();
            // Only the prompt is silenced. The header button stays, so the
            // app is still installable for anyone who changes their mind.
            rememberPrompt();
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
    if (isIOS() && !isStandalone() && !dismissed()) {
        showInstallButton();
        window.setTimeout(offerInstall, OFFER_DELAY_MS);
    }

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
