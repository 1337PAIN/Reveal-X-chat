/**
 * Firebase sign-in for Reveal-X (Google and email/password).
 *
 * The browser authenticates with Firebase, then hands the resulting ID token to
 * our server over the socket. The server verifies that token with the Firebase
 * Admin SDK before trusting any identity, so nothing here needs to be — or is —
 * treated as authoritative.
 *
 * Firebase is optional. When the server reports no config, the whole block stays
 * hidden and the built-in username/password login is used instead.
 *
 * The SDK is fetched only when there is a config to use, so a deployment
 * without Firebase pays nothing for it.
 */

const FIREBASE_SDK_VERSION = '10.14.1';
const FIREBASE_SDK_BASE = `https://www.gstatic.com/firebasejs/${FIREBASE_SDK_VERSION}`;

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const existing = document.querySelector(`script[src="${src}"]`);
        if (existing) {
            if (existing.dataset.loaded === 'true') return resolve();
            existing.addEventListener('load', () => resolve());
            existing.addEventListener('error', () => reject(new Error(`Could not load ${src}`)));
            return;
        }
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.addEventListener('load', () => { script.dataset.loaded = 'true'; resolve(); });
        script.addEventListener('error', () => reject(new Error(`Could not load ${src}`)));
        document.head.appendChild(script);
    });
}

class FirebaseSignIn {
    constructor() {
        this.available = false;
        this.auth = null;
        this.config = null;
    }

    /** Fetch server config and, if present, boot the SDK. Safe to call once. */
    async init() {
        let payload;
        try {
            const response = await fetch('/api/auth/config');
            payload = await response.json();
        } catch (error) {
            console.warn('Could not read auth config', error);
            return false;
        }

        if (!payload || !payload.firebase) return false;
        this.config = payload.firebase;

        try {
            // The compat builds expose window.firebase as a classic script, which
            // keeps us clear of inline module scripts that the CSP forbids.
            await loadScript(`${FIREBASE_SDK_BASE}/firebase-app-compat.js`);
            await loadScript(`${FIREBASE_SDK_BASE}/firebase-auth-compat.js`);
        } catch (error) {
            console.warn('Firebase SDK did not load', error);
            return false;
        }

        if (!window.firebase || !window.firebase.auth) return false;

        try {
            if (!window.firebase.apps.length) window.firebase.initializeApp(this.config);
            this.auth = window.firebase.auth();
            this.available = true;
            return true;
        } catch (error) {
            console.warn('Firebase init failed', error);
            return false;
        }
    }

    /**
     * Firebase keeps its own session, which would silently re-authenticate a
     * user who has signed out of Reveal-X. Clear it whenever we leave.
     */
    async signOut() {
        if (this.auth) {
            try { await this.auth.signOut(); } catch (error) { /* already gone */ }
        }
    }

    async signInWithGoogle() {
        const provider = new window.firebase.auth.GoogleAuthProvider();
        provider.setCustomParameters({ prompt: 'select_account' });
        const result = await this.auth.signInWithPopup(provider);
        return result.user.getIdToken();
    }

    async signInWithEmail(email, password) {
        const result = await this.auth.signInWithEmailAndPassword(email, password);
        return result.user.getIdToken();
    }

    async createWithEmail(email, password) {
        const result = await this.auth.createUserWithEmailAndPassword(email, password);
        return result.user.getIdToken();
    }

    /** Turn Firebase's error codes into something a person can act on. */
    static describeError(error) {
        const code = (error && error.code) || '';
        const messages = {
            'auth/popup-closed-by-user': 'Sign-in window was closed.',
            'auth/popup-blocked': 'Your browser blocked the sign-in popup.',
            'auth/cancelled-popup-request': 'Sign-in was cancelled.',
            'auth/invalid-email': 'That email address is not valid.',
            'auth/user-disabled': 'That account has been disabled.',
            'auth/user-not-found': 'No account matches those details.',
            'auth/wrong-password': 'No account matches those details.',
            'auth/invalid-credential': 'No account matches those details.',
            'auth/email-already-in-use': 'That email already has an account. Sign in instead.',
            'auth/weak-password': 'Choose a password of at least 6 characters.',
            'auth/network-request-failed': 'Could not reach Firebase. Check your connection.',
            'auth/operation-not-allowed': 'That sign-in method is not enabled in the Firebase console.',
            'auth/unauthorized-domain': 'This domain is not in the Firebase authorised domains list.',
        };
        return messages[code] || (error && error.message) || 'Sign-in failed.';
    }
}

window.revealxFirebase = new FirebaseSignIn();
