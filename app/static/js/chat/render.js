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

registerLate({ e2eeContext, filterVisibleMessages });

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
 * The E2EE v2 context for a stored message: what e2ee.js authenticates as
 * AES-GCM associated data. The ids are the server's, from the row itself, so
 * a row relabelled with another sender or recipient no longer decrypts.
 */
export function e2eeContext(msg) {
    const extra = msg.extra || {};
    return {
        v: extra.v,
        senderId: msg.sender_id,
        recipientId: msg.recipient_id,
        msgUuid: extra.msg_uuid,
    };
}

// "<sorted user ids>|<msg_uuid>" -> the first message id seen carrying it.
//
// The server refuses a msg_uuid it has already stored, but the server is the
// party E2EE does not trust. A server replaying a ciphertext has to keep its
// msg_uuid -- it is authenticated -- so a second row with the same uuid and a
// different id is a replay, and is not shown as a fresh message.
const seenMessageUuids = new Map();

function isReplayedMessage(msg) {
    const extra = msg.extra || {};
    if (extra.v !== 2 || !extra.msg_uuid) return false;
    const key = `${[msg.sender_id, msg.recipient_id].sort().join('|')}|${extra.msg_uuid}`;
    const first = seenMessageUuids.get(key);
    if (!first) {
        seenMessageUuids.set(key, msg.id);
        return false;
    }
    return first !== msg.id;
}

const REPLAYED_NOTICE = 'Repeated message - this encrypted message was already delivered, so it is not shown again.';

/** Fill a message body, decrypting first when the payload is E2EE. */
async function fillTextContent(node, msg) {
    if (!node) return;

    if (!msg.encrypted) {
        node.textContent = msg.content;
        return;
    }

    if (isReplayedMessage(msg)) {
        node.textContent = REPLAYED_NOTICE;
        node.classList.add('undecryptable');
        return;
    }

    node.textContent = 'Decrypting...';
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = peerPublicKey(otherId);
    const iv = msg.extra ? msg.extra.iv : null;
    const plaintext = peerKey
        ? await window.revealxE2EE.decrypt(peerKey, msg.content, iv, e2eeContext(msg))
        : null;

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

    if (isReplayedMessage(msg)) {
        player.remove();
        if (status) {
            status.textContent = REPLAYED_NOTICE;
            status.classList.add('undecryptable');
        }
        return;
    }

    if (status) status.textContent = 'Decrypting voice message...';
    const otherId = msg.sender_id === state.currentUser.id ? msg.recipient_id : msg.sender_id;
    const peerKey = peerPublicKey(otherId);
    const iv = msg.extra ? msg.extra.iv : null;
    const bytes = peerKey
        ? await window.revealxE2EE.decryptBytes(peerKey, msg.content, iv, e2eeContext(msg))
        : null;

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

    // Recordings made before the sender's browser agreed on a common container
    // can arrive in one this engine cannot decode -- an iPhone receiving
    // WebM/Opus is the case that prompted this. The <audio> element fails
    // silently there: it loads, shows controls and plays nothing. Say so, and
    // hand over the file so the recording is not simply lost.
    if (player.canPlayType(mime) === '' && status) {
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
    bubble.querySelectorAll('.share-action[data-action="reconstruct"]').forEach(button => {
        button.disabled = disabled;
    });
}

export function renderTamperPanel(panel, data, local) {
    local = local || state.localAnalysis[data.message_id];
    const endToEnd = state.endToEndHash[data.message_id];
    const tampered = data.integrity.exact_tamper_detected || data.ml.tampered
        || (local ? local.tampered : false) || (endToEnd ? !endToEnd.matches : false);
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

    // The only row that does not rely on the server at all: the sender's
    // browser hashed Share 1 before uploading it and sealed the hash in the
    // envelope, so a server that altered the share *and* its own HMAC record
    // is still caught here.
    if (endToEnd) {
        const hashRow = document.createElement('div');
        hashRow.className = `tamper-row ${endToEnd.matches ? 'ok' : 'bad'}`;
        hashRow.textContent = endToEnd.matches
            ? "End-to-end SHA-256: match (the sender's Share 1, unchanged)"
            : 'End-to-end SHA-256: MISMATCH (not the Share 1 the sender made)';
        rows.appendChild(hashRow);
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
                <audio class="voice-player" controls preload="metadata"></audio>
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

        fillVoiceContent(
            messageDiv.querySelector('.voice-player'),
            messageDiv.querySelector('.voice-status'),
            msg
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
            <div class="message-content">
                <div class="encrypted-image-notice">
                    <span class="share-status"></span>
                </div>
                ${!isSent ? '<div class="tamper-panel" data-role="tamper-panel"></div>' : ''}
                <div class="share-actions">
                    ${!isSent ? `<button class="share-action primary" type="button" data-action="reconstruct" ${hasShare1 && hasLiveShare2 ? '' : 'disabled'}>Reconstruct</button>` : ''}
                    ${!isSent && hasShare1 ? '<button class="share-action" type="button" data-action="recheck">Re-check integrity</button>' : ''}
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

