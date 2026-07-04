/**
 * Reveal-X end-to-end encryption for private text messages.
 *
 * ECDH P-256 for key agreement, HKDF-SHA256 to turn the shared secret into a
 * message key, AES-GCM for the message itself. The private key is generated
 * non-extractable and kept in IndexedDB, so it cannot be read back out by
 * script - an XSS bug can abuse the session but cannot exfiltrate the key.
 *
 * The server only ever sees the public key and the ciphertext.
 */

const RX_DB_NAME = 'revealx-e2ee';
const RX_STORE = 'keypairs';
const RX_INFO = 'reveal-x-message-key-v1';

const derivedKeyCache = new Map(); // peerPublicKeyB64 -> CryptoKey

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
     * Derive the AES-GCM key shared with one peer.
     *
     * The HKDF salt is bound to both public keys (sorted, so each side computes
     * the same value) which keeps the message key distinct per conversation.
     */
    async deriveKeyFor(peerPublicKeyB64) {
        if (!this.isReady() || !peerPublicKeyB64) return null;

        const cached = derivedKeyCache.get(peerPublicKeyB64);
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

        const hkdfKey = await window.crypto.subtle.importKey(
            'raw', sharedBits, 'HKDF', false, ['deriveKey']
        );
        const messageKey = await window.crypto.subtle.deriveKey(
            { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(RX_INFO) },
            hkdfKey,
            { name: 'AES-GCM', length: 256 },
            false,
            ['encrypt', 'decrypt']
        );

        derivedKeyCache.set(peerPublicKeyB64, messageKey);
        return messageKey;
    }

    /**
     * Encrypt arbitrary bytes (used for voice recordings).
     * Returns {ciphertext, iv} in base64, or null when the peer has no key yet.
     */
    async encryptBytes(peerPublicKeyB64, bytes) {
        const key = await this.deriveKeyFor(peerPublicKeyB64);
        if (!key) return null;

        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        const ciphertext = await window.crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, bytes);
        return { ciphertext: bufferToBase64(ciphertext), iv: bufferToBase64(iv) };
    }

    /** Decrypt to an ArrayBuffer, or null when the payload cannot be opened. */
    async decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64) {
        const key = await this.deriveKeyFor(peerPublicKeyB64);
        if (!key || !ciphertextB64 || !ivB64) return null;

        try {
            return await window.crypto.subtle.decrypt(
                { name: 'AES-GCM', iv: new Uint8Array(base64ToBuffer(ivB64)) },
                key,
                base64ToBuffer(ciphertextB64)
            );
        } catch (error) {
            // Wrong key, rotated peer key, or a tampered payload.
            return null;
        }
    }

    /** Returns {ciphertext, iv} in base64, or null when the peer has no key yet. */
    async encrypt(peerPublicKeyB64, plaintext) {
        return this.encryptBytes(peerPublicKeyB64, new TextEncoder().encode(plaintext));
    }

    /** Returns the plaintext, or null when the message cannot be opened. */
    async decrypt(peerPublicKeyB64, ciphertextB64, ivB64) {
        const plaintext = await this.decryptBytes(peerPublicKeyB64, ciphertextB64, ivB64);
        return plaintext === null ? null : new TextDecoder().decode(plaintext);
    }

    /** Drop cached keys, e.g. after the peer publishes a new public key. */
    forgetPeer(peerPublicKeyB64) {
        if (peerPublicKeyB64) derivedKeyCache.delete(peerPublicKeyB64);
        else derivedKeyCache.clear();
    }
}

window.revealxE2EE = new E2EESession();
