// State, the socket, DOM references and the in-memory Share 2 store.
// Split out of chat.js; main.js loads the modules in order.

import { late } from './late.js';

// Reveal-X Chat Application - Client Side
// [MODIFIED] Fully refactored under modular organization with modern feature sets

// socket.io is vendored under app/static/vendor/socketio rather than loaded
// from cdn.socket.io, for the same reason onnxruntime-web is: a blocking
// third-party script in the critical path is a single point of failure the
// deployment does not control.
//
// It was a CDN script, and this line had no fallback. A slow, blocked or
// failed CDN left `io` undefined, threw here at the top of the file, and
// stopped every handler below from ever binding -- so the page rendered
// normally and then responded to nothing at all, with no error shown. The
// only way out was a hard refresh, which bypasses the cache and the service
// worker and fetches the script cleanly.
//
// Vendoring removes the dependency. This guard makes whatever is left of that
// failure visible instead of silent.
if (typeof io === 'undefined') {
    const reveal = () => {
        const banner = document.getElementById('startupFailure');
        if (banner) banner.classList.remove('hidden');
        const reload = document.getElementById('startupFailureReload');
        if (reload) reload.addEventListener('click', () => window.location.reload());
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', reveal);
    } else {
        reveal();
    }
    throw new Error('socket.io did not load; Reveal-X cannot start');
}

export const socket = io();

// State Container
export const state = {
    currentUser: null,
    selectedRecipientId: null,
    users: [],
    userSearch: '',
    unreadCounts: {},
    pendingImage: null,
    keys: { publicKey: '' },
    keyExchangeStatus: {}, // {userId: 'ready' | 'pending' | 'unavailable'}
    // {userId: {previous, current}} for peers whose published key no longer
    // matches the one pinned on this device. Sending to them is blocked until
    // the change is accepted in the safety-number dialog.
    keyChanges: new Map(),
    shareAnalysis: {},     // {messageId: integrity + ML result}
    localAnalysis: {},     // {messageId: verdict computed here in the browser}
    // {messageId: {matches, expected, actual}}: SHA-256 of the Share 1 bytes
    // that arrived, against the one the sender sealed into the envelope.
    endToEndHash: {},
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
    reconnectAttempts: 0,
    // Issued at sign-in and held in memory only -- never web storage, which
    // any script on the origin can read and which outlives the tab. A reload
    // therefore means signing in again; reload survival would need an
    // HttpOnly cookie and is deliberately not done here.
    resumeToken: null,
    resuming: false,
    hasConnected: false,
    // Set when the server answered auth_required and the session could not
    // be resumed; the connection banner says "Signed out" until a sign-in.
    signedOut: false,
};

// UI Element Mappings
export const messagesContainer = document.getElementById('messagesContainer');
export const messageInput = document.getElementById('messageInput');
export const sendBtn = document.getElementById('sendBtn');
export const usersList = document.getElementById('usersList');
export const userCount = document.getElementById('userCount');
export const imageInput = document.getElementById('imageInput');
export const shareImageBtn = document.getElementById('shareImageBtn');
export const reconstructionPanel = document.getElementById('reconstructionPanel');
export const closePanel = document.getElementById('closePanel');
export const reconstructBtn = document.getElementById('reconstructBtn');
export const downloadReconstructedBtn = document.getElementById('downloadReconstructedBtn');
export const authModal = document.getElementById('authModal');
export const usernameInput = document.getElementById('usernameInput');
export const passwordInput = document.getElementById('passwordInput');
export const loginBtn = document.getElementById('loginBtn');
export const registerBtn = document.getElementById('registerBtn');
export const authError = document.getElementById('authError');
export const accountName = document.getElementById('accountName');
export const chatTitle = document.getElementById('chatTitle');
export const chatSubtitle = document.getElementById('chatSubtitle');
export const messageSearchInput = document.getElementById('messageSearchInput');
export const sidebarToggle = document.getElementById('sidebarToggle');
export const sidebarOverlay = document.getElementById('sidebarOverlay');
export const accountsSidebar = document.getElementById('accountsSidebar');
export const userSearchInput = document.getElementById('userSearchInput');
export const typingIndicator = document.getElementById('typingIndicator');
export const imagePreviewModal = document.getElementById('imagePreviewModal');
export const imagePreview = document.getElementById('imagePreview');
export const imagePreviewMeta = document.getElementById('imagePreviewMeta');
export const cancelImageBtn = document.getElementById('cancelImageBtn');
export const confirmImageBtn = document.getElementById('confirmImageBtn');

// Key manager and modal links
export const settingsBtn = document.getElementById('settingsBtn');
export const settingsModal = document.getElementById('settingsModal');
export const closeSettingsBtn = document.getElementById('closeSettingsBtn');
export const saveSettingsBtn = document.getElementById('saveSettingsBtn');
export const settingsUsername = document.getElementById('settingsUsername');
export const settingsBio = document.getElementById('settingsBio');
export const settingsStatus = document.getElementById('settingsStatus');
const settingsNotifications = document.getElementById('settingsNotifications');
export const themeToggle = document.getElementById('themeToggle');
const reconstructImageBtn = document.getElementById('reconstructImageBtn');
export const logoutBtn = document.getElementById('logoutBtn');
export const deleteAccountBtn = document.getElementById('deleteAccountBtn');
export const profileImageInput = document.getElementById('profileImageInput');
export const profilePicker = document.querySelector('.profile-picker');


// Share 2, keyed by message id, held in memory and nowhere else.
//
// It used to be written to sessionStorage as `revealx_share2_<id>` so it would
// survive a reload. sessionStorage is readable by any script on the origin and
// outlives logout for as long as the tab stays open, so the next person at the
// keyboard could read Share 2 back out of the console -- the one secret the
// server is forbidden from storing (FORBIDDEN_EXTRA_KEYS), kept by the client
// instead. The cost of memory is that a reload drops Share 2; that is the
// same guarantee the server gives, applied to the browser as well.
export const share2Memory = new Map();

export function forgetShare2(messageId) {
    if (messageId === undefined || messageId === null) return;
    share2Memory.delete(String(messageId));
    // The opened envelope of a browser-split share holds Share 2 as well.
    share2Memory.delete(`${messageId}#envelope`);
}

/** Is this a share the sender's browser split (Share 2 in an E2EE envelope)? */
export function isClientSplitShare(msg) {
    return Boolean(msg && msg.type === 'share' && msg.extra && msg.extra.v === 2 && msg.content);
}

/**
 * Decrypt and check the envelope of a browser-split share. Kept in
 * share2Memory, like every other copy of Share 2, so logout, Clear cache,
 * delete and revoke drop it. Returns the envelope or null.
 */
export async function openShareEnvelope(msg) {
    const key = `${msg.id}#envelope`;
    if (share2Memory.has(key)) return share2Memory.get(key);
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = late.peerPublicKey(otherId);
    if (!peerKey) return null;
    const text = await window.revealxE2EE.decrypt(peerKey, msg.content, msg.extra.iv, late.e2eeContext(msg));
    if (text === null) return null;
    const envelope = window.revealxVC.openEnvelope(text);
    share2Memory.set(key, envelope);
    share2Memory.set(String(msg.id), `data:image/png;base64,${envelope.share2_png_b64}`);
    return envelope;
}

// A tab open across the upgrade still holds what the old build wrote.
try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
        const key = sessionStorage.key(i);
        if (key && key.startsWith('revealx_share2_')) sessionStorage.removeItem(key);
    }
} catch (error) {
    // Storage can be blocked outright; then there is nothing to purge.
}
export const STATUS_LABELS = {
    online: 'Online',
    away: 'Away',
    dnd: 'Do Not Disturb',
    offline: 'Offline',
};

export const MAX_MESSAGE_LENGTH = 2000;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
export const MAX_VOICE_BYTES = 2 * 1024 * 1024;

// Set default username value
usernameInput.value = localStorage.getItem('revealx_username') || '';

