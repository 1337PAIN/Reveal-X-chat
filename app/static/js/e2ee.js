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

function openKeyStore() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(RX_DB_NAME, 1);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(RX_STORE)) {
                request.result.createObjectStore(RX_STORE);
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Could not open key store'));
    });
}

function keyStoreRequest(mode, action) {
    return openKeyStore().then((db) => new Promise((resolve, reject) => {
        const tx = db.transaction(RX_STORE, mode);
        const request = action(tx.objectStore(RX_STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Key store operation failed'));
    }));
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

    /** Short human-comparable fingerprint, for out-of-band verification. */
    async fingerprint(publicKeyB64) {
        const value = publicKeyB64 || this.publicKey;
        if (!value || !this.available) return '';
        const digest = await window.crypto.subtle.digest('SHA-256', base64ToBuffer(value));
        return Array.from(new Uint8Array(digest))
            .slice(0, 8)
            .map((byte) => byte.toString(16).padStart(2, '0'))
            .join(' ')
            .toUpperCase();
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
