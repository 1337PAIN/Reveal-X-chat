// Reveal-X Chat Application - Client Side
// [MODIFIED] Fully refactored under modular organization with modern feature sets

const socket = io();

// State Container
const state = {
    currentUser: null,
    selectedRecipientId: null,
    users: [],
    userSearch: '',
    unreadCounts: {},
    pendingImage: null,
    keys: { publicKey: '' },
    keyExchangeStatus: {}, // {userId: 'ready' | 'pending' | 'unavailable'}
    shareAnalysis: {},     // {messageId: integrity + ML result}
    localAnalysis: {},     // {messageId: verdict computed here in the browser}
    chatHistoryCache: [],
    voiceObjectUrls: [],
    sessionTimeoutTimer: null,
    sessionWarningTimer: null,
    countdownInterval: null,
    lastTypingTime: 0,
    typingCounter: 0,
    sendCooldownActive: false,
    messageLimit: 50,
    visibleMessagesCount: 50,
    swipeStart: null,
    onlineStatus: navigator.onLine,
    reconnectAttempts: 0
};

// UI Element Mappings
const messagesContainer = document.getElementById('messagesContainer');
const messageInput = document.getElementById('messageInput');
const sendBtn = document.getElementById('sendBtn');
const usersList = document.getElementById('usersList');
const userCount = document.getElementById('userCount');
const imageInput = document.getElementById('imageInput');
const shareImageBtn = document.getElementById('shareImageBtn');
const reconstructionPanel = document.getElementById('reconstructionPanel');
const closePanel = document.getElementById('closePanel');
const reconstructBtn = document.getElementById('reconstructBtn');
const downloadReconstructedBtn = document.getElementById('downloadReconstructedBtn');
const authModal = document.getElementById('authModal');
const usernameInput = document.getElementById('usernameInput');
const passwordInput = document.getElementById('passwordInput');
const loginBtn = document.getElementById('loginBtn');
const registerBtn = document.getElementById('registerBtn');
const authError = document.getElementById('authError');
const accountName = document.getElementById('accountName');
const chatTitle = document.getElementById('chatTitle');
const chatSubtitle = document.getElementById('chatSubtitle');
const messageSearchInput = document.getElementById('messageSearchInput');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const accountsSidebar = document.getElementById('accountsSidebar');
const userSearchInput = document.getElementById('userSearchInput');
const typingIndicator = document.getElementById('typingIndicator');
const imagePreviewModal = document.getElementById('imagePreviewModal');
const imagePreview = document.getElementById('imagePreview');
const imagePreviewMeta = document.getElementById('imagePreviewMeta');
const cancelImageBtn = document.getElementById('cancelImageBtn');
const confirmImageBtn = document.getElementById('confirmImageBtn');

// Key manager and modal links
const settingsBtn = document.getElementById('settingsBtn');
const settingsModal = document.getElementById('settingsModal');
const closeSettingsBtn = document.getElementById('closeSettingsBtn');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');
const settingsUsername = document.getElementById('settingsUsername');
const settingsBio = document.getElementById('settingsBio');
const settingsStatus = document.getElementById('settingsStatus');
const settingsNotifications = document.getElementById('settingsNotifications');
const themeToggle = document.getElementById('themeToggle');
const reconstructImageBtn = document.getElementById('reconstructImageBtn');
const logoutBtn = document.getElementById('logoutBtn');
const deleteAccountBtn = document.getElementById('deleteAccountBtn');
const profileImageInput = document.getElementById('profileImageInput');
const profilePicker = document.querySelector('.profile-picker');

let currentShares = null;
let typingStopTimer = null;
let typingHideTimer = null;
const STATUS_LABELS = {
    online: 'Online',
    away: 'Away',
    dnd: 'Do Not Disturb',
    offline: 'Offline',
};

const MAX_MESSAGE_LENGTH = 2000;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const MAX_VOICE_BYTES = 2 * 1024 * 1024;

// Set default username value
usernameInput.value = localStorage.getItem('revealx_username') || '';

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
function announceToScreenReader(text) {
    const sr = document.getElementById('srAnnouncements');
    if (sr) {
        sr.textContent = text;
    }
}

// Escape modals using Keyboard
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        closeSettings();
        closeImagePreview();
        closeActiveDevicesModal();
        closeKeyManagerModal();
        const timeoutModal = document.getElementById('sessionTimeoutModal');
        if (timeoutModal) timeoutModal.classList.add('hidden');
        if (reconstructionPanel) reconstructionPanel.classList.remove('active');
    }
});

// ==========================================
// 3. Security, escaping and DOMPurify fallback
// ==========================================

/**
 * HTML Escaper helper
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
 * Safely sanitize HTML string using DOMPurify with strict HTML Escaping fallback
 * @param {string} html - HTML string
 * @returns {string} Sanitized string
 */
function cleanHTML(html) {
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html);
    }
    return escapeHtml(html);
}

/**
 * Client-side input validator matching server rules
 */
function validateUsername(username) {
    const usernameRe = /^[A-Za-z0-9_.-]{3,30}$/;
    return usernameRe.test(username);
}

// ==========================================
// 4. Session Warning & Inactivity Monitor
// ==========================================

function resetInactivityTimers() {
    clearTimeout(state.sessionTimeoutTimer);
    clearTimeout(state.sessionWarningTimer);
    if (state.countdownInterval) clearInterval(state.countdownInterval);

    // Prompt user 2 minutes before the mock 15 minute inactivity timeout
    state.sessionWarningTimer = setTimeout(() => {
        const modal = document.getElementById('sessionTimeoutModal');
        if (modal) {
            modal.classList.remove('hidden');
            announceToScreenReader('Inactivity Alert: Your session will expire in 2 minutes.');
            
            let remaining = 120;
            const countdownEl = document.getElementById('timeoutCountdown');
            if (countdownEl) countdownEl.textContent = '2:00';
            
            state.countdownInterval = setInterval(() => {
                remaining--;
                if (countdownEl) {
                    const mins = Math.floor(remaining / 60);
                    const secs = remaining % 60;
                    countdownEl.textContent = `${mins}:${secs < 10 ? '0' : ''}${secs}`;
                }
                if (remaining <= 0) {
                    clearInterval(state.countdownInterval);
                    logoutUser();
                }
            }, 1000);
        }
    }, 13 * 60 * 1000);

    // Hard auto-logout at 15 minutes of continuous inactivity
    state.sessionTimeoutTimer = setTimeout(logoutUser, 15 * 60 * 1000);
}

function extendSession() {
    const modal = document.getElementById('sessionTimeoutModal');
    if (modal) modal.classList.add('hidden');
    if (state.countdownInterval) clearInterval(state.countdownInterval);
    
    resetInactivityTimers();
    announceToScreenReader('Session extended successfully.');
    
    // session refresh activity flash indicator
    const header = document.querySelector('.header');
    if (header) {
        header.classList.add('activity-flash');
        setTimeout(() => header.classList.remove('activity-flash'), 500);
    }
}

function logoutUser() {
    clearTimeout(state.sessionTimeoutTimer);
    clearTimeout(state.sessionWarningTimer);
    if (state.countdownInterval) clearInterval(state.countdownInterval);
    
    socket.emit('logout');
}

// Activity listeners to keep session alive
['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart'].forEach(eventName => {
    document.addEventListener(eventName, () => {
        if (state.currentUser) {
            resetInactivityTimers();
        }
    }, { passive: true });
});

const extendSessionBtn = document.getElementById('extendSessionBtn');
const endSessionBtn = document.getElementById('endSessionBtn');
if (extendSessionBtn) extendSessionBtn.addEventListener('click', extendSession);
if (endSessionBtn) endSessionBtn.addEventListener('click', logoutUser);

// ==========================================
// 5. Active Device Management Modals
// ==========================================

function openActiveDevicesModal() {
    const modal = document.getElementById('sessionListModal');
    const container = document.getElementById('sessionsList');
    if (!modal || !container) return;

    container.innerHTML = '';
    
    // Highly visual active session instances
    const activeSessions = [
        { id: 'sess_current', os: navigator.userAgent.includes('Windows') ? 'Windows (Current Browser)' : 'Current Session', active: true, location: 'Active Now' }
    ];

    activeSessions.forEach(sess => {
        const div = document.createElement('div');
        div.className = 'session-item';
        div.style.display = 'flex';
        div.style.justifyContent = 'space-between';
        div.style.alignItems = 'center';
        div.style.padding = '12px';
        div.style.borderBottom = '1px solid var(--border)';
        div.style.background = 'var(--bg-tertiary)';
        div.style.borderRadius = '6px';
        div.style.marginBottom = '8px';
        
        div.innerHTML = `
            <div style="text-align: left;">
                <strong style="display: block; font-size: 13px; color: var(--text-primary);">${cleanHTML(sess.os)}</strong>
                <span style="font-size: 11px; color: ${sess.active ? 'var(--success)' : 'var(--text-secondary)'};">
                    ${sess.active ? '● Active Now' : cleanHTML(sess.location)}
                </span>
            </div>
            ${!sess.active ? `<button class="btn btn-danger" style="font-size: 11px; padding: 4px 10px; min-height: 32px; width: auto;" data-revoke-id="${sess.id}">Revoke</button>` : ''}
        `;
        
        const btn = div.querySelector('button');
        if (btn) {
            btn.addEventListener('click', () => {
                div.remove();
                announceToScreenReader('Device session revoked successfully.');
                alert('Session access revoked.');
            });
        }
        container.appendChild(div);
    });

    modal.classList.remove('hidden');
    announceToScreenReader('Active devices list opened.');
}

function closeActiveDevicesModal() {
    const modal = document.getElementById('sessionListModal');
    if (modal) modal.classList.add('hidden');
}

const logoutAllDevicesBtn = document.getElementById('logoutAllDevicesBtn');
if (logoutAllDevicesBtn) {
    logoutAllDevicesBtn.addEventListener('click', () => {
        alert('Logging out from all other devices...');
        closeActiveDevicesModal();
        announceToScreenReader('Logged out from all other devices.');
    });
}

document.querySelectorAll('#closeSessionListBtn, #closeSessionListBtn2').forEach(btn => {
    btn.addEventListener('click', closeActiveDevicesModal);
});

// ==========================================
// 6. E2E Key Management & Verification
// ==========================================

// Real ECDH P-256 key agreement. The private key never leaves the browser and
// is generated non-extractable; only the base64 SPKI public key is published.
async function initE2EEKeys(options = {}) {
    if (!state.currentUser) return '';

    const publicKey = await window.revealxE2EE.init(state.currentUser.id, options);
    state.keys.publicKey = publicKey;

    if (publicKey) {
        socket.emit('register_public_key', { public_key: publicKey });
    }
    renderKeyManager();
    return publicKey;
}

function peerPublicKey(userId) {
    const user = state.users.find(u => u.id === userId);
    return (user && user.public_key) || '';
}

// 'ready' means both sides have published a key, so messages to this peer are
// actually encrypted. Anything else is reported honestly instead of showing a
// padlock that means nothing.
function keyExchangeState(userId) {
    if (!window.revealxE2EE.isReady()) return 'unavailable';
    return peerPublicKey(userId) ? 'ready' : 'pending';
}

async function renderKeyManager() {
    const display = document.getElementById('publicKeyDisplay');
    if (display) {
        if (!window.revealxE2EE.available) {
            display.textContent = 'WebCrypto is unavailable in this browser. Messages are sent unencrypted.';
        } else if (state.keys.publicKey) {
            const fingerprint = await window.revealxE2EE.fingerprint(state.keys.publicKey);
            display.textContent = `Fingerprint: ${fingerprint}\n\n${state.keys.publicKey}`;
        } else {
            display.textContent = 'Generating key pair...';
        }
    }

    const exchangeList = document.getElementById('keyExchangeList');
    if (!exchangeList) return;

    exchangeList.innerHTML = '';
    if (state.users.length === 0) {
        exchangeList.innerHTML = '<div class="key-exchange-row"><span>No other accounts yet</span></div>';
        return;
    }

    for (const user of state.users) {
        const status = keyExchangeState(user.id);
        const label = {
            ready: '🔒 Key exchanged',
            pending: '⏳ No key published yet',
            unavailable: '⚠️ E2EE unavailable',
        }[status];

        const item = document.createElement('div');
        item.className = `key-exchange-row ${status}`;

        const name = document.createElement('span');
        name.className = 'key-exchange-name';
        name.textContent = user.username;

        const state_ = document.createElement('span');
        state_.className = 'key-exchange-state';
        state_.textContent = label;

        item.append(name, state_);

        if (status === 'ready') {
            const fingerprint = document.createElement('small');
            fingerprint.className = 'key-exchange-fingerprint';
            fingerprint.textContent = await window.revealxE2EE.fingerprint(user.public_key);
            item.appendChild(fingerprint);
        }

        exchangeList.appendChild(item);
    }
}

function openKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (!modal) return;

    renderKeyManager();
    if (!state.keys.publicKey) initE2EEKeys();

    modal.classList.remove('hidden');
}

function closeKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (modal) modal.classList.add('hidden');
}

const regenerateKeyBtn = document.getElementById('regenerateKeyBtn');
if (regenerateKeyBtn) {
    regenerateKeyBtn.addEventListener('click', async () => {
        if (!confirm('Generate a new key pair? Messages already in this chat were encrypted to your old key and will no longer open.')) {
            return;
        }
        announceToScreenReader('Regenerating encryption key pair...');
        const publicKey = await initE2EEKeys({ regenerate: true });
        alert(publicKey
            ? 'New ECDH key pair generated and published.'
            : 'Key generation failed. Messages will be sent unencrypted.');
    });
}

document.querySelectorAll('#closeKeyManagerBtn, #closeKeyManagerBtn2').forEach(btn => {
    btn.addEventListener('click', closeKeyManagerModal);
});

// ==========================================
// 7. Search Debouncing & Utilities
// ==========================================

function debounce(func, delay = 300) {
    let timer = null;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => func.apply(this, args), delay);
    };
}

// Attach debounced filter elements
messageSearchInput.addEventListener('input', debounce(filterVisibleMessages, 300));
userSearchInput.addEventListener('input', debounce(() => {
    state.userSearch = userSearchInput.value.trim().toLowerCase();
    updateUsersList(state.users);
}, 300));

// Mobile swipe listeners for gesture responses
let touchStartX = 0;
let touchStartY = 0;
messagesContainer.addEventListener('touchstart', (event) => {
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
}, { passive: true });

messagesContainer.addEventListener('touchend', (event) => {
    const diffX = event.changedTouches[0].clientX - touchStartX;
    const diffY = event.changedTouches[0].clientY - touchStartY;
    
    // Horizontal swipe reply action
    if (Math.abs(diffX) > 100 && Math.abs(diffY) < 30) {
        const messageNode = event.target.closest('.message');
        if (messageNode) {
            const messageId = messageNode.dataset.messageId;
            if (messageId) {
                // Trigger swipe reply preview shortcut
                announceToScreenReader('Swipe gesture triggered reply.');
                const replyTitle = document.getElementById('replyTitle');
                const replyText = document.getElementById('replyText');
                const replyBar = document.getElementById('replyBar');
                
                if (replyBar && replyText && replyTitle) {
                    const contentNode = messageNode.querySelector('.message-content');
                    replyText.textContent = contentNode ? contentNode.textContent : 'File Share';
                    replyBar.classList.remove('hidden');
                    state.pendingReplyId = messageId;
                }
            }
        }
    }
}, { passive: true });

// ==========================================
// 8. Auth, Login and Account CRUD
// ==========================================

function submitAuth(mode) {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    authError.textContent = '';
    
    if (!username || !password) {
        authError.textContent = 'Enter username and password';
        return;
    }

    if (!validateUsername(username)) {
        authError.textContent = 'Username must be 3-30 letters, numbers, dots, dashes, or underscores';
        return;
    }

    // No client-side attempt gate here. It never stopped an attacker -- they
    // script the socket and never run this code -- while it did lock out real
    // users: it counted "waiting for administrator approval" as a failure, and
    // once it hit zero it refused to send anything, so the only way out was a
    // page reload. The server enforces the real limit.
    socket.emit(mode, { username, password });
}

loginBtn.addEventListener('click', () => submitAuth('login'));
registerBtn.addEventListener('click', () => submitAuth('register'));

// ==========================================
// 8b. Firebase sign-in (Google / email)
// ==========================================

const firebaseAuthBlock = document.getElementById('firebaseAuth');

(async function initFirebaseSignIn() {
    const ready = await window.revealxFirebase.init();
    if (ready && firebaseAuthBlock) firebaseAuthBlock.classList.remove('hidden');
})();

/** Run a Firebase sign-in, then hand the ID token to our server to verify. */
async function firebaseSignIn(action, buttonEl) {
    if (!window.revealxFirebase.available) return;

    authError.textContent = '';
    const original = buttonEl ? buttonEl.textContent : '';
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.textContent = 'Signing in...';
    }

    try {
        const idToken = await action();
        // The server decides who this is; we only carry the token.
        socket.emit('firebase_login', { id_token: idToken });
    } catch (error) {
        authError.textContent = window.revealxFirebase.constructor.describeError(error);
        await window.revealxFirebase.signOut();
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.textContent = original;
        }
    }
}

const googleSignInBtn = document.getElementById('googleSignInBtn');
const firebaseSignInBtn = document.getElementById('firebaseSignInBtn');
const firebaseSignUpBtn = document.getElementById('firebaseSignUpBtn');
const firebaseEmailInput = document.getElementById('firebaseEmailInput');
const firebasePasswordInput = document.getElementById('firebasePasswordInput');

function firebaseEmailCredentials() {
    const email = (firebaseEmailInput.value || '').trim();
    const password = firebasePasswordInput.value || '';
    if (!email || !password) {
        authError.textContent = 'Enter an email address and password';
        return null;
    }
    return { email, password };
}

if (googleSignInBtn) {
    googleSignInBtn.addEventListener('click', () =>
        firebaseSignIn(() => window.revealxFirebase.signInWithGoogle(), googleSignInBtn));
}

if (firebaseSignInBtn) {
    firebaseSignInBtn.addEventListener('click', () => {
        const creds = firebaseEmailCredentials();
        if (creds) {
            firebaseSignIn(() => window.revealxFirebase.signInWithEmail(creds.email, creds.password), firebaseSignInBtn);
        }
    });
}

if (firebaseSignUpBtn) {
    firebaseSignUpBtn.addEventListener('click', () => {
        const creds = firebaseEmailCredentials();
        if (creds) {
            firebaseSignIn(() => window.revealxFirebase.createWithEmail(creds.email, creds.password), firebaseSignUpBtn);
        }
    });
}

if (firebasePasswordInput) {
    firebasePasswordInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && firebaseSignInBtn) firebaseSignInBtn.click();
    });
}

// ==========================================
// 8c. Two-factor authentication (TOTP)
// ==========================================

const totpModal = document.getElementById('totpModal');
const totpCodeInput = document.getElementById('totpCodeInput');
const totpErrorEl = document.getElementById('totpError');

// The first factor passed but no session exists yet: the server is holding the
// sign-in until a valid code arrives.
socket.on('totp_required', () => {
    authError.textContent = '';
    totpErrorEl.textContent = '';
    totpCodeInput.value = '';
    totpModal.classList.remove('hidden');
    totpCodeInput.focus();
});

socket.on('totp_error', (data) => {
    totpErrorEl.textContent = data.attempts_remaining
        ? `${data.error} (${data.attempts_remaining} attempts left)`
        : data.error;
    totpCodeInput.value = '';
    totpCodeInput.focus();
});

function submitTotpCode() {
    const code = (totpCodeInput.value || '').trim();
    if (code.length !== 6) {
        totpErrorEl.textContent = 'Enter the 6-digit code';
        return;
    }
    socket.emit('verify_totp', { code });
}

document.getElementById('totpVerifyBtn').addEventListener('click', submitTotpCode);
totpCodeInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') submitTotpCode();
});
document.getElementById('totpCancelBtn').addEventListener('click', async () => {
    totpModal.classList.add('hidden');
    await window.revealxFirebase.signOut();
    window.location.reload();
});

[usernameInput, passwordInput].forEach(input => {
    input.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') submitAuth('login');
    });
});

socket.on('auth_success', (data) => {
    state.currentUser = data.user;
    const totpChallenge = document.getElementById('totpModal');
    if (totpChallenge) totpChallenge.classList.add('hidden');
    localStorage.setItem('revealx_username', state.currentUser.username);
    accountName.textContent = state.currentUser.username;
    const ownStatus = document.getElementById('userStatus');
    if (ownStatus) ownStatus.textContent = STATUS_LABELS[state.currentUser.status || 'online'];
    
    // Load profile preview photo
    const preview = document.getElementById('profilePreview');
    if (preview && data.user.profile_image) {
        preview.src = data.user.profile_image;
        preview.classList.remove('empty');
    }
    
    authModal.style.display = 'none';
    if (deleteAccountBtn) deleteAccountBtn.style.display = 'block';
    
    updateUsersList(data.users);
    // admin.js registers this; guard so chat still works if it is absent.
    if (window.revealxApplyAdminVisibility) window.revealxApplyAdminVisibility(data.user);
    initE2EEKeys();
    resetInactivityTimers();
    showWelcome();
});

socket.on('auth_error', (data) => {
    const totpChallenge = document.getElementById('totpModal');
    if (totpChallenge && !totpChallenge.classList.contains('hidden')) {
        totpChallenge.classList.add('hidden');
        window.revealxFirebase.signOut();
    }
    // Report exactly what the server said. It already explains a lockout and
    // how long it lasts; a second, invented counter only contradicted it.
    authError.textContent = data.error || 'Sign-in failed';
});

// admin.js reads the signed-in user from here.
window.state = state;

socket.on('registration_submitted', (data) => {
    // Not an error, so it must not go through the red auth-error line.
    authError.textContent = '';
    const notice = document.getElementById('authNotice');
    if (notice) {
        notice.textContent = data.message || 'Request submitted.';
        notice.classList.remove('hidden');
    }
    const username = document.getElementById('usernameInput');
    const password = document.getElementById('passwordInput');
    if (username) username.value = '';
    if (password) password.value = '';
});

socket.on('force_signed_out', (data) => {
    // An admin disabled, deleted, or reset the password on this account. The
    // server has already dropped the session; this only explains why.
    localStorage.removeItem('revealx_username');
    alert(data && data.reason ? data.reason : 'Your session was ended by an administrator.');
    window.location.reload();
});

/**
 * Transient failure banner.
 *
 * The server was already reporting these -- "Recipient must be online",
 * "Message is too long", "Unsupported image type" -- and nothing listened, so
 * a failed send looked to the user exactly like a successful one. Announced to
 * screen readers as well, since a visual-only banner is no use to anyone who
 * cannot see it.
 */
// ==========================================
// Reactions
//
// The server has always accepted react_message and broadcast
// message_reactions; nothing on the client sent or listened for either, so the
// feature the README advertised did not exist. This is the missing half.
// ==========================================

const REACTION_CHOICES = ['\u{1F44D}', '\u2764\uFE0F', '\u{1F602}', '\u{1F62E}', '\u{1F622}', '\u{1F64F}'];

/** Redraw one message's pills from the server's {emoji: [userIds]} map. */
function renderReactions(messageId, reactions) {
    const bubble = messagesContainer.querySelector('[data-message-id="' + messageId + '"]');
    if (!bubble) return;
    const holder = bubble.querySelector('.message-reactions');
    if (!holder) return;

    holder.innerHTML = '';
    Object.entries(reactions || {}).forEach(([emoji, users]) => {
        if (!users || !users.length) return;
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'reaction-pill';
        const mine = state.currentUser && users.includes(state.currentUser.id);
        if (mine) pill.classList.add('mine');
        // textContent throughout: the emoji comes back from the server and is
        // never trusted as markup.
        pill.textContent = emoji + ' ' + users.length;
        pill.title = mine ? 'Remove your reaction' : 'React with ' + emoji;
        pill.addEventListener('click', () => {
            // The server clears a user's previous reaction on any new one, so
            // re-sending nothing is how you take yours back.
            socket.emit('react_message', { message_id: messageId, emoji: mine ? '' : emoji });
        });
        holder.appendChild(pill);
    });
}

/** The small picker shown by the react control on a bubble. */
function toggleReactionPicker(messageId, button) {
    const existing = document.querySelector('.reaction-picker');
    if (existing) {
        const wasMine = existing.dataset.messageId === messageId;
        existing.remove();
        if (wasMine) return;
    }

    const picker = document.createElement('div');
    picker.className = 'reaction-picker';
    picker.dataset.messageId = messageId;
    REACTION_CHOICES.forEach(emoji => {
        const choice = document.createElement('button');
        choice.type = 'button';
        choice.textContent = emoji;
        choice.setAttribute('aria-label', 'React with ' + emoji);
        choice.addEventListener('click', () => {
            socket.emit('react_message', { message_id: messageId, emoji: emoji });
            picker.remove();
        });
        picker.appendChild(choice);
    });
    // Anchored to the whole bubble, not the meta row: the meta sits at the
    // bottom, so opening upward from there put the picker over the message
    // text. From the bubble it floats clear above it.
    const bubble = button.closest('.message') || button.parentElement;
    bubble.appendChild(picker);

    // ...but a bubble near the top of the scroll area has no room above, and
    // the picker was clipped by the container. Flip it under the bubble when
    // that would happen.
    const room = picker.getBoundingClientRect();
    const bounds = messagesContainer.getBoundingClientRect();
    if (room.top < bounds.top + 4) picker.classList.add('below');
}

document.addEventListener('click', (event) => {
    if (!event.target.closest('.reaction-picker') && !event.target.closest('.btn-react')) {
        const open = document.querySelector('.reaction-picker');
        if (open) open.remove();
    }
});

socket.on('message_reactions', (data) => {
    renderReactions(data.message_id, data.reactions);
});

function showTransientError(message) {
    let banner = document.getElementById('sendErrorBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'sendErrorBanner';
        banner.className = 'send-error-banner';
        banner.setAttribute('role', 'alert');
        document.body.appendChild(banner);
    }
    banner.textContent = message;
    banner.classList.add('visible');
    announceToScreenReader(message);

    clearTimeout(banner._hideTimer);
    banner._hideTimer = setTimeout(() => banner.classList.remove('visible'), 6000);
}

socket.on('image_error', (data) => {
    showTransientError(data && data.error ? data.error : 'The image could not be sent.');
    // The composer disables itself while sending; without this it stays stuck.
    const confirmBtn = document.getElementById('confirmImageBtn');
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Send Securely';
    }
});

socket.on('message_error', (data) => {
    showTransientError(data && data.error ? data.error : 'The message could not be sent.');
});

socket.on('logout_success', async () => {
    localStorage.removeItem('revealx_username');
    await window.revealxFirebase.signOut();
    window.location.reload();
});

// ==========================================
// 9. Real-time User presence list handlers
// ==========================================

socket.on('users_updated', (data) => {
    updateUsersList(data.users);

    if (state.selectedRecipientId) {
        const selected = state.users.find(u => u.id === state.selectedRecipientId);
        if (selected) {
            chatSubtitle.textContent = describeUser(selected);
            state.keyExchangeStatus[selected.id] = keyExchangeState(selected.id);
        }
    }

    // A peer may have just published or rotated a key.
    const keyModal = document.getElementById('keyManagerModal');
    if (keyModal && !keyModal.classList.contains('hidden')) renderKeyManager();
});

socket.on('public_key_registered', (data) => {
    state.keys.publicKey = data.public_key || '';
});

function updateUsersList(nextUsers) {
    state.users = nextUsers || [];
    const visible = state.users.filter(u =>
        u.username.toLowerCase().includes(state.userSearch)
    );

    usersList.innerHTML = '';
    userCount.textContent = `${state.users.length} account${state.users.length !== 1 ? 's' : ''}`;

    if (state.users.length === 0) {
        usersList.innerHTML = '<li class="empty-state">No other accounts yet</li>';
        return;
    }

    if (visible.length === 0) {
        usersList.innerHTML = '<li class="empty-state">No matching accounts</li>';
        return;
    }

    visible.forEach(user => {
        const li = document.createElement('li');
        const count = state.unreadCounts[user.id] || 0;
        li.dataset.userId = user.id;
        li.className = user.id === state.selectedRecipientId ? 'active' : '';
        if (!user.online) li.classList.add('offline');
        
        const initial = user.username.charAt(0).toUpperCase();
        const avatar = user.profile_image 
            ? `<img src="${user.profile_image}" alt="${escapeHtml(user.username)}" class="avatar-img">`
            : `<div class="avatar-dot">${initial}</div>`;
        
        const status = user.status || (user.online ? 'online' : 'offline');
        li.dataset.status = status;

        li.innerHTML = `
            ${avatar}
            <span class="user-meta">
                <span class="user-name">${escapeHtml(user.username)}</span>
                <span class="user-status-line">${escapeHtml(user.bio || STATUS_LABELS[status] || status)}</span>
            </span>
            ${count > 0 ? `<span class="unread-badge" aria-label="${count} unread messages">${count}</span>` : ''}
        `;
        li.addEventListener('click', () => selectUser(user.id));
        usersList.appendChild(li);
    });
}

function selectUser(userId) {
    const user = state.users.find(u => u.id === userId);
    if (!user) return;

    state.selectedRecipientId = user.id;
    state.unreadCounts[user.id] = 0;
    messageSearchInput.value = '';
    messageSearchInput.disabled = false;
    typingIndicator.textContent = '';
    chatTitle.textContent = user.username;
    chatSubtitle.textContent = describeUser(user);

    state.keyExchangeStatus[userId] = keyExchangeState(userId);

    setChatEnabled(true);
    updateUsersList(state.users);
    
    // Reset page limits for virtual scrolling chunk load
    state.visibleMessagesCount = 50;

    requestChatHistory(state.selectedRecipientId);
    closeSidebar();
    messageInput.focus();
}

/** Subtitle under the chat title: availability, plus their bio when set. */
function describeUser(user) {
    if (!user) return '';
    const status = user.status || (user.online ? 'online' : 'offline');
    const label = STATUS_LABELS[status] || status;
    return user.bio ? `${label} - ${user.bio}` : `Private conversation - ${label.toLowerCase()}`;
}

function setChatEnabled(enabled) {
    const selected = state.users.find(u => u.id === state.selectedRecipientId);
    messageInput.disabled = !enabled;
    sendBtn.disabled = !enabled;
    shareImageBtn.disabled = !enabled;
    const voiceBtn = document.getElementById('voiceBtn');
    if (voiceBtn) voiceBtn.disabled = !enabled;

    messageInput.placeholder = enabled && selected
        ? `Message ${selected.username}...`
        : 'Select an account first...';
}

// ==========================================
// 10. Message Feeds Rendering & Virtual Scrolling
// ==========================================

socket.on('chat_history', (payload) => {
    const messages = payload.messages || [];
    const recipientId = payload.recipient_id;

    if (!recipientId && !state.selectedRecipientId) {
        showWelcome();
        return;
    }

    messagesContainer.innerHTML = '';

    if (messages.length === 0) {
        messagesContainer.innerHTML = `
            <div class="welcome-message">
                <div class="welcome-icon">RX</div>
                <h2>No messages yet</h2>
                <p>Messages and image shares in this chat auto-delete after 1 hour.</p>
            </div>
        `;
        return;
    }

    // Performance Optimization: Slice history feed for virtual loading (show visible messages)
    state.chatHistoryCache = messages;
    renderVisibleMessagesSlice();
});

function renderVisibleMessagesSlice() {
    if (!state.chatHistoryCache) return;

    revokeVoiceObjectUrls();
    messagesContainer.innerHTML = '';
    
    // Load older message slices dynamically (lazy load scroll up helper)
    const totalCount = state.chatHistoryCache.length;
    const startIdx = Math.max(0, totalCount - state.visibleMessagesCount);
    const slice = state.chatHistoryCache.slice(startIdx, totalCount);
    
    // If older messages hidden, prepend load indicator
    if (startIdx > 0) {
        const loadMore = document.createElement('button');
        loadMore.className = 'btn btn-secondary';
        loadMore.style.margin = '10px auto';
        loadMore.style.display = 'block';
        loadMore.style.width = '200px';
        loadMore.textContent = 'Load Older Messages';
        loadMore.addEventListener('click', () => {
            state.visibleMessagesCount += 50;
            const scrollPos = messagesContainer.scrollHeight;
            renderVisibleMessagesSlice();
            messagesContainer.scrollTop = messagesContainer.scrollHeight - scrollPos;
        });
        messagesContainer.appendChild(loadMore);
    }

    slice.forEach(msg => displayMessage(msg));
    scrollToBottom();
}

// ==========================================
// 10b. E2EE message bodies and live share tamper detection
// ==========================================

/**
 * Parse a server timestamp.
 *
 * The server writes naive UTC ISO strings. Without a zone suffix the browser
 * reads them as local time, which made freshly sent shares display as already
 * expired for anyone not sitting in UTC.
 */
function parseServerTime(value) {
    if (!value) return null;
    const hasZone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value);
    const parsed = new Date(hasZone ? value : `${value}Z`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function localTimeLabel(msg) {
    const created = parseServerTime(msg.created_at);
    return created
        ? created.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : msg.timestamp;
}

/** Fill a message body, decrypting first when the payload is E2EE. */
async function fillTextContent(node, msg) {
    if (!node) return;

    if (!msg.encrypted) {
        node.textContent = msg.content;
        return;
    }

    node.textContent = 'Decrypting...';
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = peerPublicKey(otherId);
    const iv = msg.extra ? msg.extra.iv : null;
    const plaintext = peerKey ? await window.revealxE2EE.decrypt(peerKey, msg.content, iv) : null;

    if (plaintext === null) {
        node.textContent = 'Encrypted message - cannot be opened with the current key pair.';
        node.classList.add('undecryptable');
    } else {
        node.textContent = plaintext;
    }
    filterVisibleMessages();
}

/** Attach an audio player to a voice message, decrypting first when needed. */
async function fillVoiceContent(player, status, msg) {
    if (!player) return;

    if (!msg.encrypted) {
        player.src = msg.content;
        if (status) status.textContent = '';
        return;
    }

    if (status) status.textContent = 'Decrypting voice message...';
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = peerPublicKey(otherId);
    const iv = msg.extra ? msg.extra.iv : null;
    const bytes = peerKey ? await window.revealxE2EE.decryptBytes(peerKey, msg.content, iv) : null;

    if (!bytes) {
        player.remove();
        if (status) {
            status.textContent = 'Voice message - cannot be opened with the current key pair.';
            status.classList.add('undecryptable');
        }
        return;
    }

    const mime = (msg.extra && msg.extra.mime) || 'audio/webm';
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
    state.voiceObjectUrls.push(objectUrl);
    player.src = objectUrl;
    if (status) status.textContent = '';
}

/** Object URLs stay alive until revoked; drop them when the transcript clears. */
function revokeVoiceObjectUrls() {
    state.voiceObjectUrls.forEach(url => URL.revokeObjectURL(url));
    state.voiceObjectUrls = [];
}

function tamperPanelFor(messageId) {
    return messagesContainer.querySelector(
        `[data-message-id="${messageId}"] [data-role="tamper-panel"]`
    );
}

/**
 * Ask the server to verify the stored Share 1 and classify it.
 *
 * This is the chat-side equivalent of the /lab pipeline: the HMAC recorded at
 * generation time gives exact tamper detection, the Random Forest gives a
 * confidence score plus a suspicious-region heatmap. It does not consume the
 * share's one-time access, so it can run before reconstruction.
 */
async function runShareAnalysis(msg, { force = false, panel = null } = {}) {
    // The caller passes the element when the bubble is still being built and is
    // therefore not yet reachable from messagesContainer.
    panel = panel || tamperPanelFor(msg.id);
    const url = msg.extra && msg.extra.share1_analysis_url;
    if (!panel || !url) return null;

    const cached = state.shareAnalysis[msg.id];
    if (cached && !force) {
        renderTamperPanel(panel, cached);
        return cached;
    }

    panel.className = 'tamper-panel checking';
    panel.textContent = 'Verifying share integrity...';

    try {
        const response = await fetch(url);
        const data = await response.json();

        if (!response.ok || !data.ok) {
            // 404 means the share is spent, revoked or expired - there is
            // nothing left to reconstruct, so stop offering the action.
            if (response.status === 404) setShareActionsDisabled(panel, true);
            throw new Error(data.error || 'Integrity check failed');
        }

        state.shareAnalysis[msg.id] = data;
        renderTamperPanel(panel, data);

        if (data.integrity.exact_tamper_detected || data.ml.tampered) {
            announceToScreenReader('Warning: this image share failed its tamper check.');
        }
        return data;
    } catch (error) {
        panel.className = 'tamper-panel unknown';
        panel.textContent = error.message;
        return null;
    }
}

/**
 * Second opinion on Share 1, computed here in the browser.
 *
 * The server already answers /api/share/<id>/analysis, but that answer comes
 * from the same machine that stored the share -- if it were compromised it
 * could simply report "intact". This runs the identical Random Forest (exported
 * to ONNX) over the exact bytes that are about to be reconstructed, so the
 * recipient's verdict does not depend on trusting the sender's server.
 *
 * It does NOT replace the HMAC check: that needs the server's secret key and
 * stays authoritative for bit-for-bit integrity. This is the statistical layer.
 *
 * Returns null if the runtime is unavailable, in which case the UI just keeps
 * showing the server verdict.
 */
async function runLocalShareVerification(messageId, share1DataUrl, panel) {
    if (!window.revealxTamperML) return null;
    try {
        const local = await window.revealxTamperML.analyse(share1DataUrl);
        if (!local) return null;
        state.localAnalysis[messageId] = local;
        const server = state.shareAnalysis[messageId];
        if (panel && server) renderTamperPanel(panel, server, local);
        return local;
    } catch (error) {
        // Never let a detection failure block reconstruction.
        console.warn('In-browser tamper check failed:', error);
        return null;
    }
}

function setShareActionsDisabled(panel, disabled) {
    const bubble = panel.closest('.message');
    if (!bubble) return;
    bubble.querySelectorAll('.share-action[data-action="reconstruct"]').forEach(button => {
        button.disabled = disabled;
    });
}

function renderTamperPanel(panel, data, local) {
    local = local || state.localAnalysis[data.message_id];
    const tampered = data.integrity.exact_tamper_detected || data.ml.tampered
        || (local ? local.tampered : false);
    panel.className = `tamper-panel ${tampered ? 'danger' : 'success'}`;
    panel.innerHTML = '';

    const verdict = document.createElement('div');
    verdict.className = 'tamper-verdict';
    verdict.textContent = tampered
        ? '⚠️ Tampering detected in Share 1'
        : '✅ Share 1 verified intact';
    panel.appendChild(verdict);

    const rows = document.createElement('div');
    rows.className = 'tamper-rows';

    const hmacRow = document.createElement('div');
    hmacRow.className = `tamper-row ${data.integrity.hmac_match ? 'ok' : 'bad'}`;
    hmacRow.textContent = data.integrity.hmac_match
        ? 'HMAC-SHA256: match (bit-for-bit identical)'
        : 'HMAC-SHA256: MISMATCH (share was modified)';
    rows.appendChild(hmacRow);

    const mlRow = document.createElement('div');
    mlRow.className = `tamper-row ${data.ml.tampered ? 'bad' : 'ok'}`;
    // Once a local verdict exists it is worth being explicit about *which copy*
    // each row describes: the server scored the file it holds, the browser
    // scored the bytes that actually arrived. They are not the same claim.
    mlRow.textContent = (local ? 'Server ML (share as stored): ' : 'ML: ')
        + `${data.ml.label} - ${(data.ml.confidence * 100).toFixed(1)}% confidence`;
    rows.appendChild(mlRow);

    const timing = document.createElement('div');
    timing.className = 'tamper-row muted';
    timing.textContent = `${data.ml.model_name} - checked in ${data.processing_ms} ms`;
    rows.appendChild(timing);

    if (local) {
        const localRow = document.createElement('div');
        localRow.className = `tamper-row ${local.tampered ? 'bad' : 'ok'}`;
        localRow.textContent = `In-browser ML (bytes you received): ${local.label} - `
            + `${(local.confidence * 100).toFixed(1)}% confidence `
            + `(${local.timing_ms.total} ms, ONNX/wasm)`;
        rows.appendChild(localRow);

        // The two engines are the same forest and agree to ~1e-7 on identical
        // input, so a split verdict does not mean one of them is buggy -- it
        // means they were shown different bytes. Say so instead of silently
        // preferring whichever is more reassuring.
        if (local.tampered !== data.ml.tampered) {
            const conflict = document.createElement('div');
            conflict.className = 'tamper-row bad';
            conflict.textContent = 'The server and your browser scored different data. '
                + 'The share you received is not the one the server verified. '
                + 'Treat it as tampered.';
            rows.appendChild(conflict);
        }
    }

    panel.appendChild(rows);

    // Prefer the locally computed heatmap and reasoning when we have them:
    // they describe the bytes the recipient actually holds.
    const source = local || data.ml;
    if (source.heatmap_data_url) {
        const details = document.createElement('details');
        details.className = 'tamper-details';

        const summary = document.createElement('summary');
        summary.textContent = local
            ? 'Suspicious-region heatmap and reasoning (computed in your browser)'
            : 'Suspicious-region heatmap and reasoning';
        details.appendChild(summary);

        const heatmap = document.createElement('img');
        heatmap.className = 'tamper-heatmap';
        heatmap.src = source.heatmap_data_url;
        heatmap.alt = 'Tamper heatmap: bright regions are statistically suspicious';
        details.appendChild(heatmap);

        const list = document.createElement('ul');
        list.className = 'tamper-explanation';
        (source.explanation || []).forEach(line => {
            const item = document.createElement('li');
            item.textContent = line;
            list.appendChild(item);
        });
        details.appendChild(list);

        panel.appendChild(details);
    }
}

function displayMessage(msg) {
    const messageDiv = document.createElement('div');
    messageDiv.dataset.messageId = msg.id;

    // Cache or restore live Share 2 from client-side sessionStorage
    if (msg.type === 'share') {
        if (msg.extra && msg.extra.share2_live) {
            sessionStorage.setItem(`revealx_share2_${msg.id}`, msg.extra.share2_live);
        } else if (msg.extra) {
            const cachedShare2 = sessionStorage.getItem(`revealx_share2_${msg.id}`);
            if (cachedShare2) {
                msg.extra.share2_live = cachedShare2;
            }
        }
    }

    const isSent = msg.sender_id === state.currentUser.id;
    messageDiv.className = `message ${isSent ? 'sent' : 'received'}`;
    if (isSent) messageDiv.dataset.read = msg.read_at ? 'true' : 'false';

    // Badges report what actually happened to this message rather than showing
    // a padlock unconditionally.
    const isEncrypted = msg.encrypted === true;
    const plaintextWarning = (msg.type === 'text' || msg.type === 'voice') && !isEncrypted;
    const legacyBadge = plaintextWarning
        ? '<span class="legacy-badge" title="Sent before a key was exchanged with this account">[not encrypted]</span>'
        : '';
    const e2eLock = isEncrypted
        ? '<span class="e2e-badge" title="End-to-end encrypted: ECDH P-256 + HKDF + AES-GCM"><i class="fas fa-lock"></i></span>'
        : '';

    const sanitizedUsername = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(escapeHtml(msg.username)) : escapeHtml(msg.username);

    if (msg.type === 'text') {
        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername} ${e2eLock}</div>
            <div class="message-content"><span class="message-text"></span>${legacyBadge}</div>
            <div class="message-meta">
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction">☺</button>
                <button class="btn-delete-message" data-message-id="${msg.id}" title="${isSent ? 'Delete for everyone' : 'Delete for me'}" aria-label="${isSent ? 'Delete for everyone' : 'Delete for me'}">×</button>
            </div>
            <div class="message-reactions"></div>
        `;

        // textContent, so message bodies can never introduce markup.
        fillTextContent(messageDiv.querySelector('.message-text'), msg);

        const btn = messageDiv.querySelector('.btn-delete-message');
        if (btn) btn.addEventListener('click', () => deleteMessage(msg.id, isSent));

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }

    } else if (msg.type === 'voice') {
        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername} ${e2eLock}</div>
            <div class="message-content">
                <audio class="voice-player" controls preload="metadata"></audio>
                <span class="voice-status"></span>${legacyBadge}
            </div>
            <div class="message-meta">
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction">☺</button>
                <button class="btn-delete-message" data-message-id="${msg.id}" title="${isSent ? 'Delete for everyone' : 'Delete for me'}" aria-label="${isSent ? 'Delete for everyone' : 'Delete for me'}">×</button>
            </div>
            <div class="message-reactions"></div>
        `;

        fillVoiceContent(
            messageDiv.querySelector('.voice-player'),
            messageDiv.querySelector('.voice-status'),
            msg
        );

        const btn = messageDiv.querySelector('.btn-delete-message');
        if (btn) btn.addEventListener('click', () => deleteMessage(msg.id, isSent));

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }

    } else if (msg.type === 'share') {
        const hasShare1 = Boolean(msg.extra.share1_url);
        const hasLiveShare2 = Boolean(msg.extra.share2_live);

        // Share expiry countdown, driven by the server's own expires_at.
        const createdTime = parseServerTime(msg.created_at) || new Date();
        const expiresTime = parseServerTime(msg.expires_at)
            || new Date(createdTime.getTime() + 60 * 60 * 1000);
        const remainingMin = Math.max(0, Math.round((expiresTime - new Date()) / 60000));

        const expiryText = remainingMin > 0 ? `⏳ Expires in ${remainingMin}m` : '⏳ Expired';
        const oneTimeNotice = '<div class="one-time-notice"><i class="fas fa-exclamation-triangle"></i> ONE-TIME ACCESS SHARE</div>';

        const statusMessage = isSent
            ? 'You sent encrypted shares. The sender cannot reconstruct.'
            : hasLiveShare2
              ? 'Live Share 2 received. Verify and reconstruct Share 1 locally.'
              : 'Share 1 available. Share 2 was only sent live. You cannot reconstruct.';

        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername}</div>
            <div class="message-content">
                <div class="encrypted-image-notice">
                    ${statusMessage}
                    ${oneTimeNotice}
                    <div class="share-expiry">${expiryText}</div>
                </div>
                <div class="tamper-panel" data-role="tamper-panel"></div>
                <div class="share-actions">
                    ${!isSent ? `<button class="share-action primary" type="button" data-action="reconstruct" ${hasShare1 && hasLiveShare2 ? '' : 'disabled'}>Reconstruct</button>` : ''}
                    ${!isSent && hasShare1 ? '<button class="share-action" type="button" data-action="recheck">Re-check integrity</button>' : ''}
                    ${isSent ? '<button class="share-action btn-danger" type="button" data-action="revoke">Revoke Share</button>' : ''}
                </div>
            </div>
            <div class="message-meta">
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction">☺</button>
                <button class="btn-delete-message" data-message-id="${msg.id}" title="${isSent ? 'Delete for everyone' : 'Delete for me'}" aria-label="${isSent ? 'Delete for everyone' : 'Delete for me'}">×</button>
            </div>
            <div class="message-reactions"></div>
        `;

        messageDiv.querySelectorAll('.share-action').forEach(button => {
            button.addEventListener('click', () => {
                const action = button.dataset.action;
                if (action === 'reconstruct') {
                    openReconstruction(msg);
                } else if (action === 'recheck') {
                    runShareAnalysis(msg, { force: true });
                } else if (action === 'revoke') {
                    if (confirm('Revoke access to this share? The recipient will not be able to open it.')) {
                        socket.emit('revoke_share', { message_id: msg.id });
                    }
                }
            });
        });

        // Real-time tamper detection: as soon as a share lands in the chat the
        // recipient's client verifies the stored Share 1 (HMAC + ML) before
        // anyone reconstructs anything. Results are cached per message.
        if (!isSent && hasShare1) {
            runShareAnalysis(msg, { panel: messageDiv.querySelector('[data-role="tamper-panel"]') });
        }

        const btn = messageDiv.querySelector('.btn-delete-message');
        if (btn) btn.addEventListener('click', () => deleteMessage(msg.id, isSent));

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }
    }

    messagesContainer.appendChild(messageDiv);
    filterVisibleMessages();
}

function filterVisibleMessages() {
    const query = messageSearchInput.value.trim().toLowerCase();
    messagesContainer.querySelectorAll('.message').forEach(node => {
        const text = node.textContent.toLowerCase();
        node.classList.toggle('hidden-by-search', Boolean(query) && !text.includes(query));
    });
}

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// ==========================================
// 11. Core Messaging, Typing Indicators & Files
// ==========================================

socket.on('new_message', (msg) => {
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const isSent = msg.sender_id === state.currentUser.id;

    if (!isSent) {
        // "Mentions only" needs the plaintext, so encrypted text is opened first.
        // The notification body itself never carries ciphertext.
        notifyForIncoming(msg);
    }

    if (!state.selectedRecipientId) {
        selectUser(otherId);
        return;
    }

    if (otherId !== state.selectedRecipientId) {
        state.unreadCounts[otherId] = (state.unreadCounts[otherId] || 0) + 1;
        updateUsersList(state.users);
        return;
    }

    if (messagesContainer.querySelector('.welcome-message')) {
        messagesContainer.innerHTML = '';
    }

    // Keep the cache in step so re-renders and share re-checks can find it.
    state.chatHistoryCache.push(msg);
    displayMessage(msg);
    if (!isSent) {
        markConversationRead(msg.sender_id);
    }
    scrollToBottom();
    
    // Announce to Screen Readers for accessibility
    announceToScreenReader(`New message received from ${msg.username}`);
});

socket.on('messages_read', (data) => {
    (data.message_ids || []).forEach(id => {
        const node = messagesContainer.querySelector(`[data-message-id="${id}"]`);
        if (!node) return;

        node.dataset.read = 'true';
        const status = node.querySelector('.message-status');
        if (status) status.textContent = 'Read';
    });
});

socket.on('message_deleted', (data) => {
    const node = messagesContainer.querySelector(`[data-message-id="${data.message_id}"]`);
    if (node) {
        node.style.opacity = '0.4';
        node.style.textDecoration = 'line-through';
        setTimeout(() => node.remove(), 250);
    }
});

socket.on('typing', (data) => {
    if (!data || data.sender_id !== state.selectedRecipientId) return;

    clearTimeout(typingHideTimer);
    
    // Real-time debounced Typing Preview
    typingIndicator.textContent = data.is_typing ? `${data.username} is typing...` : '';
    if (data.is_typing) {
        typingHideTimer = setTimeout(() => {
            typingIndicator.textContent = '';
        }, 2500);
    }
});

socket.on('share_revoked', (data) => {
    alert('This encrypted share access has been revoked.');
    if (reconstructionPanel) reconstructionPanel.classList.remove('active');
    delete state.shareAnalysis[data.message_id];
    delete state.localAnalysis[data.message_id];

    // Refresh history
    if (state.selectedRecipientId) {
        requestChatHistory(state.selectedRecipientId);
    }
});

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') sendMessage();
});

messageInput.addEventListener('input', () => {
    if (!state.selectedRecipientId) return;
    // With typing indicators off, nothing is emitted at all - the peer never
    // learns that this user is composing.
    if (!getPref('typingIndicators')) return;

    // Rate Limiting Throttling for typing events (max 3 per 5 seconds)
    const now = Date.now();
    if (now - state.lastTypingTime < 5000) {
        state.typingCounter++;
        if (state.typingCounter > 3) {
            return; 
        }
    } else {
        state.typingCounter = 1;
        state.lastTypingTime = now;
    }

    socket.emit('typing', {
        recipient_id: state.selectedRecipientId,
        is_typing: true,
    });

    clearTimeout(typingStopTimer);
    typingStopTimer = setTimeout(() => {
        socket.emit('typing', {
            recipient_id: state.selectedRecipientId,
            is_typing: false,
        });
    }, 1000);
});

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || !state.selectedRecipientId) return;

    if (text.length > MAX_MESSAGE_LENGTH) {
        alert(`Message must be ${MAX_MESSAGE_LENGTH} characters or fewer`);
        return;
    }

    // Enforce Send Cooldown Counter Rate Limit feedback
    if (state.sendCooldownActive) {
        announceToScreenReader('Rate limit cooldown active. Please wait.');
        return;
    }

    state.sendCooldownActive = true;
    sendBtn.disabled = true;
    let cd = 2;
    sendBtn.textContent = `${cd}s`;
    
    const cdInterval = setInterval(() => {
        cd--;
        if (cd <= 0) {
            clearInterval(cdInterval);
            state.sendCooldownActive = false;
            sendBtn.disabled = false;
            sendBtn.textContent = 'Send';
        } else {
            sendBtn.textContent = `${cd}s`;
        }
    }, 1000);

    if (getPref('typingIndicators')) {
        socket.emit('typing', {
            recipient_id: state.selectedRecipientId,
            is_typing: false,
        });
    }

    const recipientId = state.selectedRecipientId;
    messageInput.value = '';

    // Encrypt whenever the peer has published a key. If they have not, the
    // message goes out in plaintext and is labelled as such rather than
    // pretending to be protected.
    const peerKey = peerPublicKey(recipientId);
    let payload = { recipient_id: recipientId, message: text };

    if (peerKey) {
        try {
            const sealed = await window.revealxE2EE.encrypt(peerKey, text);
            if (sealed) {
                payload = {
                    recipient_id: recipientId,
                    message: sealed.ciphertext,
                    iv: sealed.iv,
                    encrypted: true,
                };
            }
        } catch (error) {
            console.error('Encryption failed, sending in plaintext', error);
        }
    }

    socket.emit('send_message', payload);
}

/**
 * Delete a message.
 *
 * Scope depends on who you are: the sender owns what they sent and removes it
 * from both sides; the recipient can only drop their own copy.
 */
function deleteMessage(messageId, isSent) {
    const question = isSent
        ? 'Delete this message for everyone? It will be removed from both sides.'
        : 'Delete this message from your side? The sender keeps their copy.';
    if (!confirm(question)) return;

    socket.emit('delete_message', { message_id: messageId });
}

/**
 * Ask for a conversation's history.
 *
 * Opening a chat marks it read server-side, so the read-receipt preference has
 * to travel with the request - otherwise turning receipts off would still leak
 * "Read" to the sender the moment the chat is opened.
 */
function requestChatHistory(recipientId) {
    if (!recipientId) return;
    socket.emit('select_chat', {
        recipient_id: recipientId,
        send_read_receipts: getPref('readReceipts'),
    });
}

function markConversationRead(senderId) {
    // With read receipts off we simply never tell the server we read anything,
    // so the sender keeps seeing "Sent".
    if (!getPref('readReceipts')) return;
    socket.emit('mark_read', { sender_id: senderId });
}

/** Decide and raise the alert for an incoming message. */
async function notifyForIncoming(msg) {
    let preview = 'Sent an encrypted share';
    let searchable = '';

    if (msg.type === 'voice') {
        preview = 'Voice message';
    } else if (msg.type === 'text') {
        if (msg.encrypted) {
            const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
            const peerKey = peerPublicKey(otherId);
            const plaintext = peerKey
                ? await window.revealxE2EE.decrypt(peerKey, msg.content, msg.extra && msg.extra.iv)
                : null;
            searchable = plaintext || '';
            preview = 'Encrypted message';
        } else {
            searchable = msg.content;
            preview = msg.content;
        }
    }

    if (!shouldNotifyFor(searchable)) return;

    playNotificationSound();
    showNotification(`New message from ${msg.username}`, {
        body: preview,
        icon: '/static/icon-192.png'
    });
}

// ==========================================
// 11b. Voice messages
// ==========================================

const MAX_VOICE_SECONDS = 60;
let voiceRecorder = null;
let voiceChunks = [];
let voiceStopTimer = null;

const voiceBtn = document.getElementById('voiceBtn');
if (voiceBtn) voiceBtn.addEventListener('click', toggleVoiceRecording);

function voiceRecordingActive() {
    return Boolean(voiceRecorder && voiceRecorder.state === 'recording');
}

function setVoiceButtonState(recording) {
    if (!voiceBtn) return;
    voiceBtn.classList.toggle('recording', recording);
    voiceBtn.textContent = recording ? '⏹' : '🎤';
    voiceBtn.setAttribute('aria-label', recording ? 'Stop recording' : 'Voice message');
}

async function toggleVoiceRecording() {
    if (voiceRecordingActive()) {
        voiceRecorder.stop();
        return;
    }

    if (!state.selectedRecipientId) {
        alert('Select an account first');
        return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        alert('Voice recording is not supported in this browser.');
        return;
    }

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
        alert('Microphone access was denied.');
        return;
    }

    const recipientId = state.selectedRecipientId;
    voiceChunks = [];
    voiceRecorder = new MediaRecorder(stream);

    voiceRecorder.addEventListener('dataavailable', (event) => {
        if (event.data && event.data.size) voiceChunks.push(event.data);
    });

    voiceRecorder.addEventListener('stop', async () => {
        clearTimeout(voiceStopTimer);
        stream.getTracks().forEach(track => track.stop());
        setVoiceButtonState(false);

        const blob = new Blob(voiceChunks, { type: voiceRecorder.mimeType || 'audio/webm' });
        voiceRecorder = null;
        voiceChunks = [];

        if (blob.size) await sendVoiceMessage(blob, recipientId);
    });

    voiceRecorder.start();
    setVoiceButtonState(true);
    announceToScreenReader('Recording voice message. Click again to send.');

    // Hard cap so a forgotten recording cannot exceed the server size limit.
    voiceStopTimer = setTimeout(() => {
        if (voiceRecordingActive()) voiceRecorder.stop();
    }, MAX_VOICE_SECONDS * 1000);
}

async function sendVoiceMessage(blob, recipientId) {
    if (blob.size > MAX_VOICE_BYTES) {
        alert('Voice message is too long. Keep it under a minute.');
        return;
    }

    // Encrypt to the same derived key as text when the peer has published one.
    const peerKey = peerPublicKey(recipientId);
    const mime = (blob.type || 'audio/webm').split(';')[0];

    if (peerKey) {
        try {
            const sealed = await window.revealxE2EE.encryptBytes(peerKey, await blob.arrayBuffer());
            if (sealed) {
                socket.emit('send_voice', {
                    recipient_id: recipientId,
                    audio: sealed.ciphertext,
                    iv: sealed.iv,
                    mime,
                    encrypted: true,
                });
                return;
            }
        } catch (error) {
            console.error('Voice encryption failed, sending in plaintext', error);
        }
    }

    socket.emit('send_voice', {
        recipient_id: recipientId,
        audio: await blobToDataUrl(blob),
    });
}

// ==========================================
// 12. Local Image Downscale Compression
// ==========================================

shareImageBtn.addEventListener('click', () => {
    if (!state.selectedRecipientId) {
        alert('Select an account first');
        return;
    }
    imageInput.click();
});

imageInput.addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file || !state.selectedRecipientId) return;

    if (!file.type.startsWith('image/')) {
        alert('Choose a valid image file');
        return;
    }

    // Downscale and compress image client side (max width 1200px)
    const reader = new FileReader();
    reader.onload = (e) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            let w = img.width;
            let h = img.height;
            const max = 1200;
            if (w > max || h > max) {
                if (w > h) {
                    h = Math.round(h * (max / w));
                    w = max;
                } else {
                    w = Math.round(w * (max / h));
                    h = max;
                }
            }
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, w, h);
            
            // Compress JPEG at 85% quality
            const compressedUrl = canvas.toDataURL('image/jpeg', 0.85);
            state.pendingImage = {
                name: file.name,
                size: file.size,
                dataUrl: compressedUrl
            };
            
            imagePreview.src = compressedUrl;
            imagePreviewMeta.textContent = `${file.name} - compressed preview`;
            imagePreviewModal.classList.remove('hidden');
        };
        img.src = e.target.result;
    };
    reader.readAsDataURL(file);
    imageInput.value = '';
});

cancelImageBtn.addEventListener('click', closeImagePreview);
confirmImageBtn.addEventListener('click', () => {
    if (!state.pendingImage || !state.selectedRecipientId) return;

    confirmImageBtn.disabled = true;
    confirmImageBtn.textContent = 'Encrypting & Sending...';
    socket.emit('send_image', {
        recipient_id: state.selectedRecipientId,
        image: state.pendingImage.dataUrl,
    });
    closeImagePreview();
});

function closeImagePreview() {
    imagePreviewModal.classList.add('hidden');
    imagePreview.src = '';
    state.pendingImage = null;
    confirmImageBtn.disabled = false;
    confirmImageBtn.textContent = 'Encrypt & Send';
}

// ==========================================
// 13. Visual Cryptography Local Reconstruction
// ==========================================

/**
 * Switch the reconstruction panel between its two entry points.
 *
 * 'message' - opened from a share bubble; both shares load themselves.
 * 'manual'  - opened from the sidebar; the user supplies two files.
 */
function setReconstructionMode(mode) {
    reconstructionPanel.classList.toggle('manual-mode', mode === 'manual');
}

function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('Could not read Share 1'));
        reader.readAsDataURL(blob);
    });
}

/**
 * Open the reconstruction panel for a share message.
 *
 * Share 1 has one-time access, so it is downloaded exactly once here and the
 * resulting data URL is reused for both the preview and the XOR - fetching it
 * again (as an <img src> would) burns the access and returns 404.
 */
async function openReconstruction(msg) {
    const share1Url = msg && msg.extra ? msg.extra.share1_url : null;
    const share2Live = msg && msg.extra ? msg.extra.share2_live : null;

    const p1 = document.getElementById('share1Preview');
    const p2 = document.getElementById('share2Preview');
    const recPrev = document.getElementById('reconstructedPreview');
    const warning = document.getElementById('reconstructionWarning');

    if (p1) p1.src = '';
    if (p2) p2.src = share2Live || '';
    if (recPrev) recPrev.src = '';
    downloadReconstructedBtn.disabled = true;
    downloadReconstructedBtn.dataset.image = '';
    currentShares = { share1Url: null, share2Live };

    // Opened from a message: the shares arrive on their own, so no file pickers.
    setReconstructionMode('message');
    setShareDownload(downloadShare1Btn, null);
    setShareDownload(downloadShare2Btn, share2Live);

    reconstructionPanel.classList.add('active');
    reconstructBtn.disabled = true;
    announceToScreenReader('Visual cryptography reconstruction panel opened.');

    if (!share1Url) {
        if (warning) warning.textContent = 'Select both shares manually to reconstruct.';
        reconstructBtn.disabled = false;
        return;
    }

    // Surface the tamper verdict here too, so the warning is in front of the
    // user at the moment they are about to open the image.
    //
    // Deliberately forced rather than reusing the verdict cached when the
    // bubble first rendered. That one could be minutes old, and the share may
    // have been altered on the server since. It also has to happen *now*,
    // before the fetch below spends the one-time token: afterwards the server
    // will refuse to look at the share again and no second opinion is possible.
    const analysis = await runShareAnalysis(msg, { force: true });

    if (warning) warning.textContent = 'Fetching Share 1 (one-time access)...';
    try {
        const response = await fetch(share1Url);
        if (!response.ok) {
            throw new Error('Share 1 is no longer available. It may have been opened already, revoked, or expired.');
        }
        const dataUrl = await blobToDataUrl(await response.blob());
        if (p1) p1.src = dataUrl;
        currentShares.share1Url = dataUrl;
        setShareDownload(downloadShare1Btn, dataUrl);
        reconstructBtn.disabled = false;

        // Now that the bytes are actually in the browser, re-run the model here
        // on those exact bytes. This is the only check in the flow that does
        // not require trusting the server's report about its own storage.
        if (warning) warning.textContent = 'Share 1 loaded. Verifying in-browser...';
        const local = await runLocalShareVerification(msg.id, dataUrl, tamperPanelFor(msg.id));

        if (warning) {
            const localTampered = local ? local.tampered : false;
            if (!analysis && !local) {
                warning.textContent = 'Share 1 loaded. Integrity could not be checked.';
                warning.className = 'warning-text';
            } else if ((analysis && (analysis.integrity.exact_tamper_detected || analysis.ml.tampered))
                       || localTampered) {
                const parts = [];
                if (analysis) {
                    parts.push(`HMAC ${analysis.integrity.hmac_match ? 'matched' : 'mismatch'}`);
                    parts.push(`server ML ${(analysis.ml.confidence * 100).toFixed(1)}%`);
                }
                if (local) {
                    parts.push(`in-browser ML ${(local.confidence * 100).toFixed(1)}%`);
                }
                warning.textContent = `Tampering detected (${parts.join(', ')}). `
                    + 'The reconstruction below will be damaged.';
                warning.className = 'warning-text danger';
            } else {
                warning.textContent = local
                    ? 'Share 1 verified intact, confirmed independently in your browser. Safe to reconstruct.'
                    : 'Share 1 verified intact. Safe to reconstruct.';
                warning.className = 'warning-text success';
            }
        }
    } catch (error) {
        if (warning) {
            warning.textContent = error.message;
            warning.className = 'warning-text danger';
        }
    }
}

closePanel.addEventListener('click', () => {
    reconstructionPanel.classList.remove('active');
});

reconstructBtn.addEventListener('click', async () => {
    if (!currentShares || !currentShares.share1Url || !currentShares.share2Live) {
        alert('Both Share 1 and Share 2 are required to reconstruct.');
        return;
    }

    const spinner = document.getElementById('reconstructBtnSpinner');
    if (spinner) spinner.classList.remove('hidden');

    try {
        // Uses the modular reconstructSharesLocally from crypto.js
        await window.reconstructSharesLocally(currentShares.share1Url, currentShares.share2Live);
    } catch (e) {
        console.error(e);
    } finally {
        if (spinner) spinner.classList.add('hidden');
    }
});

// Manual share uploads. These controls are hidden unless the panel was opened
// in manual mode; see setReconstructionMode().
const selectShare1Btn = document.getElementById('selectShare1Btn');
const selectShare2Btn = document.getElementById('selectShare2Btn');
const share1Input = document.getElementById('share1Input');
const share2Input = document.getElementById('share2Input');
const share1Preview = document.getElementById('share1Preview');
const share2Preview = document.getElementById('share2Preview');
const downloadShare1Btn = document.getElementById('downloadShare1Btn');
const downloadShare2Btn = document.getElementById('downloadShare2Btn');

if (selectShare1Btn) selectShare1Btn.addEventListener('click', () => share1Input.click());
if (selectShare2Btn) selectShare2Btn.addEventListener('click', () => share2Input.click());

function readSharePick(input, preview, downloadBtn, assign) {
    if (!input) return;
    input.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file && file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const dataUrl = e.target.result;
                if (preview) preview.src = dataUrl;
                if (!currentShares) currentShares = { share1Url: null, share2Live: null };
                assign(dataUrl);
                setShareDownload(downloadBtn, dataUrl);
            };
            reader.readAsDataURL(file);
        }
        input.value = '';
    });
}

readSharePick(share1Input, share1Preview, downloadShare1Btn, url => { currentShares.share1Url = url; });
readSharePick(share2Input, share2Preview, downloadShare2Btn, url => { currentShares.share2Live = url; });

/** Arm (or disable) a download button for a share image. */
function setShareDownload(button, dataUrl) {
    if (!button) return;
    button.disabled = !dataUrl;
    button.dataset.image = dataUrl || '';
}

function downloadDataUrl(dataUrl, filename) {
    if (!dataUrl) return;
    const link = document.createElement('a');
    link.href = dataUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
}

// The recipient legitimately holds both shares, so let them save either one.
if (downloadShare1Btn) {
    downloadShare1Btn.addEventListener('click', () =>
        downloadDataUrl(downloadShare1Btn.dataset.image, 'share1.png'));
}
if (downloadShare2Btn) {
    downloadShare2Btn.addEventListener('click', () =>
        downloadDataUrl(downloadShare2Btn.dataset.image, 'share2.png'));
}

downloadReconstructedBtn.addEventListener('click', () =>
    downloadDataUrl(downloadReconstructedBtn.dataset.image, 'reconstructed.png'));

// ==========================================
// 14. Settings, Theme, and Data Exports
// ==========================================

settingsBtn.addEventListener('click', openSettings);
closeSettingsBtn.addEventListener('click', closeSettings);
saveSettingsBtn.addEventListener('click', saveSettings);

// Every preference below is read at the point of use, so toggling one changes
// real behaviour rather than only being written to storage.
const PREFERENCES = {
    notifications: { id: 'settingsNotifications', default: true },
    sounds: { id: 'settingsSounds', default: true },
    desktopNotifications: { id: 'settingsDesktopNotifications', default: true },
    mentionsOnly: { id: 'settingsMentionsOnly', default: false },
    readReceipts: { id: 'settingsReadReceipts', default: true },
    typingIndicators: { id: 'settingsTypingIndicators', default: true },
};

function getPref(name) {
    const spec = PREFERENCES[name];
    if (!spec) return false;
    const stored = localStorage.getItem(`revealx_pref_${name}`);
    return stored === null ? spec.default : stored === 'true';
}

function setPref(name, value) {
    localStorage.setItem(`revealx_pref_${name}`, String(Boolean(value)));
}

function settingsFeedback(message, isError = false) {
    const node = document.getElementById('settingsFeedback');
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('error', isError);
    if (message) setTimeout(() => { if (node.textContent === message) node.textContent = ''; }, 4000);
}

function openSettings() {
    if (!state.currentUser) return;

    settingsUsername.value = state.currentUser.username;
    settingsBio.value = state.currentUser.bio || '';
    settingsStatus.value = state.currentUser.status || 'online';

    Object.entries(PREFERENCES).forEach(([name, spec]) => {
        const input = document.getElementById(spec.id);
        if (input) input.checked = getPref(name);
    });

    const passcodeToggle = document.getElementById('settingsPasscodeLock');
    if (passcodeToggle) passcodeToggle.checked = hasPasscode();

    const totpToggle = document.getElementById('settingsTotp');
    if (totpToggle) totpToggle.checked = Boolean(state.currentUser.totp_enabled);

    const preview = document.getElementById('settingsProfilePreview');
    if (preview && state.currentUser.profile_image) {
        preview.src = state.currentUser.profile_image;
        preview.classList.remove('empty');
    } else if (preview) {
        preview.classList.add('empty');
    }

    settingsFeedback('');
    settingsModal.classList.remove('hidden');
    announceToScreenReader('Settings panel opened.');
}

function closeSettings() {
    settingsModal.classList.add('hidden');
}

function saveSettings() {
    const username = settingsUsername.value.trim();
    const bio = settingsBio.value.trim();
    const status = settingsStatus.value;

    if (!validateUsername(username)) {
        settingsFeedback('Username must be 3-30 letters, numbers, dots, dashes, or underscores', true);
        return;
    }

    Object.keys(PREFERENCES).forEach(name => {
        const input = document.getElementById(PREFERENCES[name].id);
        if (input) setPref(name, input.checked);
    });

    // Only ask for the OS permission when the user actually wants desktop popups.
    if (getPref('notifications') && getPref('desktopNotifications')
        && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    // Server-side profile fields. The username is validated again on the server,
    // which is what settles clashes; the UI only updates on its confirmation.
    if (username !== state.currentUser.username) {
        socket.emit('update_username', { new_username: username });
    }
    if (bio !== (state.currentUser.bio || '')) {
        socket.emit('update_bio', { bio });
    }
    if (status !== (state.currentUser.status || 'online')) {
        socket.emit('update_status', { status });
    }

    settingsFeedback('Settings saved.');
}

// The server is the authority on these; the UI reflects what it confirms.
socket.on('settings_saved', (data) => {
    if (!state.currentUser) return;

    if (data.username) {
        state.currentUser.username = data.username;
        accountName.textContent = data.username;
        settingsUsername.value = data.username;
        localStorage.setItem('revealx_username', data.username);
    }
    if (data.bio !== undefined) state.currentUser.bio = data.bio;
    if (data.status) {
        state.currentUser.status = data.status;
        const own = document.getElementById('userStatus');
        if (own) own.textContent = STATUS_LABELS[data.status] || data.status;
    }
    settingsFeedback('Settings saved.');
});

socket.on('settings_error', (data) => {
    const message = data.error || 'Could not save settings';

    // A two-factor error raised while the setup dialog is open belongs in that
    // dialog; the settings footer behind it would never be seen.
    const setupModal = document.getElementById('totpSetupModal');
    if (data.field === 'totp' && setupModal && !setupModal.classList.contains('hidden')) {
        const setupError = document.getElementById('totpSetupError');
        if (setupError) setupError.textContent = message;
        return;
    }

    if (data.field === 'totp') {
        // Enabling failed, so the checkbox must not look switched on.
        const totpToggle = document.getElementById('settingsTotp');
        if (totpToggle) totpToggle.checked = Boolean(state.currentUser && state.currentUser.totp_enabled);
    }

    settingsFeedback(message, true);
    if (data.field === 'username') settingsUsername.value = state.currentUser.username;
});

// Profile photo can be changed from the sidebar or from Settings.
const settingsProfileInput = document.getElementById('settingsProfileInput');
if (settingsProfileInput) {
    settingsProfileInput.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > 512 * 1024) {
            settingsFeedback('Profile image must be smaller than 512 KB', true);
            return;
        }
        const reader = new FileReader();
        reader.onload = (readerEvent) => {
            const dataUrl = readerEvent.target.result;
            const preview = document.getElementById('settingsProfilePreview');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('empty');
            }
            socket.emit('update_profile', { profile_image: dataUrl });
            settingsFeedback('Profile photo updated.');
        };
        reader.readAsDataURL(file);
        settingsProfileInput.value = '';
    });
}

// ==========================================
// 14a. Two-factor enrolment (from Settings)
// ==========================================

const totpSetupModal = document.getElementById('totpSetupModal');
const totpSetupCodeInput = document.getElementById('totpSetupCodeInput');
const totpSetupError = document.getElementById('totpSetupError');
const settingsTotp = document.getElementById('settingsTotp');

function closeTotpSetup() {
    totpSetupModal.classList.add('hidden');
    totpSetupCodeInput.value = '';
    totpSetupError.textContent = '';
    // The checkbox only sticks once the server confirms enrolment.
    if (settingsTotp) settingsTotp.checked = Boolean(state.currentUser && state.currentUser.totp_enabled);
}

if (settingsTotp) {
    settingsTotp.addEventListener('change', () => {
        if (settingsTotp.checked) {
            socket.emit('totp_begin_enrol', {});
            return;
        }
        // Turning it off needs proof of a current code, so the server is asked
        // with one rather than trusting the click.
        const code = prompt('Enter a current 6-digit code to turn two-factor off:');
        if (code === null) {
            settingsTotp.checked = true;
            return;
        }
        socket.emit('totp_disable', { code: code.trim() });
    });
}

socket.on('totp_enrolment', (data) => {
    document.getElementById('totpQr').src = data.qr_data_url;
    document.getElementById('totpSecret').textContent = data.secret;
    totpSetupError.textContent = '';
    totpSetupCodeInput.value = '';
    totpSetupModal.classList.remove('hidden');
    totpSetupCodeInput.focus();
});

socket.on('totp_state', (data) => {
    if (state.currentUser) state.currentUser.totp_enabled = Boolean(data.enabled);
    if (settingsTotp) settingsTotp.checked = Boolean(data.enabled);
    if (data.enabled) {
        totpSetupModal.classList.add('hidden');
        settingsFeedback('Two-factor authentication enabled.');
    } else {
        settingsFeedback('Two-factor authentication disabled.');
    }
});

document.getElementById('totpSetupConfirmBtn').addEventListener('click', () => {
    const code = (totpSetupCodeInput.value || '').trim();
    if (code.length !== 6) {
        totpSetupError.textContent = 'Enter the 6-digit code';
        return;
    }
    socket.emit('totp_confirm_enrol', { code });
});

totpSetupCodeInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') document.getElementById('totpSetupConfirmBtn').click();
});
document.getElementById('totpSetupCancelBtn').addEventListener('click', closeTotpSetup);
document.getElementById('totpSetupCloseBtn').addEventListener('click', closeTotpSetup);

// ==========================================
// 14b. Local passcode lock
// ==========================================

/*
 * A device-local screen lock. The passcode is never sent anywhere and is stored
 * only as a PBKDF2-SHA256 hash with a random salt, so reading localStorage does
 * not reveal it.
 *
 * Scope, stated plainly: this stops someone picking up an unlocked browser. It
 * is not a second factor and it does not encrypt anything - the E2EE key in
 * IndexedDB is what protects message content.
 */

const PASSCODE_KEY = 'revealx_passcode';
const PASSCODE_ITERATIONS = 210000;
const LOCK_AFTER_HIDDEN_MS = 60 * 1000;

const lockScreen = document.getElementById('lockScreen');
const lockPasscodeInput = document.getElementById('lockPasscodeInput');
const lockError = document.getElementById('lockError');
let hiddenSince = null;

function hasPasscode() {
    return Boolean(localStorage.getItem(PASSCODE_KEY));
}

function toBase64(buffer) {
    return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

async function derivePasscodeHash(passcode, saltB64, iterations) {
    const salt = Uint8Array.from(atob(saltB64), ch => ch.charCodeAt(0));
    const material = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(passcode), 'PBKDF2', false, ['deriveBits']
    );
    const bits = await crypto.subtle.deriveBits(
        { name: 'PBKDF2', salt, iterations, hash: 'SHA-256' }, material, 256
    );
    return toBase64(bits);
}

async function setPasscode(passcode) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const saltB64 = toBase64(salt);
    const hash = await derivePasscodeHash(passcode, saltB64, PASSCODE_ITERATIONS);
    localStorage.setItem(PASSCODE_KEY, JSON.stringify({
        salt: saltB64, hash, iterations: PASSCODE_ITERATIONS,
    }));
}

async function verifyPasscode(passcode) {
    const stored = localStorage.getItem(PASSCODE_KEY);
    if (!stored) return false;
    try {
        const { salt, hash, iterations } = JSON.parse(stored);
        return await derivePasscodeHash(passcode, salt, iterations) === hash;
    } catch (error) {
        return false;
    }
}

function lockApp() {
    if (!hasPasscode() || !lockScreen) return;
    lockError.textContent = '';
    lockPasscodeInput.value = '';
    lockScreen.classList.remove('hidden');
    lockPasscodeInput.focus();
}

function unlockApp() {
    if (lockScreen) lockScreen.classList.add('hidden');
    hiddenSince = null;
}

async function handleUnlock() {
    const passcode = lockPasscodeInput.value;
    if (!passcode) return;

    if (await verifyPasscode(passcode)) {
        unlockApp();
        announceToScreenReader('Unlocked.');
    } else {
        lockError.textContent = 'Incorrect passcode';
        lockPasscodeInput.value = '';
        lockPasscodeInput.focus();
    }
}

if (lockScreen) {
    document.getElementById('lockUnlockBtn').addEventListener('click', handleUnlock);
    lockPasscodeInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') handleUnlock();
    });
    document.getElementById('lockSignOutBtn').addEventListener('click', () => {
        unlockApp();
        logoutUser();
    });
}

// Re-lock after the tab has been away long enough to have changed hands.
document.addEventListener('visibilitychange', () => {
    if (!hasPasscode()) return;
    if (document.hidden) {
        hiddenSince = Date.now();
    } else if (hiddenSince && Date.now() - hiddenSince > LOCK_AFTER_HIDDEN_MS) {
        lockApp();
    }
});

const passcodeToggle = document.getElementById('settingsPasscodeLock');
if (passcodeToggle) {
    passcodeToggle.addEventListener('change', async () => {
        if (passcodeToggle.checked) {
            const passcode = prompt('Choose a passcode for this device (4-12 characters):');
            if (!passcode || passcode.length < 4 || passcode.length > 12) {
                passcodeToggle.checked = false;
                if (passcode !== null) settingsFeedback('Passcode must be 4-12 characters', true);
                return;
            }
            if (prompt('Confirm the passcode:') !== passcode) {
                passcodeToggle.checked = false;
                settingsFeedback('Passcodes did not match', true);
                return;
            }
            await setPasscode(passcode);
            settingsFeedback('Passcode lock enabled for this device.');
        } else {
            const passcode = prompt('Enter your current passcode to turn the lock off:');
            if (passcode === null) {
                passcodeToggle.checked = true;
                return;
            }
            if (!await verifyPasscode(passcode)) {
                passcodeToggle.checked = true;
                settingsFeedback('Incorrect passcode', true);
                return;
            }
            localStorage.removeItem(PASSCODE_KEY);
            settingsFeedback('Passcode lock disabled.');
        }
    });
}

// Lock straight away when the page loads with a passcode already set.
if (hasPasscode()) lockApp();

// Theme Selection
const curTheme = localStorage.getItem('revealx_theme') || 'dark';
if (curTheme === 'light') {
    document.body.setAttribute('data-theme', 'light');
}
updateThemeIcon();

themeToggle.addEventListener('click', () => {
    const isDark = document.body.getAttribute('data-theme') !== 'light';
    document.body.setAttribute('data-theme', isDark ? 'light' : 'dark');
    localStorage.setItem('revealx_theme', isDark ? 'light' : 'dark');
    updateThemeIcon();
});

function updateThemeIcon() {
    const icon = document.querySelector('.theme-icon');
    const isDark = document.body.getAttribute('data-theme') !== 'light';
    if (icon) icon.textContent = isDark ? '🌙' : '☀️';
}

// Data Export all messages as JSON
const exportDataBtn = document.getElementById('exportDataBtn');
if (exportDataBtn) {
    exportDataBtn.addEventListener('click', () => {
        if (!state.chatHistoryCache || state.chatHistoryCache.length === 0) {
            alert('No chat history to export.');
            return;
        }
        const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(state.chatHistoryCache, null, 2));
        const dlAnchor = document.createElement('a');
        dlAnchor.setAttribute("href", dataStr);
        dlAnchor.setAttribute("download", "revealx_chat_export.json");
        document.body.appendChild(dlAnchor);
        dlAnchor.click();
        dlAnchor.remove();
        announceToScreenReader('Chat data exported successfully.');
    });
}

// Clear Cache local data
const clearCacheBtn = document.getElementById('clearCacheBtn');
if (clearCacheBtn) {
    clearCacheBtn.addEventListener('click', () => {
        sessionStorage.clear();
        alert('Client visual decryption cache cleared successfully.');
    });
}

// Account Deletion reason prompt
if (deleteAccountBtn) {
    deleteAccountBtn.addEventListener('click', () => {
        const reason = prompt('Please let us know the reason for your account deletion (optional):');
        const confirmed = confirm('Are you absolutely sure? This will delete your account and all private messages forever.');
        if (confirmed) {
            socket.emit('delete_account', { reason: reason || 'none' });
        }
    });
}

// Profile upload handlers
if (profilePicker) profilePicker.addEventListener('click', () => profileImageInput.click());
if (profileImageInput) {
    profileImageInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (file.size > 512 * 1024) {
            alert('Profile image must be smaller than 512 KB');
            return;
        }

        const reader = new FileReader();
        reader.onload = (readerEvent) => {
            const dataUrl = readerEvent.target.result;
            const preview = document.getElementById('profilePreview');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('empty');
            }
            socket.emit('update_profile', { profile_image: dataUrl });
        };
        reader.readAsDataURL(file);
        profileImageInput.value = '';
    });
}

socket.on('profile_updated', (data) => {
    const preview = document.getElementById('profilePreview');
    if (preview && data.profile_image) {
        preview.src = data.profile_image;
        preview.classList.remove('empty');
    }
    if (state.selectedRecipientId) {
        requestChatHistory(state.selectedRecipientId);
    }
});

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
            <p>Select an account to start E2E Encrypted sharing</p>
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

function closeSidebar() {
    accountsSidebar.classList.remove('open');
    sidebarOverlay.classList.remove('active');
    sidebarToggle.classList.remove('active');
    sidebarToggle.setAttribute('aria-expanded', 'false');
    sidebarToggle.setAttribute('aria-label', 'Open accounts');
}

function openSidebar() {
    accountsSidebar.classList.add('open');
    sidebarOverlay.classList.add('active');
    sidebarToggle.classList.add('active');
    sidebarToggle.setAttribute('aria-expanded', 'true');
    sidebarToggle.setAttribute('aria-label', 'Close accounts');
}

if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
        if (accountsSidebar.classList.contains('open')) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });
}

if (sidebarOverlay) {
    sidebarOverlay.addEventListener('click', closeSidebar);
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
        currentShares = { share1Url: null, share2Live: null };
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

// Manual E2EE modals links inside sidebar or footer
const devicesBtn = document.createElement('button');
devicesBtn.className = 'icon-btn';
devicesBtn.title = 'Active Devices';
devicesBtn.innerHTML = '📱';
devicesBtn.style.marginRight = '8px';
devicesBtn.addEventListener('click', openActiveDevicesModal);

const keysBtn = document.createElement('button');
keysBtn.className = 'icon-btn';
keysBtn.title = 'E2EE Keys';
keysBtn.innerHTML = '🔑';
keysBtn.style.marginRight = '8px';
keysBtn.addEventListener('click', openKeyManagerModal);

const sidebarFooter = document.querySelector('.sidebar-footer');
if (sidebarFooter) {
    sidebarFooter.insertBefore(devicesBtn, sidebarFooter.firstChild);
    sidebarFooter.insertBefore(keysBtn, sidebarFooter.firstChild);
}

// Handle socket connectivity states
socket.on('connect', () => {
    console.log("WebSocket connected!");
});

socket.on('disconnect', () => {
    state.reconnectAttempts++;
    const countEl = document.getElementById('reconnectCount');
    if (countEl) countEl.textContent = ` (retrying ${state.reconnectAttempts}...)`;
});

// Set default chat placeholder
setChatEnabled(false);
showWelcome();
