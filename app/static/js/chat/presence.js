// The contact list and presence.
// Split out of chat.js; main.js loads the modules in order.

import { late, registerLate } from './late.js';
import {
    STATUS_LABELS,
    chatSubtitle,
    chatTitle,
    messageInput,
    messageSearchInput,
    sendBtn,
    shareImageBtn,
    socket,
    state,
    typingIndicator,
    userCount,
    usersList,
} from './core.js';
import {
    keyExchangeState,
    refreshPeerPins,
    renderKeyManager,
} from './keys.js';

registerLate({ setChatEnabled, updateUsersList });

// ==========================================
// 9. Real-time User presence list handlers
// ==========================================

socket.on('users_updated', (data) => {
    updateUsersList(data.users);
    refreshPeerPins();

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

export function updateUsersList(nextUsers) {
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
        
        // Built with createElement and .src, never interpolated: the photo is
        // a data URL from another user, and it used to be spliced into
        // innerHTML, where a quote in it ended the attribute and the rest
        // became markup in everyone's sidebar.
        let avatar;
        if (user.profile_image) {
            avatar = document.createElement('img');
            avatar.className = 'avatar-img';
            avatar.alt = user.username;
            avatar.src = user.profile_image;
        } else {
            avatar = document.createElement('div');
            avatar.className = 'avatar-dot';
            avatar.textContent = user.username.charAt(0).toUpperCase();
        }
        
        const status = user.status || (user.online ? 'online' : 'offline');
        li.dataset.status = status;

        li.innerHTML = `
            <span class="user-meta">
                <span class="user-name">${escapeHtml(user.username)}</span>
                <span class="user-status-line">${escapeHtml(user.bio || STATUS_LABELS[status] || status)}</span>
            </span>
            ${count > 0 ? `<span class="unread-badge" aria-label="${count} unread messages">${count}</span>` : ''}
        `;
        li.prepend(avatar);
        li.addEventListener('click', () => selectUser(user.id));
        usersList.appendChild(li);
    });
}

export function selectUser(userId) {
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

    late.requestChatHistory(state.selectedRecipientId);
    late.closeSidebar();
    messageInput.focus();
}

/** Subtitle under the chat title: availability, plus their bio when set. */
function describeUser(user) {
    if (!user) return '';
    const status = user.status || (user.online ? 'online' : 'offline');
    const label = STATUS_LABELS[status] || status;
    return user.bio ? `${label} - ${user.bio}` : `Private conversation - ${label.toLowerCase()}`;
}

export function setChatEnabled(enabled) {
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

