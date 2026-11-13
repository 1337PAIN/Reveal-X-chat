// Offline state, screen-reader announcements and the escaping fallback.
// Split out of chat.js; main.js loads the modules in order.

import { late } from './late.js';
import {
    reconstructionPanel,
    state,
} from './core.js';

// ==========================================
// 1. Service Worker & Offline Management
// ==========================================

if ('serviceWorker' in navigator) {
    // A page already open keeps running the JavaScript it loaded with, however
    // fresh the files on the server are. When a new worker takes over, reload
    // once so the tab is actually running the version it is being served --
    // otherwise the only way out is a hard refresh by hand.
    //
    // Only when a controller was already in place: the first registration on a
    // fresh visit also fires this, and reloading there would be a pointless
    // flash for no benefit.
    if (navigator.serviceWorker.controller) {
        let reloading = false;
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            if (reloading) return;          // guard against a reload loop
            reloading = true;
            window.location.reload();
        });
    }

    window.addEventListener('load', () => {
        // Registered from the root so its scope covers the whole origin.
        navigator.serviceWorker.register('/sw.js', { scope: '/' })
            .then((reg) => {
                console.log('ServiceWorker registered with scope: ', reg.scope);
                // Ask for a new version on every load rather than waiting for
                // the browser's own update schedule.
                reg.update().catch(() => {});
            })
            .catch(err => console.error('ServiceWorker registration failed: ', err));
    });
}

function updateOnlineStatus() {
    state.onlineStatus = navigator.onLine;
    const indicator = document.getElementById('offlineIndicator');
    if (!state.onlineStatus) {
        if (indicator) {
            indicator.style.display = 'inline-flex';
            indicator.classList.add('active');
        }
        showOfflineBanner();
        announceToScreenReader('Network connection lost. Reveal-X is now offline.');
    } else {
        if (indicator) {
            indicator.style.display = 'none';
            indicator.classList.remove('active');
        }
        removeOfflineBanner();
        announceToScreenReader('Network restored. Reconnected to Reveal-X secure server.');
        state.reconnectAttempts = 0;
        const countEl = document.getElementById('reconnectCount');
        if (countEl) countEl.textContent = '';
    }
}

function showOfflineBanner() {
    if (document.getElementById('offlineBanner')) return;
    const banner = document.createElement('div');
    banner.id = 'offlineBanner';
    banner.className = 'offline-banner';
    banner.innerHTML = `⚠️ Reveal-X is currently Offline. Standard features cached. <span id="bannerCount"></span>`;
    document.body.appendChild(banner);
}

function removeOfflineBanner() {
    const banner = document.getElementById('offlineBanner');
    if (banner) banner.remove();
}

window.addEventListener('online', updateOnlineStatus);
window.addEventListener('offline', updateOnlineStatus);

// Set initial offline check
updateOnlineStatus();

// ==========================================
// 2. Accessibility & Screen Reader Controls
// ==========================================

/**
 * Announce messages/actions directly to screen readers
 * @param {string} text - Message description
 */
export function announceToScreenReader(text) {
    const sr = document.getElementById('srAnnouncements');
    if (sr) {
        sr.textContent = text;
    }
}

// Escape modals using Keyboard
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        late.closeSettings();
        late.closeImagePreview();
        late.closeActiveDevicesModal();
        late.closeKeyManagerModal();
        const timeoutModal = document.getElementById('sessionTimeoutModal');
        if (timeoutModal) timeoutModal.classList.add('hidden');
        if (reconstructionPanel) reconstructionPanel.classList.remove('active');
        // The drawer covers the conversation, so Escape has to dismiss it too.
        // It was the one overlay this handler did not close.
        late.closeSidebar(true);
    }
});

// ==========================================
// 3. Security, escaping and DOMPurify fallback
// ==========================================
//
// escapeHtml, cleanHTML and validateUsername now live in sanitize.js, which is
// loaded before this file. They are the boundary between text someone else
// typed and HTML this app builds, and they were untestable while they sat in
// the middle of this file. See tests/js/sanitize.test.mjs.

// pwa.js (a classic script) announces install events through this. In
// chat.js every top-level function was a window property by default; module
// functions are not, so the one that is used from outside is exposed here.
window.announceToScreenReader = announceToScreenReader;
