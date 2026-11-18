// The chat menu (⋮ beside the contact's name), lock chat, archive, block.
// What the menu offers and how the list is grouped are in chat-menu-ui.js
// and tested there; this connects them to the socket and the page.

import { late, registerLate } from './late.js';
import { messagesContainer, socket, state } from './core.js';
import { announceToScreenReader } from './basics.js';
import { closeConversation, updateUsersList } from './presence.js';
import { askSecret } from './settings.js';

const ui = window.revealxChatMenuUI;
const dialogs = window.revealxDialogs;
const menuBtn = document.getElementById('chatMenuBtn');
const menu = document.getElementById('chatMenu');
const menuControl = menuBtn && menu ? dialogs.wireMenu(menuBtn, menu) : null;

registerLate({ refreshChatMenu });

function selectedUser() {
    return state.users.find((u) => u.id === state.selectedRecipientId) || null;
}

/** Rebuild the menu for the open chat: labels follow its settings. */
function refreshChatMenu() {
    if (!menu) return;
    ui.buildMenu(document, menu, ui.menuItems(selectedUser()), pick);
}

function pick(action) {
    const user = selectedUser();
    if (menuControl) menuControl.close(action !== 'info');
    if (!user) return;
    const change = ui.prefChange(action, user);
    if (change) {
        socket.emit('chat_pref', change);
        const done = {
            muted: change.value ? 'Notifications muted' : 'Notifications on',
            pinned: change.value ? 'Chat pinned' : 'Chat unpinned',
            archived: change.value ? 'Chat archived' : 'Chat unarchived',
        }[change.pref];
        dialogs.toast(`${done} for ${user.username}.`);
        return;
    }
    if (action === 'info') openContactInfo(user);
    else if (action === 'lock') toggleLock(user);
    else if (action === 'clear') confirmThen({
        title: `Clear your chat with ${user.username}?`,
        body: 'Every message in it is deleted for you. They keep their copy.',
        confirmLabel: 'Clear chat',
    }, () => socket.emit('clear_chat', { peer_id: user.id }));
    else if (action === 'block') confirmThen({
        title: `Block ${user.username}?`,
        body: 'They are removed from your contacts and cannot message you or send you a request. '
            + 'They are not told. You can unblock them in Settings.',
        confirmLabel: 'Block',
    }, () => socket.emit('block_user', { user_id: user.id }));
    else if (action === 'remove') confirmThen({
        title: `Remove ${user.username}?`,
        body: 'You will not be able to message each other until one of you adds the other again.',
        confirmLabel: 'Remove',
    }, () => socket.emit('contact_remove', { user_id: user.id }));
}

function confirmThen(options, then) {
    dialogs.askConfirm(options).then((agreed) => {
        if (agreed) then();
    });
}

// --- Contact info -------------------------------------------------------------

const infoModal = document.getElementById('contactInfoModal');

async function openContactInfo(user) {
    if (!infoModal) return;
    const avatar = document.getElementById('contactInfoAvatar');
    const photo = document.getElementById('contactInfoPhoto');
    avatar.textContent = (user.username || '?').charAt(0).toUpperCase();
    avatar.hidden = Boolean(user.profile_image);
    photo.hidden = !user.profile_image;
    if (user.profile_image) photo.src = user.profile_image;
    document.getElementById('contactInfoName').textContent = user.username;
    document.getElementById('contactInfoStatus').textContent = user.online ? 'Online' : 'Offline';
    document.getElementById('contactInfoBio').textContent = user.bio || '';
    const safety = document.getElementById('contactInfoSafety');
    safety.textContent = user.public_key ? 'Working it out...' : 'They have no encryption key yet.';
    safety.classList.remove('is-number');
    infoModal.classList.remove('hidden');
    document.getElementById('contactInfoCloseBtn').focus();
    if (user.public_key && window.revealxE2EE && window.revealxE2EE.isReady()) {
        try {
            safety.textContent = await window.revealxE2EE.safetyNumber(user.public_key);
            safety.classList.add('is-number');
        } catch (error) {
            safety.textContent = 'Could not be worked out on this device.';
        }
    }
}

function closeContactInfo() {
    infoModal.classList.add('hidden');
    if (menuBtn && !menuBtn.hidden) menuBtn.focus();
}

if (infoModal) {
    document.getElementById('contactInfoCloseBtn').addEventListener('click', closeContactInfo);
    // Escape closes it, as it does every other dialog; it was the one left out.
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !infoModal.classList.contains('hidden')) closeContactInfo();
    });
}

// --- Lock chat ----------------------------------------------------------------

function askPin(options) {
    return askSecret({ pin: true, maxLength: 8, autocomplete: 'off', validate: ui.pinProblem, ...options });
}

async function toggleLock(user) {
    if (user.locked) {
        socket.emit('chat_lock', { peer_id: user.id, value: false });
        dialogs.toast(`${user.username}'s chat is no longer locked.`);
        return;
    }
    if (!state.chatLock.pinSet) {
        const pin = await askPin({
            title: 'Set a chat-lock PIN',
            body: 'Locked chats are hidden behind this PIN. You will need it to open them, on any device.',
            label: 'New PIN (4-8 digits)',
            confirmLabel: 'Type it again',
            okLabel: 'Set PIN and lock',
        });
        if (!pin) return;
        // One event: set the PIN and lock this chat together (see the server).
        socket.emit('chat_lock_set_pin', { pin, lock_peer_id: user.id });
    } else {
        socket.emit('chat_lock', { peer_id: user.id, value: true });
    }
    dialogs.toast(`${user.username}'s chat is locked.`);
}

async function openLockedChats() {
    const pin = await askPin({
        title: 'Open locked chats',
        label: 'Chat-lock PIN',
        okLabel: 'Open',
    });
    if (pin) socket.emit('chat_lock_open', { pin });
}

socket.on('chat_lock_state', (data) => {
    const wasOpen = state.chatLock.open;
    state.chatLock = { pinSet: Boolean(data && data.pin_set), open: Boolean(data && data.open) };
    if (state.chatLock.open && !wasOpen) announceToScreenReader('Locked chats are showing.');
    updateUsersList(state.users);
    const selected = selectedUser();
    if (selected && selected.locked && !state.chatLock.open) closeConversation();
    refreshSettings();
});

socket.on('chat_lock_error', (data) => {
    dialogs.toast((data && data.error) || 'That did not work.', { kind: 'error' });
});

const lockedChatsBtn = document.getElementById('lockedChatsBtn');
if (lockedChatsBtn) lockedChatsBtn.addEventListener('click', openLockedChats);
const hideLockedBtn = document.getElementById('hideLockedBtn');
if (hideLockedBtn) hideLockedBtn.addEventListener('click', () => socket.emit('chat_lock_close', {}));

// --- Archived -----------------------------------------------------------------

const archivedBtn = document.getElementById('archivedBtn');
if (archivedBtn) {
    archivedBtn.addEventListener('click', () => {
        state.showArchived = !state.showArchived;
        updateUsersList(state.users);
    });
}

// --- Clear chat -----------------------------------------------------------------

socket.on('chat_cleared', (data) => {
    if (data && data.peer_id === state.selectedRecipientId) {
        messagesContainer.replaceChildren();
        late.requestChatHistory(state.selectedRecipientId);
    }
});

// --- Settings: chat-lock PIN and blocked accounts --------------------------------

const changePinBtn = document.getElementById('changeChatLockPinBtn');
const forgotPinBtn = document.getElementById('forgotChatLockPinBtn');

function refreshSettings() {
    if (changePinBtn) changePinBtn.hidden = !state.chatLock.pinSet;
    if (forgotPinBtn) forgotPinBtn.hidden = !state.chatLock.pinSet;
}

if (changePinBtn) {
    changePinBtn.addEventListener('click', async () => {
        const current = await askPin({ title: 'Change chat-lock PIN', label: 'Current PIN', okLabel: 'Next' });
        if (!current) return;
        const pin = await askPin({
            title: 'Change chat-lock PIN',
            label: 'New PIN (4-8 digits)',
            confirmLabel: 'Type it again',
            okLabel: 'Change PIN',
        });
        if (pin) socket.emit('chat_lock_set_pin', { pin, current_pin: current });
    });
}

if (forgotPinBtn) {
    forgotPinBtn.addEventListener('click', () => confirmThen({
        title: 'Reset your chat-lock PIN?',
        body: 'Every locked chat is cleared for you and unlocked, and the PIN is removed. '
            + 'Your contacts keep their copies. This cannot be undone.',
        confirmLabel: 'Clear locked chats and reset',
    }, () => socket.emit('chat_lock_reset', {})));
}

const blockedList = document.getElementById('blockedList');

socket.on('blocked_updated', (data) => {
    if (!blockedList) return;
    const blocked = (data && data.blocked) || [];
    if (!blocked.length) {
        const empty = document.createElement('li');
        empty.className = 'settings-hint';
        empty.textContent = 'Nobody is blocked.';
        blockedList.replaceChildren(empty);
        return;
    }
    blockedList.replaceChildren(...blocked.map((entry) => {
        const li = document.createElement('li');
        li.className = 'blocked-row';
        const name = document.createElement('span');
        name.textContent = entry.username;
        const unblock = document.createElement('button');
        unblock.type = 'button';
        unblock.className = 'request-btn';
        unblock.textContent = 'Unblock';
        unblock.setAttribute('aria-label', `Unblock ${entry.username}`);
        unblock.addEventListener('click', () => socket.emit('unblock_user', { user_id: entry.id }));
        li.append(name, unblock);
        return li;
    }));
});
