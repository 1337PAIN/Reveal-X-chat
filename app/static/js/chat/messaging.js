// Sending text and voice, typing, deleting, revoking.
// Split out of chat.js; main.js loads the modules in order.

import { late, registerLate } from './late.js';
import { shared } from './shared.js';
import {
    MAX_MESSAGE_LENGTH,
    MAX_VOICE_BYTES,
    forgetShare2,
    messageInput,
    messagesContainer,
    reconstructionPanel,
    sendBtn,
    socket,
    state,
    typingIndicator,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';
import {
    checkPeerPin,
    openSafetyChangeDialog,
    peerPublicKey,
} from './keys.js';
import {
    showTransientError,
} from './reactions.js';
import {
    selectUser,
    updateUsersList,
} from './presence.js';
import {
    displayMessage,
    openEncrypted,
    scrollToBottom,
} from './render.js';

registerLate({ deleteMessage, requestChatHistory });

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
    forgetShare2(data && data.message_id);
    const node = messagesContainer.querySelector(`[data-message-id="${data.message_id}"]`);
    if (node) {
        node.classList.add('message-leaving');
        setTimeout(() => node.remove(), 250);
    }
});

socket.on('typing', (data) => {
    if (!data || data.sender_id !== state.selectedRecipientId) return;

    clearTimeout(shared.typingHideTimer);
    
    // Real-time debounced Typing Preview
    typingIndicator.textContent = data.is_typing ? `${data.username} is typing...` : '';
    if (data.is_typing) {
        shared.typingHideTimer = setTimeout(() => {
            typingIndicator.textContent = '';
        }, 2500);
    }
});

socket.on('share_revoked', (data) => {
    forgetShare2(data && data.message_id);
    window.revealxDialogs.toast('Access to that image share has been revoked.');
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

// The character counter (composer.js). Visible for the last 200 characters;
// announced politely only when a threshold is crossed, through the existing
// screen-reader region -- the visible counter itself is aria-hidden so a
// reader is not told the count on every keystroke.
let lastComposerLength = 0;
function updateComposerCounter() {
    const length = messageInput.value.length;
    const counter = document.getElementById('composerCounter');
    const view = window.revealxComposer.counterState(length, MAX_MESSAGE_LENGTH, lastComposerLength);
    lastComposerLength = length;
    if (counter) {
        counter.hidden = !view.visible;
        counter.textContent = view.text;
        counter.dataset.level = view.level;
    }
    if (view.announce) announceToScreenReader(view.announce);
}
messageInput.addEventListener('input', updateComposerCounter);

messageInput.addEventListener('input', () => {
    if (!state.selectedRecipientId) return;
    // With typing indicators off, nothing is emitted at all - the peer never
    // learns that this user is composing.
    if (!late.getPref('typingIndicators')) return;

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

    clearTimeout(shared.typingStopTimer);
    shared.typingStopTimer = setTimeout(() => {
        socket.emit('typing', {
            recipient_id: state.selectedRecipientId,
            is_typing: false,
        });
    }, 1000);
});

// Conversations in which the user agreed to send unencrypted, this tab only.
// A reload or sign-out asks again.
const plaintextConsent = new Set();

/**
 * Decide how a message to `recipientId` may be sent, asking the user when the
 * policy needs consent. Returns 'encrypt', 'plaintext', or null for "do not
 * send". The rules themselves are in send-policy.js.
 *
 * The pin is checked here, at send time, rather than from a cached flag: the
 * key could have changed since the user list was last rendered.
 */
async function decideHowToSend(recipientId) {
    const pin = await checkPeerPin(recipientId);
    const decision = window.revealxSendPolicy.decideSend({
        e2eeReady: window.revealxE2EE.isReady(),
        peerHasKey: Boolean(peerPublicKey(recipientId)),
        pinStatus: pin.status,
        plaintextConfirmed: plaintextConsent.has(recipientId),
    });

    if (decision.action === 'blocked') {
        openSafetyChangeDialog(recipientId);
        return null;
    }
    if (decision.action === 'confirm-plaintext') {
        const user = state.users.find(u => u.id === recipientId);
        const why = decision.reason === 'peer-has-no-key'
            ? `${user ? user.username : 'This contact'} has not set up encryption yet`
            : 'This browser cannot encrypt messages (it needs HTTPS and Web Crypto)';
        const agreed = await window.revealxDialogs.askConfirm({
            title: 'Send without encryption?',
            body: `${why}, so messages in this conversation would be sent unencrypted `
                + 'and the server could read them.',
            confirmLabel: 'Send unencrypted',
        });
        if (!agreed) return null;
        plaintextConsent.add(recipientId);
        return 'plaintext';
    }
    return decision.action;
}

// While disconnected, socket.io buffers emits and flushes them on reconnect
// -- before the resume token has been presented -- so they would reach a
// socket with no session and be refused. Better to say so now.
export function connectionReady() {
    if (socket.connected && !state.resuming) return true;
    showTransientError('Reconnecting. Try again in a moment.');
    return false;
}

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text || !state.selectedRecipientId) return;
    if (!connectionReady()) return;

    const mode = await decideHowToSend(state.selectedRecipientId);
    if (!mode) return;

    if (text.length > MAX_MESSAGE_LENGTH) {
        window.revealxDialogs.toast(`Messages can be up to ${MAX_MESSAGE_LENGTH} characters.`, { kind: 'error' });
        return;
    }

    // A two-second pause between sends. The label used to become a countdown
    // ("2s", "1s") and the button was disabled: a screen reader announced a
    // button called "2s", and disabling it threw keyboard focus out of the
    // composer. It stays "Send", marked aria-disabled with a visual state,
    // and a press during the pause is explained rather than ignored.
    if (state.sendCooldownActive) {
        announceToScreenReader('Wait a moment before sending again.');
        return;
    }

    state.sendCooldownActive = true;
    sendBtn.setAttribute('aria-disabled', 'true');
    sendBtn.classList.add('cooling');
    setTimeout(() => {
        state.sendCooldownActive = false;
        sendBtn.removeAttribute('aria-disabled');
        sendBtn.classList.remove('cooling');
    }, 2000);

    if (late.getPref('typingIndicators')) {
        socket.emit('typing', {
            recipient_id: state.selectedRecipientId,
            is_typing: false,
        });
    }

    const recipientId = state.selectedRecipientId;
    messageInput.value = '';
    updateComposerCounter();

    let payload = { recipient_id: recipientId, message: text };

    if (mode === 'encrypt') {
        // This used to catch an encryption error, log it, and send the
        // message in plaintext anyway. A failure here now stops the send and
        // gives the text back; see send-policy.js.
        let sealed;
        try {
            sealed = await window.revealxE2EE.encrypt(peerPublicKey(recipientId), text, { recipientId });
        } catch (error) {
            sealed = error;
        }
        if (window.revealxSendPolicy.afterEncrypt(sealed).action !== 'send') {
            console.error('Encryption failed; the message was not sent', sealed);
            messageInput.value = text;
            showTransientError('The message could not be encrypted, so it was not sent.');
            return;
        }
        payload = {
            recipient_id: recipientId,
            message: sealed.ciphertext,
            iv: sealed.iv,
            encrypted: true,
            v: sealed.v,
            msg_uuid: sealed.msg_uuid,
            sent_at: sealed.sent_at,
        };
    }

    socket.emit('send_message', payload);
}

/**
 * Delete a message.
 *
 * Scope depends on who you are: the sender owns what they sent and removes it
 * from both sides; the recipient can only drop their own copy.
 */
async function deleteMessage(messageId, isSent) {
    // Asked in the app (dialogs.js), not with the browser's confirm box, which blocks
    // the page, cannot be themed, and reads as a browser alert.
    const agreed = await window.revealxDialogs.askConfirm(isSent
        ? {
            title: 'Delete for everyone?',
            body: 'This message will be removed from both sides.',
            confirmLabel: 'Delete for everyone',
        }
        : {
            title: 'Delete for you?',
            body: 'It is removed from your side only. The sender keeps their copy.',
            confirmLabel: 'Delete for me',
        });
    if (!agreed) return;

    socket.emit('delete_message', { message_id: messageId });
    // Focus is on this message's "More actions" button, which goes when the
    // message does, and focus would fall to <body> -- the next Tab starting
    // from the top of the page. The composer is where a keyboard user goes on.
    messageInput.focus();
}

/**
 * Ask for a conversation's history.
 *
 * Opening a chat marks it read server-side, so the read-receipt preference has
 * to travel with the request - otherwise turning receipts off would still leak
 * "Read" to the sender the moment the chat is opened.
 */
export function requestChatHistory(recipientId) {
    if (!recipientId) return;
    socket.emit('select_chat', {
        recipient_id: recipientId,
        send_read_receipts: late.getPref('readReceipts'),
    });
}

function markConversationRead(senderId) {
    // With read receipts off we simply never tell the server we read anything,
    // so the sender keeps seeing "Sent".
    if (!late.getPref('readReceipts')) return;
    socket.emit('mark_read', { sender_id: senderId });
}

/** Decide and raise the alert for an incoming message. */
async function notifyForIncoming(msg) {
    let preview = 'Image share';
    let searchable = '';

    if (msg.type === 'voice') {
        preview = 'Voice message';
    } else if (msg.type === 'text') {
        if (msg.encrypted) {
            // Through the same checks as the transcript: a stale or replayed
            // message must not reach a notification either.
            const opened = await openEncrypted(msg);
            searchable = opened.status === 'ok' ? opened.text : '';
            preview = 'Encrypted message';
        } else {
            searchable = msg.content;
            preview = msg.content;
        }
    }

    if (!late.shouldNotifyFor(searchable)) return;

    late.playNotificationSound();
    late.showNotification(`New message from ${msg.username}`, {
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
let voiceTicker = null;
let voiceStartedAt = 0;
let voiceCancelled = false;

const voiceBtn = document.getElementById('voiceBtn');
const voiceCancelBtn = document.getElementById('voiceCancelBtn');
const voiceTimerEl = document.getElementById('voiceTimer');
if (voiceBtn) voiceBtn.addEventListener('click', toggleVoiceRecording);
if (voiceCancelBtn) voiceCancelBtn.addEventListener('click', cancelVoiceRecording);

/**
 * Container to record in.
 *
 * Left to itself, MediaRecorder picks per engine: Chrome gives WebM/Opus and
 * Safari gives MP4/AAC. Each plays its own and may not play the other's, so a
 * recording made on Android could arrive on an iPhone as a player that loads
 * and produces nothing -- with no error anywhere to explain it.
 *
 * MP4/AAC is the one container both engines record *and* play, so it is tried
 * first. The list is ordered by how widely the result can be played back, not
 * by quality; the fallbacks only run on an engine that cannot do MP4.
 */
function pickRecorderMime() {
    const candidates = [
        'audio/mp4;codecs=mp4a.40.2',
        'audio/mp4',
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
    ];
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
    return candidates.find(type => MediaRecorder.isTypeSupported(type)) || '';
}

function voiceRecordingActive() {
    return Boolean(voiceRecorder && voiceRecorder.state === 'recording');
}

function formatVoiceClock(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

function setVoiceButtonState(recording) {
    if (voiceBtn) {
        voiceBtn.classList.toggle('recording', recording);
        // The icon is decoration (aria-hidden); the name carries the meaning.
        voiceBtn.replaceChildren(window.revealxIcons.icon(document, recording ? 'stop' : 'mic'));
        voiceBtn.setAttribute('aria-label', recording ? 'Stop and send recording' : 'Record a voice message');
        // The composer carries the state so CSS can lay the row out for it: on
        // a phone the clock and the discard button do not fit beside a text
        // field, and the field is the one thing that is no use while recording.
        const composer = voiceBtn.closest('.message-input');
        if (composer) composer.classList.toggle('is-recording', recording);
    }
    if (voiceCancelBtn) voiceCancelBtn.classList.toggle('hidden', !recording);
    if (voiceTimerEl) {
        voiceTimerEl.classList.toggle('hidden', !recording);
        if (!recording) voiceTimerEl.textContent = '';
    }

    clearInterval(voiceTicker);
    voiceTicker = null;
    if (!recording) return;

    // A recording with no visible clock is one the user cannot judge against
    // the 60-second cap until it cuts them off.
    voiceStartedAt = Date.now();
    const tick = () => {
        const elapsed = (Date.now() - voiceStartedAt) / 1000;
        const left = Math.max(0, MAX_VOICE_SECONDS - elapsed);
        if (voiceTimerEl) {
            voiceTimerEl.textContent = formatVoiceClock(elapsed);
            voiceTimerEl.classList.toggle('ending', left <= 10);
        }
    };
    tick();
    voiceTicker = setInterval(tick, 250);
}

/** Stop recording and throw the audio away, rather than sending it. */
function cancelVoiceRecording() {
    if (!voiceRecordingActive()) return;
    voiceCancelled = true;
    voiceRecorder.stop();
    announceToScreenReader('Recording discarded.');
}

async function toggleVoiceRecording() {
    if (voiceRecordingActive()) {
        voiceRecorder.stop();
        return;
    }

    if (!state.selectedRecipientId) {
        window.revealxDialogs.toast('Select an account first.');
        return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        window.revealxDialogs.toast('Voice recording is not supported in this browser.', { kind: 'error' });
        return;
    }

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
        window.revealxDialogs.toast('Microphone access was denied.', { kind: 'error' });
        return;
    }

    const recipientId = state.selectedRecipientId;
    voiceChunks = [];
    voiceCancelled = false;
    const preferred = pickRecorderMime();
    try {
        voiceRecorder = preferred
            ? new MediaRecorder(stream, { mimeType: preferred })
            : new MediaRecorder(stream);
    } catch (error) {
        // isTypeSupported said yes and the constructor disagreed. Rather than
        // lose the recording, fall back to whatever the engine prefers.
        console.warn('Preferred recording type refused, using the default', error);
        voiceRecorder = new MediaRecorder(stream);
    }

    voiceRecorder.addEventListener('dataavailable', (event) => {
        if (event.data && event.data.size) voiceChunks.push(event.data);
    });

    voiceRecorder.addEventListener('stop', async () => {
        clearTimeout(voiceStopTimer);
        stream.getTracks().forEach(track => track.stop());
        setVoiceButtonState(false);

        const blob = new Blob(voiceChunks, { type: voiceRecorder.mimeType || 'audio/webm' });
        const discarded = voiceCancelled;
        voiceRecorder = null;
        voiceChunks = [];
        voiceCancelled = false;

        if (discarded) return;
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
        window.revealxDialogs.toast('Voice message is too long. Keep it under a minute.', { kind: 'error' });
        return;
    }
    if (!connectionReady()) return;

    const mode = await decideHowToSend(recipientId);
    if (!mode) return;

    const mime = (blob.type || 'audio/webm').split(';')[0];

    if (mode === 'encrypt') {
        // As for text: a failure stops the send rather than falling back to
        // an unencrypted recording, which is what this used to do.
        let sealed;
        try {
            sealed = await window.revealxE2EE.encryptBytes(
                peerPublicKey(recipientId), await blob.arrayBuffer(), { recipientId });
        } catch (error) {
            sealed = error;
        }
        if (window.revealxSendPolicy.afterEncrypt(sealed).action !== 'send') {
            console.error('Voice encryption failed; the recording was not sent', sealed);
            showTransientError('The voice message could not be encrypted, so it was not sent.');
            return;
        }
        socket.emit('send_voice', {
            recipient_id: recipientId,
            audio: sealed.ciphertext,
            iv: sealed.iv,
            mime,
            encrypted: true,
            v: sealed.v,
            msg_uuid: sealed.msg_uuid,
            sent_at: sealed.sent_at,
        });
        return;
    }

    socket.emit('send_voice', {
        recipient_id: recipientId,
        audio: await late.blobToDataUrl(blob),
    });
}

