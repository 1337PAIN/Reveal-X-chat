// Message reactions.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import {
    messagesContainer,
    share2Memory,
    socket,
    state,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';

registerLate({ showTransientError });

// ==========================================
// Reactions
//
// The server has always accepted react_message and broadcast
// message_reactions; nothing on the client sent or listened for either, so the
// feature the README advertised did not exist. This is the missing half.
// ==========================================

const REACTION_CHOICES = ['\u{1F44D}', '\u2764\uFE0F', '\u{1F602}', '\u{1F62E}', '\u{1F622}', '\u{1F64F}'];

/** Redraw one message's pills from the server's {emoji: [userIds]} map. */
export function renderReactions(messageId, reactions) {
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
export function toggleReactionPicker(messageId, button) {
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

export function showTransientError(message) {
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
    share2Memory.clear();
    localStorage.removeItem('revealx_username');
    await window.revealxFirebase.signOut();
    window.location.reload();
});

