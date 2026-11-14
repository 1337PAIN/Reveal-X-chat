/**
 * Reveal-X end-to-end encryption for private text messages.
 *
 * ECDH P-256 for key agreement, HKDF-SHA256 to turn the shared secret into a
 * message key, AES-GCM for the message itself. The private key is generated
 * non-extractable and kept in IndexedDB, so it cannot be read back out by
 * script - an XSS bug can abuse the session but cannot exfiltrate the key.
 *
 * The server only ever sees the public key and the ciphertext.
 *
 * Protocol v3 (current, and the only one this file opens).
 *
 *   v1 derived one key per *pair* of users with no associated data, so the
 *   server could reflect Alice's own message back to her as Bob's, or deliver
 *   it twice. v2 fixed both directions and bound the sender, recipient and a
 *   msg_uuid. v3 keeps that and adds two things:
 *
 *   - The conversation. AES-GCM additional data is
 *     "v3|<conversation>|<sender_id>|<recipient_id>|<msg_uuid>|<sent_at>",
 *     where conversation is "dm" for a direct message ("group:<id>" is
 *     reserved). A ciphertext cannot be moved into another conversation.
 *   - The sender's clock. sent_at is integer milliseconds from the sender,
 *     authenticated, so openMessage can refuse anything more than 65 minutes
 *     old or 5 minutes ahead -- a stored ciphertext cannot be replayed later
 *     as new. Freshness proves the *sender* wrote it in that window by their
 *     clock; it does not prove when the server delivered it.
 *
 *   The replay check (same msg_uuid on a second row) now lives in IndexedDB,
 *   so a reload no longer forgets what was seen.
 *
 * v1 and v2 decryption is gone, not kept as a fallback: a fallback is a
 * downgrade path. Messages expire after an hour on the server, so an hour
 * after this is deployed no v1 or v2 row is left to open; until then such a
 * row is shown as "sent before an update".
 */

const RX_DB_NAME = 'revealx-e2ee';
const RX_STORE = 'keypairs';
const RX_INFO_V3 = 'reveal-x-message-key-v3';
const RX_PROTOCOL = 3;

// Freshness window for the authenticated sent_at. 65 minutes is the message
// lifetime (one hour) plus five minutes of clock difference between sender
// and reader; 5 minutes ahead allows the same difference the other way.
const RX_MAX_AGE_MS = 65 * 60 * 1000;
const RX_MAX_AHEAD_MS = 5 * 60 * 1000;

// "<sender public key>|<recipient public key>" -> CryptoKey.
const derivedKeyCache = new Map();

// No field may contain the separator: "a|b" + "c" and "a" + "b|c" would
// otherwise be the same associated data for two different contexts.
function aadField(value) {
    const text = String(value);
    return text.includes('|') ? null : text;
}

/** The v3 associated data, or null when a field cannot be encoded safely. */
function associatedData(context) {
    const conversation = context.conversation || 'dm';
    if (conversation !== 'dm' && !/^group:[^|]+$/.test(conversation)) return null;
    if (!Number.isSafeInteger(context.sentAt) || context.sentAt < 0) return null;
    const fields = [context.senderId, context.recipientId, context.msgUuid].map(aadField);
    if (fields.some((field) => field === null || field === '')) return null;
    return new TextEncoder().encode(
        `v3|${conversation}|${fields[0]}|${fields[1]}|${fields[2]}|${context.sentAt}`);
}

// What a reader has already been shown, keyed
// "<conversation>|<sorted user ids>|<msg_uuid>" -> {messageId, expiresAt}. In
// its own database for the same reason the pins are: no version upgrade of
// the database that holds private keys.
const RX_REPLAY_DB_NAME = 'revealx-e2ee-replay';
const RX_REPLAY_STORE = 'seen';

function newMessageUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    // RFC 4122 v4 from getRandomValues, for engines without randomUUID.
    const b = window.crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function bufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i += 1) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
}

function base64ToBuffer(value) {
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

// Peers' public keys as first seen, per local account. A separate database
// rather than a second store in RX_DB_NAME: adding a store means a version
// upgrade of a database that already holds every user's private key, and a
// failed upgrade there would be far worse than a missing pin.
const RX_PIN_DB_NAME = 'revealx-e2ee-pins';
const RX_PIN_STORE = 'pins';

// Safety number derivation. Iterated so that grinding for a key whose number
// collides with a victim's costs more per attempt; the digit layout follows
// the scheme Signal popularised (30 digits per key, both halves sorted).
const RX_SAFETY_VERSION = 'reveal-x-safety-number-v1';
const RX_SAFETY_ITERATIONS = 5200;
const safetyHalfCache = new Map(); // publicKeyB64 -> 30 digits

function openStore(dbName, storeName) {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(dbName, 1);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(storeName)) {
                request.result.createObjectStore(storeName);
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Could not open key store'));
    });
}

function storeRequest(dbName, storeName, mode, action) {
    return openStore(dbName, storeName).then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, mode);
        const request = action(tx.objectStore(storeName));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Key store operation failed'));
    }));
}

function keyStoreRequest(mode, action) {
    return storeRequest(RX_DB_NAME, RX_STORE, mode, action);
}

function pinStoreRequest(mode, action) {
    return storeRequest(RX_PIN_DB_NAME, RX_PIN_STORE, mode, action);
}

function replayStoreRequest(mode, action) {
    return storeRequest(RX_REPLAY_DB_NAME, RX_REPLAY_STORE, mode, action);
}

/** 30 decimal digits identifying one public key. */
async function safetyHalf(publicKeyB64) {
    const cached = safetyHalfCache.get(publicKeyB64);
    if (cached) return cached;

    const key = new Uint8Array(base64ToBuffer(publicKeyB64));
    const prefix = new TextEncoder().encode(`${RX_SAFETY_VERSION}|`);
    let hash = new Uint8Array(prefix.length + key.length);
    hash.set(prefix);
    hash.set(key, prefix.length);
    for (let i = 0; i < RX_SAFETY_ITERATIONS; i += 1) {
        const input = new Uint8Array(hash.length + key.length);
        input.set(hash);
        input.set(key, hash.length);
        hash = new Uint8Array(await window.crypto.subtle.digest('SHA-512', input));
    }

    let digits = '';
    for (let chunk = 0; chunk < 6; chunk += 1) {
        // Five bytes is 40 bits, well inside a double's exact integer range.
        let value = 0;
        for (let i = 0; i < 5; i += 1) value = value * 256 + hash[chunk * 5 + i];
        digits += String(value % 100000).padStart(5, '0');
    }
    safetyHalfCache.set(publicKeyB64, digits);
    return digits;
}

/**
 * The E2EE session for the signed-in user. `publicKey` is base64 SPKI and is
 * the only part that is ever published.
 */
class E2EESession {
    constructor() {
        this.userId = null;
        this.keyPair = null;
        this.publicKey = '';
        this.available = Boolean(window.crypto && window.crypto.subtle && window.indexedDB);
        // The replay check's in-page copy. IndexedDB is the record that
        // survives a reload; this keeps the check working when IndexedDB is
        // refused (some private windows) and answers repeat lookups quickly.
        this.seenInPage = new Map();
        this.replayStoreBroken = false;
        this.replayPruned = null;
        // Replay checks run one at a time: two rows with the same uuid opened
        // concurrently must not both find "not seen yet".
        this.replayQueue = Promise.resolve();
    }

    /** Load this user's key pair from IndexedDB, generating one on first use. */
    async init(userId, { regenerate = false } = {}) {
        this.userId = userId;
        derivedKeyCache.clear();

        if (!this.available) {
            this.keyPair = null;
            this.publicKey = '';
            return '';
        }

        try {
            let stored = regenerate ? null : await keyStoreRequest('readonly', (store) => store.get(userId));

            if (!stored) {
                // extractable=false applies to the private key; per the WebCrypto
                // spec the public half stays exportable so we can publish it.
                const keyPair = await window.crypto.subtle.generateKey(
                    { name: 'ECDH', namedCurve: 'P-256' },
                    false,
                    ['deriveKey', 'deriveBits']
                );
                const spki = await window.crypto.subtle.exportKey('spki', keyPair.publicKey);
                stored = { keyPair, publicKey: bufferToBase64(spki) };
                await keyStoreRequest('readwrite', (store) => store.put(stored, userId));
            }

            this.keyPair = stored.keyPair;
            this.publicKey = stored.publicKey;
            return this.publicKey;
        } catch (error) {
            console.error('E2EE key setup failed', error);
            this.keyPair = null;
            this.publicKey = '';
            return '';
        }
    }

    isReady() {
        return Boolean(this.keyPair && this.publicKey);
    }

    /**
     * The 60-digit safety number for this conversation, as 12 groups of 5.
     *
     * Replaces a fingerprint that was the first 8 bytes of SHA-256: 64 bits
     * is a 2^32 birthday bound for an attacker who chooses their own key, and
     * it covered one key, so two people had to compare two values each. The
     * safety number covers both keys, sorted, so both screens show the same
     * single number and either key changing changes it.
     */
    async safetyNumber(peerPublicKeyB64) {
        if (!this.available || !this.publicKey || !peerPublicKeyB64) return '';
        const halves = [await safetyHalf(this.publicKey), await safetyHalf(peerPublicKeyB64)].sort();
        return halves.join('').match(/\d{5}/g).join(' ');
    }

    /**
     * Compare a peer's published key with the one pinned for them.
     *
     * The server distributes public keys, so it is in a position to swap one:
     * Alice would encrypt to a key the server holds and nothing would look
     * different. The first key seen for a peer is pinned (trust on first
     * use); any later difference is reported as 'changed' and is *not*
     * adopted until acceptPeerKey is called. Returns
     * {status: 'none'|'new'|'match'|'changed', verified, previous}.
     */
    async checkPeerKey(peerId, peerPublicKeyB64) {
        if (!peerPublicKeyB64) return { status: 'none', verified: false };
        if (!this.available || !this.userId) return { status: 'new', verified: false };

        const pinId = `${this.userId}|${peerId}`;
        const pin = await pinStoreRequest('readonly', (store) => store.get(pinId));
        if (!pin) {
            await pinStoreRequest('readwrite', (store) => store.put(
                { publicKey: peerPublicKeyB64, verified: false, pinnedAt: Date.now() }, pinId));
            return { status: 'new', verified: false };
        }
        if (pin.publicKey === peerPublicKeyB64) {
            return { status: 'match', verified: Boolean(pin.verified) };
        }
        return { status: 'changed', verified: false, previous: pin.publicKey };
    }

    /** The user saw the change and chose to continue: pin the new key. */
    async acceptPeerKey(peerId, peerPublicKeyB64) {
        if (!this.available || !this.userId || !peerPublicKeyB64) return;
        await pinStoreRequest('readwrite', (store) => store.put(
            { publicKey: peerPublicKeyB64, verified: false, pinnedAt: Date.now() },
            `${this.userId}|${peerId}`));
    }

    /**
     * Record that the safety number was compared out of band. Only for the
     * key currently pinned, so a verification can never be carried onto a
     * key that was swapped in afterwards. Returns whether it was recorded.
     */
    async setPeerVerified(peerId, peerPublicKeyB64, verified) {
        if (!this.available || !this.userId) return false;
        const pinId = `${this.userId}|${peerId}`;
        const pin = await pinStoreRequest('readonly', (store) => store.get(pinId));
        if (!pin || pin.publicKey !== peerPublicKeyB64) return false;
        await pinStoreRequest('readwrite', (store) => store.put(
            { ...pin, verified: Boolean(verified) }, pinId));
        return true;
    }

    /**
     * Derive an AES-GCM key for one direction of a conversation with a peer.
     *
     * The HKDF salt is bound to both public keys (sorted, so each side computes
     * the same value), and the HKDF info names the direction: sender's public
     * key, then recipient's. A->B and B->A are different keys, so one
     * direction's ciphertext cannot be replayed as the other's.
     */
    async deriveKeyFor(peerPublicKeyB64, direction) {
        if (!this.isReady() || !peerPublicKeyB64 || !direction) return null;

        const cacheKey = `${direction.senderPublicKey}|${direction.recipientPublicKey}`;
        const cached = derivedKeyCache.get(cacheKey);
        if (cached) return cached;

        const peerKey = await window.crypto.subtle.importKey(
            'spki',
            base64ToBuffer(peerPublicKeyB64),
            { name: 'ECDH', namedCurve: 'P-256' },
            false,
            []
        );

        const sharedBits = await window.crypto.subtle.deriveBits(
            { name: 'ECDH', public: peerKey },
            this.keyPair.privateKey,
            256
        );

        const pair = [this.publicKey, peerPublicKeyB64].sort().join('|');
        const salt = await window.crypto.subtle.digest('SHA-256', new TextEncoder().encode(pair));
        const info = `${RX_INFO_V3}|${direction.senderPublicKey}|${direction.recipientPublicKey}`;

        const hkdfKey = await window.crypto.subtle.importKey(
            'raw', sharedBits, 'HKDF', false, ['deriveKey']
        );
        const messageKey = await window.crypto.subtle.deriveKey(
            { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(info) },
            hkdfKey,
            { name: 'AES-GCM', length: 256 },
            false,
            ['encrypt', 'decrypt']
        );

        derivedKeyCache.set(cacheKey, messageKey);
        return messageKey;
    }

    /** Which way a message went, from this session's point of view. */
    direction(peerPublicKeyB64, senderId) {
        const outgoing = senderId === this.userId;
        return outgoing
            ? { senderPublicKey: this.publicKey, recipientPublicKey: peerPublicKeyB64 }
            : { senderPublicKey: peerPublicKeyB64, recipientPublicKey: this.publicKey };
    }

    /**
     * Encrypt bytes to a peer (used directly for voice recordings).
     *
     * `context.recipientId` is the peer's user id. `context.msgUuid` and
     * `context.sentAt` (this clock, integer ms) are filled in when absent;
     * `context.conversation` defaults to "dm". Returns {ciphertext, iv, v,
     * msg_uuid, sent_at} with the binary fields in base64, or null when the
     * peer has no key yet or a field cannot be encoded.
     */
    async encryptBytes(peerPublicKeyB64, bytes, context = {}) {
        const key = await this.deriveKeyFor(
            peerPublicKeyB64, this.direction(peerPublicKeyB64, this.userId));
        if (!key) return null;

        const msgUuid = context.msgUuid || newMessageUuid();
        const sentAt = context.sentAt === undefined ? Date.now() : context.sentAt;
        const additionalData = associatedData({
            conversation: context.conversation,
            senderId: this.userId,
            recipientId: context.recipientId,
            msgUuid,
            sentAt,
        });
        if (!additionalData) return null;
        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        const ciphertext = await window.crypto.subtle.encrypt(
            { name: 'AES-GCM', iv, additionalData }, key, bytes);
        return {
            ciphertext: bufferToBase64(ciphertext),
            iv: bufferToBase64(iv),
            v: RX_PROTOCOL,
            msg_uuid: msgUuid,
            sent_at: sentAt,
        };
    }

    /**
     * Decrypt to an ArrayBuffer, or null when the payload cannot be opened.
     *
     * `context` is rebuilt from the stored row: {v, conversation, senderId,
     * recipientId, msgUuid, sentAt}. Only v === 3 is ever tried. This is the
     * cryptography alone; the chat goes through openMessage, which also
     * checks freshness and replay.
     */
    async decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64, context = {}) {
        if (!ciphertextB64 || !ivB64 || context.v !== RX_PROTOCOL) return null;
        const additionalData = associatedData(context);
        if (!additionalData) return null;
        const key = await this.deriveKeyFor(
            peerPublicKeyB64, this.direction(peerPublicKeyB64, context.senderId));
        if (!key) return null;
        try {
            return await window.crypto.subtle.decrypt(
                { name: 'AES-GCM', iv: new Uint8Array(base64ToBuffer(ivB64)), additionalData },
                key, base64ToBuffer(ciphertextB64));
        } catch (error) {
            // Wrong key, rotated peer key, wrong direction or context, or a
            // tampered payload. AES-GCM does not say which, by design.
            return null;
        }
    }

    /**
     * Open a stored message the way the chat must: decrypt, then check the
     * authenticated sent_at is fresh, then check it is not a replay.
     *
     * Returns {status, text?, bytes?}. status is one of
     *   'legacy'       written by v1 or v2; never decrypted
     *   'failed'       did not authenticate (wrong key, tampered, relabelled)
     *   'unverifiable' authentic, but sent_at is outside the freshness window
     *   'replay'       authentic and fresh, but this uuid was already shown
     *                  under a different row id
     *   'ok'           with `text` (or `bytes` when options.binary)
     * Content is handed back only with 'ok'. The order matters: the replay
     * record is written only for a message that authenticated, so a forged
     * row cannot poison the record of a real one.
     */
    async openMessage(peerPublicKeyB64, ciphertextB64, ivB64, context = {}, options = {}) {
        if (context.v !== RX_PROTOCOL) return { status: 'legacy' };
        const bytes = await this.decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64, context);
        if (bytes === null) return { status: 'failed' };

        const now = Date.now();
        if (now - context.sentAt > RX_MAX_AGE_MS || context.sentAt - now > RX_MAX_AHEAD_MS) {
            return { status: 'unverifiable' };
        }
        if (await this.isReplay(context, options.messageId)) return { status: 'replay' };
        return options.binary
            ? { status: 'ok', bytes }
            : { status: 'ok', text: new TextDecoder().decode(bytes) };
    }

    /**
     * Has this (conversation, pair, msg_uuid) been shown under another row id?
     *
     * The server refuses a msg_uuid it already stored, but the server is the
     * party E2EE does not trust. A server replaying a ciphertext has to keep
     * its msg_uuid -- it is authenticated -- so a second row with the same
     * uuid and a different id is a replay. The first row's id is recorded in
     * IndexedDB until sent_at + 65 minutes; after that the message is refused
     * as stale anyway, so pruning the entry opens no window.
     */
    isReplay(context, messageId) {
        const check = async () => {
            const pair = [context.senderId, context.recipientId].map(String).sort().join('|');
            const key = `${context.conversation || 'dm'}|${pair}|${context.msgUuid}`;
            const id = String(messageId);

            let first = this.seenInPage.get(key);
            if (first === undefined && !this.replayStoreBroken) {
                try {
                    await this.pruneReplayStore();
                    const stored = await replayStoreRequest('readonly', (store) => store.get(key));
                    if (stored) first = stored.messageId;
                } catch (error) {
                    this.replayStoreBroken = true;
                }
            }
            if (first !== undefined) {
                this.seenInPage.set(key, first);
                return first !== id;
            }

            this.seenInPage.set(key, id);
            if (!this.replayStoreBroken) {
                try {
                    await replayStoreRequest('readwrite', (store) => store.put(
                        { key, messageId: id, expiresAt: context.sentAt + RX_MAX_AGE_MS }, key));
                } catch (error) {
                    this.replayStoreBroken = true;
                }
            }
            return false;
        };
        const result = this.replayQueue.then(check);
        this.replayQueue = result.catch(() => {});
        return result;
    }

    /** Drop replay records past their expiry. Runs once per page load. */
    pruneReplayStore() {
        if (!this.replayPruned) {
            this.replayPruned = (async () => {
                if (!this.available) return;
                const now = Date.now();
                const entries = await replayStoreRequest('readonly', (store) => store.getAll());
                for (const entry of entries || []) {
                    if (entry && entry.key && !(entry.expiresAt > now)) {
                        await replayStoreRequest('readwrite', (store) => store.delete(entry.key));
                    }
                }
            })();
        }
        return this.replayPruned;
    }

    /** Returns {ciphertext, iv, v, msg_uuid}, or null when the peer has no key yet. */
    async encrypt(peerPublicKeyB64, plaintext, context = {}) {
        return this.encryptBytes(peerPublicKeyB64, new TextEncoder().encode(plaintext), context);
    }

    /** Returns the plaintext, or null when the message cannot be opened. */
    async decrypt(peerPublicKeyB64, ciphertextB64, ivB64, context = {}) {
        const plaintext = await this.decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64, context);
        return plaintext === null ? null : new TextDecoder().decode(plaintext);
    }

    /** Drop cached keys, e.g. after the peer publishes a new public key. */
    forgetPeer(peerPublicKeyB64) {
        if (!peerPublicKeyB64) {
            derivedKeyCache.clear();
            return;
        }
        for (const cacheKey of Array.from(derivedKeyCache.keys())) {
            if (cacheKey.split('|').includes(peerPublicKeyB64)) derivedKeyCache.delete(cacheKey);
        }
    }
}

window.revealxE2EE = new E2EESession();
