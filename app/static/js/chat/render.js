// Rendering messages: decryption, shares, the tamper panel.
// Split out of chat.js; main.js loads the modules in order.

import { late, registerLate } from './late.js';
import {
    isClientSplitShare,
    messageSearchInput,
    messagesContainer,
    share2Memory,
    socket,
    state,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';
import {
    peerPublicKey,
} from './keys.js';
import {
    renderReactions,
    toggleReactionPicker,
} from './reactions.js';

registerLate({ e2eeContext, openEncrypted, filterVisibleMessages });

// ==========================================
// 10. Message Feeds Rendering & Virtual Scrolling
// ==========================================

socket.on('chat_history', (payload) => {
    const messages = payload.messages || [];
    const recipientId = payload.recipient_id;

    if (!recipientId && !state.selectedRecipientId) {
        late.showWelcome();
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
        loadMore.className = 'btn btn-secondary load-older';
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

/**
 * The E2EE v4 context for a stored message: what e2ee.js authenticates as
 * AES-GCM associated data. The ids are the server's, from the row itself, so
 * a row relabelled with another sender or recipient no longer decrypts.
 * Direct messages are conversation "dm"; "group:<id>" is reserved for when
 * groups have a client.
 */
export function e2eeContext(msg) {
    const extra = msg.extra || {};
    return {
        v: extra.v,
        conversation: 'dm',
        senderId: msg.sender_id,
        recipientId: msg.recipient_id,
        msgUuid: extra.msg_uuid,
        sentAt: extra.sent_at,
        epk: extra.epk,
    };
}

/**
 * Open an encrypted row through e2ee.js's openMessage, which decrypts and
 * then refuses stale, future-dated and replayed messages. The replay record
 * is in IndexedDB, so it survives a reload; it used to be a Map here.
 */
export function openEncrypted(msg, options = {}) {
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = peerPublicKey(otherId);
    if (!peerKey) return Promise.resolve({ status: 'failed' });
    const iv = msg.extra ? msg.extra.iv : null;
    return window.revealxE2EE.openMessage(peerKey, msg.content, iv, e2eeContext(msg),
        { messageId: msg.id, ...options });
}

const REPLAYED_NOTICE = 'Repeated message - this encrypted message was already delivered, so it is not shown again.';

// What each refusal from openMessage looks like to the reader.
export const OPEN_NOTICES = {
    legacy: 'Sent before an update; can\'t be opened.',
    unverifiable: 'Can\'t be verified - it was sent more than an hour ago or with a clock far from this one.',
    replay: REPLAYED_NOTICE,
    // Forward secrecy (E2EE v4): the key that sealed it was thrown away, so
    // once this page has reloaded only the recipient can open it.
    'own-sealed': 'Sent with forward secrecy - only the recipient can open it now.',
};

/** Fill a message body, decrypting first when the payload is E2EE. */
async function fillTextContent(node, msg) {
    if (!node) return;

    if (!msg.encrypted) {
        node.textContent = msg.content;
        return;
    }

    node.textContent = 'Decrypting...';
    const opened = await openEncrypted(msg);

    if (opened.status === 'ok') {
        node.textContent = opened.text;
    } else {
        node.textContent = OPEN_NOTICES[opened.status]
            || 'Encrypted message - cannot be opened with the current key pair.';
        node.classList.add('undecryptable');
    }
    filterVisibleMessages();
}

const VOICE_BARS = 36;
let voiceDecoder = null;

/**
 * The waveform and true length of a recording, from its bytes. An
 * OfflineAudioContext decodes without needing a user gesture and plays
 * nothing. Null when this engine cannot decode the container; the player
 * then draws a stable placeholder and takes the length from the element.
 */
async function decodeVoice(bytes) {
    const Context = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!Context || !bytes) return null;
    try {
        voiceDecoder = voiceDecoder || new Context(1, 1, 44100);
        // decodeAudioData detaches the buffer it is given; hand it a copy.
        const audio = await voiceDecoder.decodeAudioData(bytes.slice(0));
        return {
            peaks: window.revealxVoiceUI.peaksFromSamples(audio.getChannelData(0), VOICE_BARS),
            duration: audio.duration,
        };
    } catch (error) {
        return null;
    }
}

/** The bytes inside a base64 data: URL (an unencrypted voice note). */
function dataUrlBytes(url) {
    try {
        const binary = atob(String(url).slice(String(url).indexOf(',') + 1));
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        return bytes.buffer;
    } catch (error) {
        return null;
    }
}

async function drawVoiceWaveform(parts, bytes, msg) {
    if (!parts) return;
    const ui = window.revealxVoiceUI;
    const decoded = await decodeVoice(bytes);
    if (decoded) {
        ui.setPeaks(parts, decoded.peaks);
        ui.setDuration(parts, decoded.duration);
    } else {
        ui.setPeaks(parts, ui.placeholderPeaks(msg.id, VOICE_BARS));
    }
}

/**
 * Attach a voice message to its player, decrypting first when needed. The
 * <audio> element is the engine and stays hidden; `parts` is the visible
 * player from voice-ui.js.
 */
async function fillVoiceContent(player, status, msg, parts) {
    if (!player) return;

    if (!msg.encrypted) {
        player.src = msg.content;
        if (status) status.textContent = '';
        drawVoiceWaveform(parts, dataUrlBytes(msg.content), msg);
        return;
    }

    if (status) status.textContent = 'Decrypting voice message...';
    const opened = await openEncrypted(msg, { binary: true });

    if (opened.status !== 'ok') {
        player.remove();
        if (parts) parts.root.remove();
        if (status) {
            status.textContent = OPEN_NOTICES[opened.status]
                || 'Voice message - cannot be opened with the current key pair.';
            status.classList.add('undecryptable');
        }
        return;
    }
    const bytes = opened.bytes;

    const mime = (msg.extra && msg.extra.mime) || 'audio/webm';
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
    state.voiceObjectUrls.push(objectUrl);
    player.src = objectUrl;
    if (status) status.textContent = '';
    drawVoiceWaveform(parts, bytes, msg);

    // Recordings made before the sender's browser agreed on a common container
    // can arrive in one this engine cannot decode -- an iPhone receiving
    // WebM/Opus is the case that prompted this. The <audio> element fails
    // silently there: it loads, shows controls and plays nothing. Say so, and
    // hand over the file so the recording is not simply lost.
    if (player.canPlayType(mime) === '' && status) {
        if (parts) parts.root.remove();
        status.textContent = 'This browser cannot play ' + mime + '. ';
        status.classList.add('undecryptable');
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = 'voice-message' + (mime.includes('mp4') ? '.m4a' : '.webm');
        link.textContent = 'Download instead';
        link.className = 'voice-download-link';
        status.appendChild(link);
    }
}

/** Object URLs stay alive until revoked; drop them when the transcript clears. */
function revokeVoiceObjectUrls() {
    state.voiceObjectUrls.forEach(url => URL.revokeObjectURL(url));
    state.voiceObjectUrls = [];
}

export function tamperPanelFor(messageId) {
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
export async function runShareAnalysis(msg, { force = false, panel = null } = {}) {
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
            if (response.status === 404) {
                setShareActionsDisabled(panel, true);
                // Opening a share uses it up. The verdict it was opened on
                // still holds, so keep showing that rather than replacing it
                // with the server's "already opened".
                if (cached) {
                    renderTamperPanel(panel, cached);
                    return cached;
                }
                throw new Error('This share was already opened, revoked or has expired, '
                    + 'so it can no longer be checked.');
            }
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
export async function runLocalShareVerification(messageId, share1DataUrl, panel) {
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
    bubble.querySelectorAll('.share-action[data-action="reconstruct"], '
        + '.share-action[data-action="recheck"]').forEach(button => {
        button.disabled = disabled;
    });
}

export function renderTamperPanel(panel, data, local) {
    local = local || state.localAnalysis[data.message_id];
    const endToEnd = state.endToEndHash[data.message_id];
    // The rules (any failing check makes it "tampered", which copy each row
    // describes, the server/browser disagreement) live in share-ui.js, where
    // tests/js/share-ui.test.mjs checks them; this only picks the inputs.
    const summary = window.revealxShareUI.verdictSummary(data, local, endToEnd);
    window.revealxShareUI.drawVerdict(document, panel, summary);
}

/**
 * Show the rebuilt picture in its bubble, so an image share ends up looking
 * like an image. Memory only, like the reconstruction panel itself.
 */
export function revealShareImage(messageId, src) {
    const root = messagesContainer.querySelector(
        `[data-message-id="${messageId}"] .share-card`
    );
    if (!root || !src) return;
    window.revealxShareUI.showImage(document, {
        root,
        tile: root.querySelector('.share-tile'),
        caption: root.querySelector('.share-caption'),
        detail: root.querySelector('.share-detail'),
    }, src);
    // Share 1 was fetched once to build this; it cannot be fetched or
    // checked again, so neither action is offered any more.
    root.closest('.message').querySelectorAll(
        '.share-action[data-action="reconstruct"], .share-action[data-action="recheck"]'
    ).forEach(button => { button.disabled = true; });
}

/** Draw a status chip from chips.js: an aria-hidden glyph and the words. */
function fillChip(node, chip) {
    if (!node || !chip) return;
    node.className = `msg-chip ${chip.kind}`;
    node.title = chip.title || '';
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = chip.icon;
    const words = document.createElement('span');
    words.textContent = chip.text;
    node.replaceChildren(icon, words);
}

/** Whole minutes until a share expires, from the server's own expires_at. */
function shareMinutesLeft(msg) {
    const created = parseServerTime(msg.created_at) || new Date();
    const expires = parseServerTime(msg.expires_at) || new Date(created.getTime() + 60 * 60 * 1000);
    return Math.max(0, Math.round((expires - new Date()) / 60000));
}

export function displayMessage(msg) {
    const messageDiv = document.createElement('div');
    messageDiv.dataset.messageId = msg.id;

    // Remember or restore live Share 2, in memory only (see share2Memory).
    if (msg.type === 'share') {
        if (msg.extra && msg.extra.share2_live) {
            share2Memory.set(String(msg.id), msg.extra.share2_live);
        } else if (msg.extra) {
            const remembered = share2Memory.get(String(msg.id));
            if (remembered) {
                msg.extra.share2_live = remembered;
            }
        }
    }

    const isSent = msg.sender_id === state.currentUser.id;
    messageDiv.className = `message ${isSent ? 'sent' : 'received'}`;
    if (isSent) messageDiv.dataset.read = msg.read_at ? 'true' : 'false';

    // Every message gets one status chip (chips.js) saying in words what
    // happened to it -- "E2EE", "Not encrypted", or for a share how long it
    // can still be opened. It replaces a bare header padlock, a red
    // "[not encrypted]" badge and a red one-time notice: red is for tamper
    // and error states only.
    const isEncrypted = msg.encrypted === true;

    const sanitizedUsername = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(escapeHtml(msg.username)) : escapeHtml(msg.username);

    if (msg.type === 'text') {
        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername}</div>
            <div class="message-content"><span class="message-text"></span></div>
            <div class="message-meta">
                <span class="msg-chip"></span>
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction" aria-haspopup="true" aria-expanded="false"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-react"></use></svg></button>
                <span class="message-menu-wrap">
                    <button class="btn-message-menu" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for this message"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-more"></use></svg></button>
                    <span class="message-menu" role="menu" hidden>
                        <button class="message-menu-item danger" type="button" role="menuitem">${isSent ? 'Delete for everyone' : 'Delete for me'}</button>
                    </span>
                </span>
            </div>
            <div class="message-reactions"></div>
        `;

        // textContent, so message bodies can never introduce markup.
        fillTextContent(messageDiv.querySelector('.message-text'), msg);

        // Delete sits in a per-message menu (dialogs.js) rather than as a bare
        // "x" beside the timestamp, which was easy to hit by accident on a
        // phone; deleteMessage then asks in an in-app dialog.
        const menuBtn = messageDiv.querySelector('.btn-message-menu');
        const menu = messageDiv.querySelector('.message-menu');
        if (menuBtn && menu) {
            const control = window.revealxDialogs.wireMenu(menuBtn, menu);
            menu.querySelector('[role="menuitem"]').addEventListener('click', () => {
                control.close(true);
                late.deleteMessage(msg.id, isSent);
            });
        }

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }

    } else if (msg.type === 'voice') {
        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername}</div>
            <div class="message-content">
                <span class="voice-note-slot"></span>
                <audio class="voice-player" preload="metadata" hidden></audio>
                <span class="voice-status"></span>
            </div>
            <div class="message-meta">
                <span class="msg-chip"></span>
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction" aria-haspopup="true" aria-expanded="false"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-react"></use></svg></button>
                <span class="message-menu-wrap">
                    <button class="btn-message-menu" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for this message"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-more"></use></svg></button>
                    <span class="message-menu" role="menu" hidden>
                        <button class="message-menu-item danger" type="button" role="menuitem">${isSent ? 'Delete for everyone' : 'Delete for me'}</button>
                    </span>
                </span>
            </div>
            <div class="message-reactions"></div>
        `;

        // The browser's own <audio controls> looked different everywhere and
        // showed "0:00 / Infinity" for a fresh WebM recording. voice-ui.js
        // draws the player; the <audio> element underneath plays the sound.
        const voicePlayer = messageDiv.querySelector('.voice-player');
        const voiceParts = window.revealxVoiceUI.buildVoicePlayer(document, { bars: VOICE_BARS });
        messageDiv.querySelector('.voice-note-slot').replaceWith(voiceParts.root);
        window.revealxVoiceUI.attachVoicePlayer(voiceParts, voicePlayer);
        fillVoiceContent(
            voicePlayer,
            messageDiv.querySelector('.voice-status'),
            msg,
            voiceParts
        );

        // Delete sits in a per-message menu (dialogs.js) rather than as a bare
        // "x" beside the timestamp, which was easy to hit by accident on a
        // phone; deleteMessage then asks in an in-app dialog.
        const menuBtn = messageDiv.querySelector('.btn-message-menu');
        const menu = messageDiv.querySelector('.message-menu');
        if (menuBtn && menu) {
            const control = window.revealxDialogs.wireMenu(menuBtn, menu);
            menu.querySelector('[role="menuitem"]').addEventListener('click', () => {
                control.close(true);
                late.deleteMessage(msg.id, isSent);
            });
        }

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }

    } else if (msg.type === 'share') {
        const hasShare1 = Boolean(msg.extra.share1_url);
        // A browser-split share carries Share 2 sealed in the message itself,
        // so it survives a reload and an offline recipient.
        const hasLiveShare2 = Boolean(msg.extra.share2_live) || isClientSplitShare(msg);



        // Said as plain words, and set as text below: the recipient's name is
        // another user's input and must not be interpolated into markup.
        const recipient = state.users.find(u => u.id === msg.recipient_id);
        const recipientName = recipient ? recipient.username : 'the recipient';
        const statusMessage = isSent
            ? `Only ${recipientName} can rebuild this image. You can revoke it until they open it.`
            : isClientSplitShare(msg)
              ? 'Share 2 arrived end-to-end encrypted. Verify and reconstruct locally.'
              : hasLiveShare2
              ? 'Live Share 2 received. Verify and reconstruct Share 1 locally.'
              : 'Share 1 available. Share 2 was only sent live. You cannot reconstruct.';

        messageDiv.innerHTML = `
            <div class="message-username">${isSent ? 'You' : sanitizedUsername}</div>
            <div class="message-content share-content">
                <div class="share-card-slot"></div>
                ${!isSent ? '<div class="tamper-panel" data-role="tamper-panel"></div>' : ''}
                <div class="share-actions">
                    ${!isSent ? `<button class="share-action primary" type="button" data-action="reconstruct" ${hasShare1 && hasLiveShare2 ? '' : 'disabled'}><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-rebuild"></use></svg>Reconstruct</button>` : ''}
                    ${!isSent && hasShare1 ? '<button class="share-action share-recheck" type="button" data-action="recheck" aria-label="Re-check integrity" title="Re-check integrity"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-shield-check"></use></svg><span class="btn-label">Re-check</span></button>' : ''}
                    ${isSent ? '<button class="share-action secondary" type="button" data-action="revoke">Revoke</button>' : ''}
                </div>
            </div>
            <div class="message-meta">
                <span class="msg-chip"></span>
                <span class="message-time">${localTimeLabel(msg)}</span>
                ${isSent ? `<span class="message-status">${msg.read_at ? 'Read' : 'Sent'}</span>` : ''}
                <button class="btn-react" data-message-id="${msg.id}" title="React" aria-label="Add a reaction" aria-haspopup="true" aria-expanded="false"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-react"></use></svg></button>
                <span class="message-menu-wrap">
                    <button class="btn-message-menu" type="button" aria-haspopup="menu" aria-expanded="false" aria-label="More actions for this message"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-more"></use></svg></button>
                    <span class="message-menu" role="menu" hidden>
                        <button class="message-menu-item danger" type="button" role="menuitem">${isSent ? 'Delete for everyone' : 'Delete for me'}</button>
                    </span>
                </span>
            </div>
            <div class="message-reactions"></div>
        `;

        // The sealed tile: what a picture looks like before it is rebuilt.
        const card = window.revealxShareUI.buildShareCard(document, { sent: isSent });
        messageDiv.querySelector('.share-card-slot').replaceWith(card.root);
        messageDiv.querySelector('.share-status').textContent = statusMessage;
        messageDiv.querySelectorAll('.share-action').forEach(button => {
            button.addEventListener('click', () => {
                const action = button.dataset.action;
                if (action === 'reconstruct') {
                    late.openReconstruction(msg);
                } else if (action === 'recheck') {
                    runShareAnalysis(msg, { force: true });
                } else if (action === 'revoke') {
                    window.revealxDialogs.askConfirm({
                        title: 'Revoke this image share?',
                        body: 'The recipient will no longer be able to open it.',
                        confirmLabel: 'Revoke',
                    }).then((agreed) => {
                        if (agreed) socket.emit('revoke_share', { message_id: msg.id });
                    });
                }
            });
        });

        // Real-time tamper detection: as soon as a share lands in the chat the
        // recipient's client verifies the stored Share 1 (HMAC + ML) before
        // anyone reconstructs anything. Results are cached per message.
        if (!isSent && hasShare1) {
            runShareAnalysis(msg, { panel: messageDiv.querySelector('[data-role="tamper-panel"]') });
        }

        // Delete sits in a per-message menu (dialogs.js) rather than as a bare
        // "x" beside the timestamp, which was easy to hit by accident on a
        // phone; deleteMessage then asks in an in-app dialog.
        const menuBtn = messageDiv.querySelector('.btn-message-menu');
        const menu = messageDiv.querySelector('.message-menu');
        if (menuBtn && menu) {
            const control = window.revealxDialogs.wireMenu(menuBtn, menu);
            menu.querySelector('[role="menuitem"]').addEventListener('click', () => {
                control.close(true);
                late.deleteMessage(msg.id, isSent);
            });
        }

        const reactBtn = messageDiv.querySelector('.btn-react');
        if (reactBtn) reactBtn.addEventListener('click', () => toggleReactionPicker(msg.id, reactBtn));
        if (msg.extra && msg.extra.reactions) {
            // Drawn after the node is appended, so the lookup by id resolves.
            setTimeout(() => renderReactions(msg.id, msg.extra.reactions), 0);
        }
    }

    fillChip(messageDiv.querySelector('.msg-chip'), window.revealxChips.messageChip({

        type: msg.type,

        encrypted: isEncrypted,

        minutesLeft: msg.type === 'share' ? shareMinutesLeft(msg) : undefined,

    }));

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

export function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

