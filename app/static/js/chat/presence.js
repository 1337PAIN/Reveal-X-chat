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
    const selected = state.users.find(u => u.id === state.selectedRecipientId);
    if (state.selectedRecipientId && (!selected || (selected.locked && !state.chatLock.open))) {
        // Gone, or locked while the PIN is not entered.
        closeConversation();
    } else if (late.refreshChatMenu) {
        late.refreshChatMenu();
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

const FLAG_ICONS = [['pinned', 'pin'], ['muted', 'bell-off'], ['locked', 'lock']];

/** One contact's row, with its pin / mute / lock marks. */
function contactRow(user) {
    const status = user.status || (user.online ? 'online' : 'offline');
    const row = window.revealxContactRow.buildContactRow(document, user, {
        selected: user.id === state.selectedRecipientId,
        unread: state.unreadCounts[user.id] || 0,
        statusLabel: STATUS_LABELS[status] || status,
        onSelect: selectUser,
    });
    const marks = FLAG_ICONS.filter(([flag]) => user[flag]);
    const button = row.querySelector('.contact-button');
    if (marks.length && button) {
        // The icons are decoration; the same facts are said in words.
        const words = document.createElement('span');
        words.className = 'sr-only';
        words.textContent = window.revealxChatMenuUI.flagWords(user);
        const icons = document.createElement('span');
        icons.className = 'row-flags';
        icons.setAttribute('aria-hidden', 'true');
        marks.forEach(([, icon]) => icons.append(window.revealxIcons.icon(document, icon)));
        button.append(words, icons);
    }
    return row;
}

function emptyRow(text) {
    const li = document.createElement('li');
    li.className = 'empty-state';
    li.textContent = text;
    return li;
}

export function updateUsersList(nextUsers) {
    state.users = nextUsers || [];
    const matches = state.users.filter(u =>
        u.username.toLowerCase().includes(state.userSearch)
    );
    // Pinned first; archived and (until the PIN) locked chats out of the
    // main list. The rules are in chat-menu-ui.js.
    const groups = window.revealxChatMenuUI.organise(matches, { lockOpen: state.chatLock.open });

    usersList.replaceChildren();
    userCount.textContent = `${state.users.length} contact${state.users.length !== 1 ? 's' : ''}`;

    // Rows are built by contact-row.js: a <button> per account, so the list
    // can be reached and used by keyboard, and nothing from another user is
    // ever parsed as markup.
    if (state.users.length === 0) {
        usersList.append(emptyRow('No contacts yet. Add someone by their username above.'));
    } else if (groups.main.length === 0) {
        usersList.append(emptyRow(matches.length ? 'Nothing here: see Archived above.' : 'No matching contacts'));
    } else {
        groups.main.forEach(user => usersList.appendChild(contactRow(user)));
    }

    // Locked chats: one row to open them while hidden, one to hide them again.
    const lockedBtn = document.getElementById('lockedChatsBtn');
    const hideLockedBtn = document.getElementById('hideLockedBtn');
    const lockedTotal = state.users.filter(u => u.locked).length;
    if (lockedBtn) {
        lockedBtn.hidden = groups.lockedHidden === 0;
        document.getElementById('lockedChatsCount').textContent = String(groups.lockedHidden);
    }
    if (hideLockedBtn) hideLockedBtn.hidden = !(state.chatLock.open && lockedTotal > 0);

    // Archived: a row that folds the archived chats out beneath it.
    const archivedBtn = document.getElementById('archivedBtn');
    const archivedList = document.getElementById('archivedList');
    if (archivedBtn && archivedList) {
        const count = groups.archived.length;
        archivedBtn.hidden = count === 0;
        document.getElementById('archivedCount').textContent = String(count);
        const open = state.showArchived && count > 0;
        archivedBtn.setAttribute('aria-expanded', String(open));
        archivedList.hidden = !open;
        archivedList.replaceChildren(...(open ? groups.archived.map(contactRow) : []));
    }
}

/** Back to no conversation selected. */
export function closeConversation() {
    state.selectedRecipientId = null;
    chatTitle.textContent = 'Select a contact';
    chatSubtitle.textContent = 'Choose a contact to start a private chat';
    typingIndicator.textContent = '';
    setChatEnabled(false);
    const menuBtn = document.getElementById('chatMenuBtn');
    if (menuBtn) menuBtn.hidden = true;
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
    const menuBtn = document.getElementById('chatMenuBtn');
    if (menuBtn) menuBtn.hidden = false;
    if (late.refreshChatMenu) late.refreshChatMenu();

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

