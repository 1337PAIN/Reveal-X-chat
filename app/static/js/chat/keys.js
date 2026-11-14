// E2EE keys: setup, the key manager, pinning and safety numbers.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import {
    socket,
    state,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';

registerLate({ closeKeyManagerModal, peerPublicKey });

// ==========================================
// 6. E2E Key Management & Verification
// ==========================================

// Real ECDH P-256 key agreement. The private key never leaves the browser and
// is generated non-extractable; only the base64 SPKI public key is published.
export async function initE2EEKeys(options = {}) {
    if (!state.currentUser) return '';

    const publicKey = await window.revealxE2EE.init(state.currentUser.id, options);
    state.keys.publicKey = publicKey;

    if (publicKey) {
        socket.emit('register_public_key', { public_key: publicKey });
    }
    renderKeyManager();
    // The user list can arrive before the key store is open; pins checked
    // then would have been skipped.
    refreshPeerPins();
    return publicKey;
}

export function peerPublicKey(userId) {
    const user = state.users.find(u => u.id === userId);
    return (user && user.public_key) || '';
}

// 'ready' means both sides have published a key, so messages to this peer are
// actually encrypted. Anything else is reported honestly instead of showing a
// padlock that means nothing.
export function keyExchangeState(userId) {
    if (!window.revealxE2EE.isReady()) return 'unavailable';
    return peerPublicKey(userId) ? 'ready' : 'pending';
}

/**
 * Compare a peer's published key with the pin on this device.
 *
 * Returns the pin status from e2ee.js. A 'changed' result is recorded in
 * state.keyChanges, which blocks sending to that peer until the user accepts
 * the new key -- the server distributes keys, so a silent change is exactly
 * what substituting one would look like.
 */
export async function checkPeerPin(userId) {
    const current = peerPublicKey(userId);
    if (!current || !window.revealxE2EE.isReady()) return { status: 'none' };
    const result = await window.revealxE2EE.checkPeerKey(userId, current);
    if (result.status === 'changed') {
        state.keyChanges.set(userId, { previous: result.previous, current });
    } else {
        state.keyChanges.delete(userId);
    }
    return result;
}

export async function refreshPeerPins() {
    for (const user of state.users) {
        if (user.public_key) await checkPeerPin(user.id);
    }
    if (state.selectedRecipientId && state.keyChanges.has(state.selectedRecipientId)) {
        openSafetyChangeDialog(state.selectedRecipientId);
    }
}

export async function openSafetyChangeDialog(userId) {
    const modal = document.getElementById('safetyChangeModal');
    const change = state.keyChanges.get(userId);
    if (!modal || !change) return;
    const user = state.users.find(u => u.id === userId);
    const name = user ? user.username : 'This contact';

    document.getElementById('safetyChangeText').textContent =
        `${name}'s encryption key is different from the one this device saw before. ` +
        'They may have reinstalled or used a new browser, or someone may be ' +
        'intercepting the conversation. Messages to them are on hold until you ' +
        'continue. Compare the new safety number with them in person or over a ' +
        'call before trusting it.';
    document.getElementById('safetyChangeNumber').textContent =
        await window.revealxE2EE.safetyNumber(change.current);
    modal.dataset.userId = userId;
    modal.classList.remove('hidden');
}

const safetyChangeAccept = document.getElementById('safetyChangeAccept');
if (safetyChangeAccept) {
    safetyChangeAccept.addEventListener('click', async () => {
        const modal = document.getElementById('safetyChangeModal');
        const userId = modal && modal.dataset.userId;
        const change = userId && state.keyChanges.get(userId);
        if (change) {
            await window.revealxE2EE.acceptPeerKey(userId, change.current);
            state.keyChanges.delete(userId);
        }
        if (modal) modal.classList.add('hidden');
        renderKeyManager();
    });
}

const safetyChangeCancel = document.getElementById('safetyChangeCancel');
if (safetyChangeCancel) {
    safetyChangeCancel.addEventListener('click', () => {
        const modal = document.getElementById('safetyChangeModal');
        if (modal) modal.classList.add('hidden');
    });
}

export async function renderKeyManager() {
    const display = document.getElementById('publicKeyDisplay');
    if (display) {
        if (!window.revealxE2EE.available) {
            display.textContent = 'WebCrypto is unavailable in this browser. Messages are sent unencrypted.';
        } else if (state.keys.publicKey) {
            display.textContent = state.keys.publicKey;
        } else {
            display.textContent = 'Generating key pair...';
        }
    }

    const exchangeList = document.getElementById('keyExchangeList');
    if (!exchangeList) return;

    exchangeList.innerHTML = '';
    if (state.users.length === 0) {
        exchangeList.innerHTML = '<div class="key-exchange-row"><span>No other accounts yet</span></div>';
        return;
    }

    for (const user of state.users) {
        const status = keyExchangeState(user.id);
        const label = {
            ready: '🔒 Key exchanged',
            pending: '⏳ No key published yet',
            unavailable: '⚠️ E2EE unavailable',
        }[status];

        const item = document.createElement('div');
        item.className = `key-exchange-row ${status}`;

        const name = document.createElement('span');
        name.className = 'key-exchange-name';
        name.textContent = user.username;

        const state_ = document.createElement('span');
        state_.className = 'key-exchange-state';
        state_.textContent = label;

        item.append(name, state_);

        if (status === 'ready') {
            const pin = await checkPeerPin(user.id);

            const number = document.createElement('small');
            number.className = 'key-exchange-fingerprint';
            number.textContent = `Safety number: ${await window.revealxE2EE.safetyNumber(user.public_key)}`;
            item.appendChild(number);

            if (pin.status === 'changed') {
                item.classList.add('changed');
                state_.textContent = '⚠️ Key changed - review';
                const review = document.createElement('button');
                review.type = 'button';
                review.className = 'btn btn-warning btn-sm';
                review.textContent = 'Review change';
                review.addEventListener('click', () => openSafetyChangeDialog(user.id));
                item.appendChild(review);
            } else {
                // Verified means the two people compared this number out of
                // band. It is recorded per pinned key, so a later key change
                // clears it rather than carrying it over.
                const label = document.createElement('label');
                label.className = 'key-verified-toggle';
                const box = document.createElement('input');
                box.type = 'checkbox';
                box.checked = Boolean(pin.verified);
                box.addEventListener('change', async () => {
                    const saved = await window.revealxE2EE.setPeerVerified(
                        user.id, user.public_key, box.checked);
                    if (!saved) box.checked = false;
                    renderKeyManager();
                });
                label.append(box, document.createTextNode(' Mark as verified'));
                item.appendChild(label);
                if (pin.verified) state_.textContent = '✅ Verified';
            }
        }

        exchangeList.appendChild(item);
    }
}

export function openKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (!modal) return;

    renderKeyManager();
    if (!state.keys.publicKey) initE2EEKeys();

    modal.classList.remove('hidden');
}

function closeKeyManagerModal() {
    const modal = document.getElementById('keyManagerModal');
    if (modal) modal.classList.add('hidden');
}

const regenerateKeyBtn = document.getElementById('regenerateKeyBtn');
if (regenerateKeyBtn) {
    regenerateKeyBtn.addEventListener('click', async () => {
        const agreed = await window.revealxDialogs.askConfirm({
            title: 'Generate a new key pair?',
            body: 'Messages already in your chats were encrypted to your current key and will no longer open.',
            confirmLabel: 'Generate new keys',
        });
        if (!agreed) return;
        announceToScreenReader('Regenerating encryption key pair...');
        const publicKey = await initE2EEKeys({ regenerate: true });
        if (publicKey) {
            window.revealxDialogs.toast('New key pair generated and published.');
        } else {
            window.revealxDialogs.toast('Key generation failed. Nothing will be sent until encryption works again.', { kind: 'error' });
        }
    });
}

document.querySelectorAll('#closeKeyManagerBtn, #closeKeyManagerBtn2').forEach(btn => {
    btn.addEventListener('click', closeKeyManagerModal);
});

