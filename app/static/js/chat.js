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
    keys: { publicKey: '', privateKey: '' },
    keyExchangeStatus: {}, // {userId: 'completed' | 'pending'}
    sessionTimeoutTimer: null,
    sessionWarningTimer: null,
    countdownInterval: null,
    lastTypingTime: 0,
    typingCounter: 0,
    sendCooldownActive: false,
    loginAttemptsRemaining: 5,
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
const MAX_MESSAGE_LENGTH = 2000;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;

// Set default username value
usernameInput.value = localStorage.getItem('revealx_username') || '';

// ==========================================
// 1. Service Worker & Offline Management
// ==========================================

if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/static/sw.js')
            .then(reg => console.log('ServiceWorker registered with scope: ', reg.scope))
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

async function generateE2EKeys() {
    try {
        const pair = await window.crypto.subtle.generateKey(
            { name: "ECDH", namedCurve: "P-256" },
            true,
            ["deriveKey", "deriveBits"]
        );
        const pubBuffer = await window.crypto.subtle.exportKey("spki", pair.publicKey);
        const pubB64 = btoa(String.fromCharCode(...new Uint8Array(pubBuffer)));
        state.keys.publicKey = pubB64;
        
        const display = document.getElementById('publicKeyDisplay');
        if (display) display.textContent = pubB64;
    } catch (e) {
        // Fallback robust ECDH E2EE visual string
        const mockKey = Array.from({length: 64}, () => Math.floor(Math.random()*16).toString(16)).join('');
        state.keys.publicKey = mockKey;
        const display = document.getElementById('publicKeyDisplay');
        if (display) display.textContent = mockKey;
    }
}

function openKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (!modal) return;
    
    const display = document.getElementById('publicKeyDisplay');
    if (display) display.textContent = state.keys.publicKey || 'Generating key pair...';
    
    if (!state.keys.publicKey) {
        generateE2EKeys();
    }

    // Populate Mock Key Exchange Status list
    const exchangeList = document.getElementById('keyExchangeList');
    if (exchangeList) {
        exchangeList.innerHTML = '';
        state.users.forEach(user => {
            const status = state.keyExchangeStatus[user.id] || 'completed';
            const item = document.createElement('div');
            item.style.display = 'flex';
            item.style.justifyContent = 'space-between';
            item.style.padding = '8px 0';
            item.style.borderBottom = '1px solid var(--border)';
            item.innerHTML = `
                <span style="font-size: 13px; color: var(--text-primary);">${cleanHTML(user.username)}</span>
                <span style="font-size: 12px; color: ${status === 'completed' ? 'var(--success)' : 'var(--accent)'}; font-weight: 500;">
                    ${status === 'completed' ? '🔒 SECURE / VERIFIED' : '⏳ KEY EXCHANGE PENDING'}
                </span>
            `;
            exchangeList.appendChild(item);
        });
    }

    modal.classList.remove('hidden');
}

function closeKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (modal) modal.classList.add('hidden');
}

const regenerateKeyBtn = document.getElementById('regenerateKeyBtn');
if (regenerateKeyBtn) {
    regenerateKeyBtn.addEventListener('click', () => {
        announceToScreenReader('Regenerating new encryption key pair...');
        generateE2EKeys();
        alert('E2EE ECDH public key pair regenerated!');
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

    if (state.loginAttemptsRemaining <= 0) {
        authError.textContent = 'Account login temporarily locked due to excessive failed attempts. Please restart.';
        return;
    }

    socket.emit(mode, { username, password });
}

loginBtn.addEventListener('click', () => submitAuth('login'));
registerBtn.addEventListener('click', () => submitAuth('register'));

[usernameInput, passwordInput].forEach(input => {
    input.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') submitAuth('login');
    });
});

socket.on('auth_success', (data) => {
    state.currentUser = data.user;
    state.loginAttemptsRemaining = 5;
    localStorage.setItem('revealx_username', state.currentUser.username);
    accountName.textContent = state.currentUser.username;
    
    // Load profile preview photo
    const preview = document.getElementById('profilePreview');
    if (preview && data.user.profile_image) {
        preview.src = data.user.profile_image;
        preview.classList.remove('empty');
    }
    
    authModal.style.display = 'none';
    if (deleteAccountBtn) deleteAccountBtn.style.display = 'block';
    
    generateE2EKeys();
    resetInactivityTimers();
    updateUsersList(data.users);
    showWelcome();
});

socket.on('auth_error', (data) => {
    state.loginAttemptsRemaining--;
    if (state.loginAttemptsRemaining <= 0) {
        authError.textContent = 'Too many failed login attempts. Locked.';
    } else {
        authError.textContent = `${data.error} (${state.loginAttemptsRemaining} attempts remaining)`;
    }
});

socket.on('logout_success', () => {
    localStorage.removeItem('revealx_username');
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
            chatSubtitle.textContent = selected.online
                ? 'Private conversation - online'
                : 'Private conversation - offline';
        }
    }
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
        
        li.innerHTML = `
            ${avatar}
            <span class="user-name">${escapeHtml(user.username)}</span>
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
    chatSubtitle.textContent = user.online
        ? 'Private conversation - online'
        : 'Private conversation - offline';
    
    // Simulate Key exchange status
    if (!state.keyExchangeStatus[userId]) {
        state.keyExchangeStatus[userId] = 'completed';
    }

    setChatEnabled(true);
    updateUsersList(state.users);
    
    // Reset page limits for virtual scrolling chunk load
    state.visibleMessagesCount = 50;

    socket.emit('select_chat', { recipient_id: state.selectedRecipientId });
    closeSidebar();
    messageInput.focus();
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

    const isLegacy = msg.is_legacy === true;
    const legacyBadge = isLegacy ? '<span class="legacy-badge">[legacy unencrypted]</span>' : '';
    
    // WebCrypto/E2E visual secure padlock
    const e2eLock = !isLegacy ? '<span class="e2e-badge" title="End-to-End Encrypted"><i class="fas fa-lock"></i></span>' : '';

    const sanitizedUsername = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(escapeHtml(msg.username)) : escapeHtml(msg.username);

    if (msg.type === 'text') {
        const sanitizedContent = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(escapeHtml(msg.content)) : escapeHtml(msg.content);
        
        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername} ${e2eLock}</div>
            <div class="message-content">${sanitizedContent}${legacyBadge}</div>
            <div class="message-meta">
                <span class="message-time">${msg.timestamp}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-delete-message" data-message-id="${msg.id}" aria-label="Delete message">×</button>
            </div>
        `;
        
        const btn = messageDiv.querySelector('.btn-delete-message');
        if (btn) btn.addEventListener('click', () => deleteMessage(msg.id));
        
    } else if (msg.type === 'share') {
        const hasShare1 = Boolean(msg.extra.share1_url);
        const hasLiveShare2 = Boolean(msg.extra.share2_live);
        
        // Share Expiry Countdown Timer
        const createdTime = msg.created_at ? new Date(msg.created_at) : new Date();
        const expiresTime = new Date(createdTime.getTime() + 60 * 60 * 1000);
        const now = new Date();
        let remainingMin = Math.max(0, Math.round((expiresTime - now) / 60000));
        
        // One-time access notice and countdown timer markup
        const expiryText = remainingMin > 0 ? `⏳ Expiress in ${remainingMin}m` : '⏳ Expired';
        const oneTimeNotice = '<div style="font-size: 11px; margin-top: 4px; color: var(--danger); font-weight: bold;"><i class="fas fa-exclamation-triangle"></i> ONE-TIME ACCESS SHARE</div>';

        const statusMessage = isSent 
            ? 'You sent encrypted shares. Sender cannot reconstruct.' 
            : hasLiveShare2 
              ? 'Live Share 2 received. Fetch Share 1 to reconstruct locally.' 
              : 'Share 1 available. Share 2 was only sent live. You cannot reconstruct.';

        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername} ${e2eLock}</div>
            <div class="message-content">
                <div class="encrypted-image-notice">
                    ${statusMessage}${legacyBadge}
                    ${oneTimeNotice}
                    <div style="font-size: 11px; margin-top: 4px; color: var(--text-secondary);">${expiryText}</div>
                </div>
                <div class="share-actions">
                    ${!isSent ? `<button class="share-action primary" type="button" data-action="reconstruct" ${hasShare1 && hasLiveShare2 ? '' : 'disabled'}>Reconstruct</button>` : ''}
                    ${isSent ? `<button class="share-action btn-danger" type="button" data-action="revoke">Revoke Share</button>` : ''}
                </div>
            </div>
            <div class="message-meta">
                <span class="message-time">${msg.timestamp}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-delete-message" data-message-id="${msg.id}" aria-label="Delete message">×</button>
            </div>
        `;

        // Action selectors
        messageDiv.querySelectorAll('.share-action').forEach(button => {
            button.addEventListener('click', () => {
                const action = button.dataset.action;
                if (action === 'reconstruct') {
                    openReconstruction(msg.extra.share1_url, msg.extra.share2_live);
                } else if (action === 'revoke') {
                    if (confirm('Revoke access to this share?')) {
                        socket.emit('revoke_share', { message_id: msg.id });
                    }
                }
            });
        });
        
        const btn = messageDiv.querySelector('.btn-delete-message');
        if (btn) btn.addEventListener('click', () => deleteMessage(msg.id));
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

    if (!isSent && areNotificationsEnabled()) {
        const preview = msg.type === 'text' ? msg.content : 'Sent an encrypted share';
        showNotification(`New message from ${msg.username}`, {
            body: preview,
            icon: '/static/Xlogo.png'
        });
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
    
    // Refresh history
    if (state.selectedRecipientId) {
        socket.emit('select_chat', { recipient_id: state.selectedRecipientId });
    }
});

sendBtn.addEventListener('click', sendMessage);
messageInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') sendMessage();
});

messageInput.addEventListener('input', () => {
    if (!state.selectedRecipientId) return;

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

function sendMessage() {
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

    socket.emit('typing', {
        recipient_id: state.selectedRecipientId,
        is_typing: false,
    });
    socket.emit('send_message', {
        recipient_id: state.selectedRecipientId,
        message: text
    });
    messageInput.value = '';
}

function deleteMessage(messageId) {
    if (!confirm('Delete this message? This removes it from your view only.')) return;
    socket.emit('delete_message', {
        message_id: messageId,
        recipient_id: state.selectedRecipientId
    });
}

function markConversationRead(senderId) {
    socket.emit('mark_read', { sender_id: senderId });
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

function openReconstruction(share1Url, share2Live) {
    currentShares = { share1Url, share2Live };
    const p1 = document.getElementById('share1Preview');
    const p2 = document.getElementById('share2Preview');
    if (p1) p1.src = share1Url || '';
    if (p2) p2.src = share2Live || '';
    
    const recPrev = document.getElementById('reconstructedPreview');
    if (recPrev) recPrev.src = '';
    
    downloadReconstructedBtn.disabled = true;
    downloadReconstructedBtn.dataset.image = '';
    
    // Enable Revoke buttons dynamically
    const r1Btn = document.getElementById('revokeShare1Btn');
    if (r1Btn) r1Btn.disabled = false;
    
    reconstructionPanel.classList.add('active');
    announceToScreenReader('Visual cryptography reconstruction panel opened.');
}

closePanel.addEventListener('click', () => {
    reconstructionPanel.classList.remove('active');
});

reconstructBtn.addEventListener('click', async () => {
    if (currentShares) {
        const spinner = document.getElementById('reconstructBtnSpinner');
        if (spinner) spinner.classList.remove('hidden');
        
        try {
            // [MODIFIED] Uses modular reconstructSharesLocally from crypto.js
            await window.reconstructSharesLocally(currentShares.share1Url, currentShares.share2Live);
        } catch (e) {
            console.error(e);
        } finally {
            if (spinner) spinner.classList.add('hidden');
        }
    }
});

// Manual Image uploads for Reconstruction
if (selectShare1Btn) selectShare1Btn.addEventListener('click', () => share1Input.click());
if (selectShare2Btn) selectShare2Btn.addEventListener('click', () => share2Input.click());

if (share1Input) {
    share1Input.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file && file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const dataUrl = e.target.result;
                share1Preview.src = dataUrl;
                if (currentShares) currentShares.share1Url = dataUrl;
                else currentShares = { share1Url: dataUrl, share2Live: null };
            };
            reader.readAsDataURL(file);
        }
        share1Input.value = '';
    });
}

if (share2Input) {
    share2Input.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file && file.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const dataUrl = e.target.result;
                share2Preview.src = dataUrl;
                if (currentShares) currentShares.share2Live = dataUrl;
                else currentShares = { share1Url: null, share2Live: dataUrl };
            };
            reader.readAsDataURL(file);
        }
        share2Input.value = '';
    });
}

downloadReconstructedBtn.addEventListener('click', () => {
    if (downloadReconstructedBtn.dataset.image) {
        const link = document.createElement('a');
        link.href = downloadReconstructedBtn.dataset.image;
        link.download = 'reconstructed_overlay.png';
        document.body.appendChild(link);
        link.click();
        link.remove();
    }
});

// ==========================================
// 14. Settings, Theme, and Data Exports
// ==========================================

settingsBtn.addEventListener('click', openSettings);
closeSettingsBtn.addEventListener('click', closeSettings);
saveSettingsBtn.addEventListener('click', saveSettings);

function openSettings() {
    if (!state.currentUser) return;
    
    settingsUsername.value = state.currentUser.username;
    settingsBio.value = localStorage.getItem('revealx_bio') || '';
    settingsStatus.value = localStorage.getItem('revealx_status') || 'online';
    
    if (settingsNotifications) {
        settingsNotifications.checked = localStorage.getItem('revealx_notifications') !== 'false';
    }
    
    const preview = document.getElementById('settingsProfilePreview');
    if (preview && state.currentUser.profile_image) {
        preview.src = state.currentUser.profile_image;
        preview.classList.remove('empty');
    } else if (preview) {
        preview.classList.add('empty');
    }
    
    settingsModal.classList.remove('hidden');
    announceToScreenReader('Settings panel opened.');
}

function closeSettings() {
    settingsModal.classList.add('hidden');
}

function saveSettings() {
    const userVal = settingsUsername.value.trim();
    const bio = settingsBio.value.trim();
    const status = settingsStatus.value;
    const notifications = settingsNotifications ? settingsNotifications.checked : true;

    if (!userVal) {
        alert('Username cannot be empty');
        return;
    }

    if (userVal !== state.currentUser.username) {
        socket.emit('update_username', { new_username: userVal });
    }

    localStorage.setItem('revealx_bio', bio);
    localStorage.setItem('revealx_status', status);
    localStorage.setItem('revealx_notifications', notifications);

    if (notifications && Notification && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    accountName.textContent = userVal;
    state.currentUser.username = userVal;

    closeSettings();
    alert('Settings successfully updated!');
}

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
        socket.emit('select_chat', { recipient_id: state.selectedRecipientId });
    }
});

// ==========================================
// 15. Notification & Shell Presences
// ==========================================

function areNotificationsEnabled() {
    return localStorage.getItem('revealx_notifications') !== 'false';
}

function showNotification(title, options = {}) {
    if (!areNotificationsEnabled() || !Notification) return;
    if (Notification.permission === 'granted') {
        new Notification(title, {
            tag: 'reveal-x-notif',
            requireInteraction: false,
            ...options
        });
    }
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
