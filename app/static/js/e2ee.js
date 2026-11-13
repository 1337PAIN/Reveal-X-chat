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
 * Protocol v2 (current). v1 derived one key per *pair* of users and used it
 * in both directions, with no associated data. Two consequences followed:
 *
 *   - Reflection. A ciphertext Alice sent Bob decrypted just as well when the
 *     server presented it to Alice as a message *from* Bob, so the server
 *     could put Alice's own words in Bob's mouth.
 *   - Replay and relabelling. Nothing bound a ciphertext to who sent it, to
 *     whom, or to which message it was, so the server could deliver it twice
 *     or attribute it to a different conversation row.
 *
 * v2 derives a separate key per direction (HKDF info carries the sender's and
 * then the recipient's public key) and authenticates
 * "v2|<sender_id>|<recipient_id>|<msg_uuid>" as AES-GCM additional data. The
 * ids come from the server's row, so relabelling a row breaks the tag, and
 * msg_uuid lets a client notice the same ciphertext delivered twice.
 *
 * v1 decryption is kept for rows written before the upgrade. Messages expire
 * after an hour, so that path has nothing to open soon after deployment. New
 * messages are always v2, and v2 rows are never tried under v1.
 */

const RX_DB_NAME = 'revealx-e2ee';
const RX_STORE = 'keypairs';
const RX_INFO = 'reveal-x-message-key-v1';
const RX_INFO_V2 = 'reveal-x-message-key-v2';
const RX_PROTOCOL = 2;

// "<version>|<sender public key>|<recipient public key>" -> CryptoKey. v1 keys
// are direction-free and cached under "1|<peer public key>".
const derivedKeyCache = new Map();

function associatedData(senderId, recipientId, msgUuid) {
    return new TextEncoder().encode(`v2|${senderId}|${recipientId}|${msgUuid}`);
}

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
     * Derive an AES-GCM key shared with one peer.
     *
     * The HKDF salt is bound to both public keys (sorted, so each side computes
     * the same value). For v2 the HKDF info additionally names the direction:
     * sender's public key, then recipient's, so A->B and B->A are different
     * keys and one direction's ciphertext cannot be replayed as the other's.
     */
    async deriveKeyFor(peerPublicKeyB64, direction = null) {
        if (!this.isReady() || !peerPublicKeyB64) return null;

        const cacheKey = direction
            ? `2|${direction.senderPublicKey}|${direction.recipientPublicKey}`
            : `1|${peerPublicKeyB64}`;
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
        const info = direction
            ? `${RX_INFO_V2}|${direction.senderPublicKey}|${direction.recipientPublicKey}`
            : RX_INFO;

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
     * `context.recipientId` is the peer's user id; `context.msgUuid` is
     * generated when absent. Returns {ciphertext, iv, v, msg_uuid} with the
     * binary fields in base64, or null when the peer has no key yet.
     */
    async encryptBytes(peerPublicKeyB64, bytes, context = {}) {
        const key = await this.deriveKeyFor(
            peerPublicKeyB64, this.direction(peerPublicKeyB64, this.userId));
        if (!key) return null;

        const msgUuid = context.msgUuid || newMessageUuid();
        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        // Encrypt the message before sending it to the other user.
        const ciphertext = await window.crypto.subtle.encrypt(
            {
                name: 'AES-GCM',
                iv,
                additionalData: associatedData(this.userId, context.recipientId || '', msgUuid),
            },
            key,
            bytes
        );
        return {
            ciphertext: bufferToBase64(ciphertext),
            iv: bufferToBase64(iv),
            v: RX_PROTOCOL,
            msg_uuid: msgUuid,
        };
    }

    /**
     * Decrypt to an ArrayBuffer, or null when the payload cannot be opened.
     *
     * `context` is rebuilt from the stored row: {v, senderId, recipientId,
     * msgUuid}. Anything but v === 2 is a legacy v1 row.
     */
    async decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64, context = {}) {
        if (!ciphertextB64 || !ivB64) return null;
        const v2 = context.v === RX_PROTOCOL;
        const key = await this.deriveKeyFor(
            peerPublicKeyB64, v2 ? this.direction(peerPublicKeyB64, context.senderId) : null);
        if (!key) return null;

        const params = { name: 'AES-GCM', iv: new Uint8Array(base64ToBuffer(ivB64)) };
        if (v2) {
            params.additionalData = associatedData(
                context.senderId || '', context.recipientId || '', context.msgUuid || '');
        }
        try {
            return await window.crypto.subtle.decrypt(params, key, base64ToBuffer(ciphertextB64));
        } catch (error) {
            // Wrong key, rotated peer key, wrong direction or context, or a
            // tampered payload. AES-GCM does not say which, by design.
            return null;
        }
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
