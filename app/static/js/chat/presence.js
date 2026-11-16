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
    // Removed as a contact (by either side) while their chat was open: the
    // server will refuse anything sent now, so close it rather than let the
    // composer look usable.
    if (state.selectedRecipientId && !state.users.some(u => u.id === state.selectedRecipientId)) {
        closeConversation();
    }

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
    userCount.textContent = `${state.users.length} contact${state.users.length !== 1 ? 's' : ''}`;

    if (state.users.length === 0) {
        usersList.innerHTML = '<li class="empty-state">No contacts yet. Add someone by their username above.</li>';
        return;
    }

    if (visible.length === 0) {
        usersList.innerHTML = '<li class="empty-state">No matching contacts</li>';
        return;
    }

    // Rows are built by contact-row.js: a <button> per account, so the list
    // can be reached and used by keyboard, and nothing from another user is
    // ever parsed as markup.
    visible.forEach(user => {
        const status = user.status || (user.online ? 'online' : 'offline');
        usersList.appendChild(window.revealxContactRow.buildContactRow(document, user, {
            selected: user.id === state.selectedRecipientId,
            unread: state.unreadCounts[user.id] || 0,
            statusLabel: STATUS_LABELS[status] || status,
            onSelect: selectUser,
        }));
    });
}

/** Back to no conversation selected. */
export function closeConversation() {
    state.selectedRecipientId = null;
    chatTitle.textContent = 'Select a contact';
    chatSubtitle.textContent = 'Choose a contact to start a private chat';
    typingIndicator.textContent = '';
    setChatEnabled(false);
    const remove = document.getElementById('removeContactBtn');
    if (remove) remove.hidden = true;
    late.showWelcome();
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
    const remove = document.getElementById('removeContactBtn');
    if (remove) remove.hidden = false;

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
        : 'Select a contact first...';
}

