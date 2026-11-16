// Notifications, sidebar buttons and connection state.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import { shared } from './shared.js';
import {
    accountsSidebar,
    authModal,
    downloadReconstructedBtn,
    logoutBtn,
    messageSearchInput,
    messagesContainer,
    reconstructBtn,
    reconstructionPanel,
    sidebarOverlay,
    sidebarToggle,
    socket,
    state,
    userSearchInput,
} from './core.js';
import {
    typingOpensKeyboard,
    updateConnectionBanner,
} from './basics.js';
import {
    logoutUser,
} from './session.js';
import {
    openKeyManagerModal,
} from './keys.js';
import {
    setChatEnabled,
} from './presence.js';
import {
    downloadShare1Btn,
    downloadShare2Btn,
    setReconstructionMode,
    setShareDownload,
    share1Preview,
    share2Preview,
} from './reconstruction.js';
import {
    getPref,
    lockScreen,
    openSettings,
} from './settings.js';

registerLate({ closeSidebar, playNotificationSound, shouldNotifyFor, showNotification, showWelcome });

// ==========================================
// 15. Notification & Shell Presences
// ==========================================

function areNotificationsEnabled() {
    return getPref('notifications');
}

/** True when this message should raise an alert, honouring "mentions only". */
function shouldNotifyFor(text) {
    if (!getPref('notifications')) return false;
    if (!getPref('mentionsOnly')) return true;
    if (!text || !state.currentUser) return false;
    return text.toLowerCase().includes(`@${state.currentUser.username.toLowerCase()}`);
}

/* A short two-tone chime synthesised on the fly, so the app ships no audio
   asset and the sound preference has something real to switch off. */
let audioContext = null;

function playNotificationSound() {
    if (!getPref('sounds')) return;
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        audioContext = audioContext || new Ctx();
        if (audioContext.state === 'suspended') audioContext.resume();

        const now = audioContext.currentTime;
        const gain = audioContext.createGain();
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.12, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        gain.connect(audioContext.destination);

        [880, 1320].forEach((frequency, index) => {
            const osc = audioContext.createOscillator();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(frequency, now + index * 0.09);
            osc.connect(gain);
            osc.start(now + index * 0.09);
            osc.stop(now + index * 0.09 + 0.18);
        });
    } catch (error) {
        // Audio is a nicety; never let it break message delivery.
    }
}

function showNotification(title, options = {}) {
    // The desktop popup is a separate opt-in from notifications in general, so
    // a user can keep the in-app chime without OS-level popups.
    if (!getPref('notifications') || !getPref('desktopNotifications')) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;

    new Notification(title, {
        tag: 'reveal-x-notif',
        requireInteraction: false,
        ...options
    });
}

function showWelcome() {
    messagesContainer.innerHTML = `
        <div class="welcome-message">
            <div class="welcome-icon">RX</div>
            <h2>Private Secure Chat</h2>
            <p>Select a contact to start an end-to-end encrypted chat</p>
            <div class="features">
                <div class="feature"><span>E2EE</span> Private Chat</div>
                <div class="feature"><span>XOR</span> Visual Shares</div>
                <div class="feature"><span>1h</span> Pruning</div>
            </div>
        </div>
    `;
    messageSearchInput.disabled = true;
    messageSearchInput.value = '';
}

/* The accounts drawer.
 *
 * Below 900px this is a drawer over the conversation; at 900px and up it is
 * docked and the toggle is display:none, so none of the focus handling here
 * runs -- it is all conditioned on the toggle being visible.
 *
 * A drawer that opens without taking focus is a drawer a keyboard user cannot
 * reach: Tab from the toggle went straight past it into the chat behind.
 */

/** True only when the sidebar is acting as an overlay drawer. */
function sidebarIsDrawer() {
    return sidebarToggle && getComputedStyle(sidebarToggle).display !== 'none';
}

function sidebarFocusables() {
    return [...accountsSidebar.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
    )].filter((el) => el.offsetParent !== null);
}

/** Keep Tab inside the drawer while it covers the page. */
function trapSidebarFocus(event) {
    if (event.key !== 'Tab' || !accountsSidebar.classList.contains('open')) return;
    const focusable = sidebarFocusables();
    if (!focusable.length) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    // The toggle stays reachable: it is what closes the drawer again.
    if (event.shiftKey && (document.activeElement === first || document.activeElement === sidebarToggle)) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        sidebarToggle.focus();
    }
}

/**
 * @param {boolean} restoreFocus Put focus back on the toggle. Skipped when the
 *   drawer closes because a conversation was picked -- selectUser() moves focus
 *   to the composer, and two things claiming focus is worse than neither.
 */
function closeSidebar(restoreFocus = false) {
    const wasOpen = accountsSidebar.classList.contains('open');
    accountsSidebar.classList.remove('open');
    sidebarOverlay.classList.remove('active');
    sidebarToggle.classList.remove('active');
    sidebarToggle.setAttribute('aria-expanded', 'false');
    sidebarToggle.setAttribute('aria-label', 'Open contacts');
    document.removeEventListener('keydown', trapSidebarFocus, true);

    if (restoreFocus && wasOpen && sidebarIsDrawer()) sidebarToggle.focus();
}

function openSidebar() {
    accountsSidebar.classList.add('open');
    sidebarOverlay.classList.add('active');
    sidebarToggle.classList.add('active');
    sidebarToggle.setAttribute('aria-expanded', 'true');
    sidebarToggle.setAttribute('aria-label', 'Close contacts');

    if (!sidebarIsDrawer()) return;
    document.addEventListener('keydown', trapSidebarFocus, true);
    // On a touch screen focusing the search box opens the on-screen keyboard
    // over the list the drawer was opened to show, so focus the drawer itself:
    // still inside it for the trap and screen readers, no keyboard until the
    // search box is tapped. Elsewhere, land on the search box: typing is the
    // fastest way through a list that grows.
    if (typingOpensKeyboard()) {
        accountsSidebar.focus({ preventScroll: true });
        return;
    }
    const target = userSearchInput || sidebarFocusables()[0];
    if (target) target.focus();
}

if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        if (accountsSidebar.classList.contains('open')) {
            closeSidebar(true);
        } else {
            openSidebar();
        }
    });
}

if (sidebarOverlay) {
    sidebarOverlay.addEventListener('click', () => closeSidebar(true));
}

if (logoutBtn) {
    logoutBtn.addEventListener('click', logoutUser);
}

// General active buttons toggle settings panel
const modalSettingsBtn = document.getElementById('settingsBtn');
if (modalSettingsBtn) {
    modalSettingsBtn.addEventListener('click', openSettings);
}

// Devices & keys toggle links inside templates
const reconstructPanelBtn = document.getElementById('reconstructImageBtn');
if (reconstructPanelBtn) {
    reconstructPanelBtn.addEventListener('click', () => {
        // Manual mode: the user supplies both shares from disk.
        shared.currentShares = { share1Url: null, share2Live: null };
        setReconstructionMode('manual');
        if (share1Preview) share1Preview.src = '';
        if (share2Preview) share2Preview.src = '';
        setShareDownload(downloadShare1Btn, null);
        setShareDownload(downloadShare2Btn, null);
        setShareDownload(downloadReconstructedBtn, null);
        const recPreview = document.getElementById('reconstructedPreview');
        if (recPreview) recPreview.src = '';
        reconstructBtn.disabled = false;
        const warning = document.getElementById('reconstructionWarning');
        if (warning) {
            warning.textContent = 'Select both shares manually to reconstruct.';
            warning.className = 'warning-text';
        }
        reconstructionPanel.classList.add('active');
    });
}

// Key manager link in the sidebar footer. (A phone button beside it opened a
// fake "active devices" list; it is gone until it can show real sessions --
// see the comment in index.html where its dialog used to be.)
// Named for screen readers (the key glyph is decoration) and given a visible
// word, as the other footer buttons are.
const keysBtn = document.createElement('button');
keysBtn.type = 'button';
keysBtn.className = 'icon-btn footer-btn';
keysBtn.title = 'Encryption keys';
keysBtn.setAttribute('aria-label', 'Encryption keys');
{
    const icon = window.revealxIcons.icon(document, 'key');
    const label = document.createElement('span');
    label.className = 'btn-label';
    label.textContent = 'Keys';
    keysBtn.append(icon, label);
}
keysBtn.addEventListener('click', openKeyManagerModal);

const sidebarFooter = document.querySelector('.sidebar-footer');
if (sidebarFooter) {
    sidebarFooter.insertBefore(keysBtn, sidebarFooter.firstChild);
}

// Handle socket connectivity states
socket.on('connect', () => {
    console.log("WebSocket connected!");
    // A reconnect is a new socket, and the server's session was tied to the
    // old one. Present the resume token before anything else needs it.
    if (state.hasConnected && state.currentUser && state.resumeToken) {
        state.resuming = true;
        socket.emit('resume_session', { token: state.resumeToken });
    }
    state.hasConnected = true;
    updateConnectionBanner();
});

socket.on('disconnect', () => {
    state.reconnectAttempts++;
    const countEl = document.getElementById('reconnectCount');
    if (countEl) countEl.textContent = ` (retrying ${state.reconnectAttempts}...)`;
    updateConnectionBanner();
});

// Set default chat placeholder
setChatEnabled(false);
showWelcome();

/* Put the cursor in the username box.
 *
 * Focus started on <body>, so the first thing every visitor had to do was click
 * a field the page had already decided they wanted. The `autofocus` attribute
 * is not enough on its own here: it only fires on the initial parse, and this
 * dialog is shown and hidden by script.
 *
 * Deferred so it does not race the passcode lock, which is shown on the same
 * load and has the stronger claim -- it is covering the page. A timeout rather
 * than requestAnimationFrame: rAF does not run in a background tab, so a page
 * restored into one would come back with no cursor anywhere. */
setTimeout(() => {
    const locked = lockScreen && !lockScreen.classList.contains('hidden');
    const authVisible = authModal && getComputedStyle(authModal).display !== 'none';
    if (locked || !authVisible) return;

    const username = document.getElementById('usernameInput');
    // Do not pull focus from someone already typing (a password manager may
    // have filled and focused a field before this ran).
    if (username && document.activeElement === document.body) username.focus();
});
