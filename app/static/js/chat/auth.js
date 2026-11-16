// Sign-in, sign-out, the second factor and session resume.
// Split out of chat.js; main.js loads the modules in order.

import { late } from './late.js';
import {
    STATUS_LABELS,
    accountName,
    authError,
    authModal,
    deleteAccountBtn,
    loginBtn,
    passwordInput,
    registerBtn,
    share2Memory,
    socket,
    state,
    usernameInput,
} from './core.js';
import {
    showOwnInitial,
    typingOpensKeyboard,
    updateConnectionBanner,
} from './basics.js';
import {
    resetInactivityTimers,
} from './session.js';
import {
    initE2EEKeys,
} from './keys.js';

// ==========================================
// 8. Auth, Login and Account CRUD
// ==========================================

// ==========================================
// 8a. Sign in, or create an account
// ==========================================
//
// One form, two modes. Create account checks the name as it is typed (the
// server's check_username) and asks for the password twice; the rules and
// wording are in signup-ui.js.

const signup = window.revealxSignupUI;
const modeSignIn = document.getElementById('authModeSignIn');
const modeSignUp = document.getElementById('authModeSignUp');
const confirmPasswordInput = document.getElementById('confirmPasswordInput');
const usernameStatus = document.getElementById('usernameStatus');
let authMode = 'login';
let lastUsernameStatus = null;
let checkTimer = 0;

function showUsernameHint(status) {
    const hint = signup.usernameHint(status);
    usernameStatus.textContent = hint.text;
    usernameStatus.className = `username-status ${hint.state}`;
}

function setAuthMode(mode) {
    authMode = mode;
    const creating = mode === 'register';
    modeSignIn.setAttribute('aria-pressed', String(!creating));
    modeSignUp.setAttribute('aria-pressed', String(creating));
    confirmPasswordInput.hidden = !creating;
    loginBtn.hidden = creating;
    registerBtn.hidden = !creating;
    passwordInput.setAttribute('autocomplete', creating ? 'new-password' : 'current-password');
    authError.textContent = '';
    lastUsernameStatus = null;
    showUsernameHint(null);
    if (creating) requestUsernameCheck();
}

function requestUsernameCheck() {
    clearTimeout(checkTimer);
    const name = usernameInput.value.trim();
    if (authMode !== 'register' || !signup.worthChecking(name)) {
        showUsernameHint(null);
        return;
    }
    // Asked once typing pauses, not on every key: the server limits it.
    checkTimer = setTimeout(() => socket.emit('check_username', { username: name }), 350);
}

modeSignIn.addEventListener('click', () => setAuthMode('login'));
modeSignUp.addEventListener('click', () => setAuthMode('register'));
usernameInput.addEventListener('input', requestUsernameCheck);

socket.on('username_status', (status) => {
    // Answers for a name that has since been changed are stale.
    if (authMode !== 'register' || !status || status.username !== usernameInput.value.trim()) return;
    lastUsernameStatus = status;
    showUsernameHint(status);
});

function submitAuth(mode) {
    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    authError.textContent = '';
    
    if (!username || !password) {
        authError.textContent = 'Enter username and password';
        return;
    }

    if (mode === 'register') {
        const problem = signup.signupProblem({
            username, password, confirm: confirmPasswordInput.value, status: lastUsernameStatus,
        });
        if (problem) {
            authError.textContent = problem;
            return;
        }
    }

    if (!validateUsername(username)) {
        authError.textContent = 'Username must be 3-30 letters, numbers, dots, dashes, or underscores';
        return;
    }

    // No client-side attempt gate here. It never stopped an attacker -- they
    // script the socket and never run this code -- while it did lock out real
    // users: it counted "waiting for administrator approval" as a failure, and
    // once it hit zero it refused to send anything, so the only way out was a
    // page reload. The server enforces the real limit.
    socket.emit(mode, { username, password });
}

loginBtn.addEventListener('click', () => submitAuth('login'));
registerBtn.addEventListener('click', () => submitAuth('register'));

// ==========================================
// 8b. Firebase sign-in (Google / email)
// ==========================================

const firebaseAuthBlock = document.getElementById('firebaseAuth');

(async function initFirebaseSignIn() {
    const ready = await window.revealxFirebase.init();
    if (ready && firebaseAuthBlock) firebaseAuthBlock.classList.remove('hidden');
})();

/** Run a Firebase sign-in, then hand the ID token to our server to verify. */
async function firebaseSignIn(action, buttonEl) {
    if (!window.revealxFirebase.available) return;

    authError.textContent = '';
    const original = buttonEl ? buttonEl.textContent : '';
    if (buttonEl) {
        buttonEl.disabled = true;
        buttonEl.textContent = 'Signing in...';
    }

    try {
        const idToken = await action();
        // The server decides who this is; we only carry the token.
        socket.emit('firebase_login', { id_token: idToken });
    } catch (error) {
        authError.textContent = window.revealxFirebase.constructor.describeError(error);
        await window.revealxFirebase.signOut();
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.textContent = original;
        }
    }
}

const googleSignInBtn = document.getElementById('googleSignInBtn');
const firebaseSignInBtn = document.getElementById('firebaseSignInBtn');
const firebaseSignUpBtn = document.getElementById('firebaseSignUpBtn');
const firebaseEmailInput = document.getElementById('firebaseEmailInput');
const firebasePasswordInput = document.getElementById('firebasePasswordInput');

function firebaseEmailCredentials() {
    const email = (firebaseEmailInput.value || '').trim();
    const password = firebasePasswordInput.value || '';
    if (!email || !password) {
        authError.textContent = 'Enter an email address and password';
        return null;
    }
    return { email, password };
}

if (googleSignInBtn) {
    googleSignInBtn.addEventListener('click', () =>
        firebaseSignIn(() => window.revealxFirebase.signInWithGoogle(), googleSignInBtn));
}

if (firebaseSignInBtn) {
    firebaseSignInBtn.addEventListener('click', () => {
        const creds = firebaseEmailCredentials();
        if (creds) {
            firebaseSignIn(() => window.revealxFirebase.signInWithEmail(creds.email, creds.password), firebaseSignInBtn);
        }
    });
}

if (firebaseSignUpBtn) {
    firebaseSignUpBtn.addEventListener('click', () => {
        const creds = firebaseEmailCredentials();
        if (creds) {
            firebaseSignIn(() => window.revealxFirebase.createWithEmail(creds.email, creds.password), firebaseSignUpBtn);
        }
    });
}

if (firebasePasswordInput) {
    firebasePasswordInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter' && firebaseSignInBtn) firebaseSignInBtn.click();
    });
}

// ==========================================
// 8c. Two-factor authentication (TOTP)
// ==========================================

const totpModal = document.getElementById('totpModal');
const totpCodeInput = document.getElementById('totpCodeInput');
const totpErrorEl = document.getElementById('totpError');

// The first factor passed but no session exists yet: the server is holding the
// sign-in until a valid code arrives.
socket.on('totp_required', () => {
    authError.textContent = '';
    totpErrorEl.textContent = '';
    totpCodeInput.value = '';
    totpModal.classList.remove('hidden');
    totpCodeInput.focus();
});

socket.on('totp_error', (data) => {
    totpErrorEl.textContent = data.attempts_remaining
        ? `${data.error} (${data.attempts_remaining} attempts left)`
        : data.error;
    totpCodeInput.value = '';
    totpCodeInput.focus();
});

function submitTotpCode() {
    const code = (totpCodeInput.value || '').trim();
    if (code.length !== 6) {
        totpErrorEl.textContent = 'Enter the 6-digit code';
        return;
    }
    socket.emit('verify_totp', { code });
}

document.getElementById('totpVerifyBtn').addEventListener('click', submitTotpCode);
totpCodeInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') submitTotpCode();
});
document.getElementById('totpCancelBtn').addEventListener('click', async () => {
    totpModal.classList.add('hidden');
    await window.revealxFirebase.signOut();
    window.location.reload();
});

[usernameInput, passwordInput].forEach(input => {
    input.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') submitAuth('login');
    });
});

socket.on('auth_success', (data) => {
    state.currentUser = data.user;
    if (data.resume_token) state.resumeToken = data.resume_token;
    state.resuming = false;
    state.signedOut = false;
    updateConnectionBanner();
    const totpChallenge = document.getElementById('totpModal');
    if (totpChallenge) totpChallenge.classList.add('hidden');
    localStorage.setItem('revealx_username', state.currentUser.username);
    accountName.textContent = state.currentUser.username;
    showOwnInitial(state.currentUser.username);
    const ownStatus = document.getElementById('userStatus');
    if (ownStatus) ownStatus.textContent = STATUS_LABELS[state.currentUser.status || 'online'];
    
    // Load profile preview photo
    const preview = document.getElementById('profilePreview');
    if (preview && data.user.profile_image) {
        preview.src = data.user.profile_image;
        preview.classList.remove('empty');
    }
    
    authModal.style.display = 'none';
    if (deleteAccountBtn) deleteAccountBtn.style.display = 'block';
    // Focus was in the sign-in form, which has just been hidden, so it fell
    // to <body>. Put it on the account search, the first thing to do next.
    // Only if focus really was lost: after a resume the user may be typing.
    // Not on a touch screen, where it would open the on-screen keyboard.
    const focused = document.activeElement;
    if (!typingOpensKeyboard()
        && (!focused || focused === document.body || authModal.contains(focused))) {
        const search = document.getElementById('userSearchInput');
        if (search) search.focus();
    }
    
    late.updateUsersList(data.users);
    // admin.js registers this; guard so chat still works if it is absent.
    if (window.revealxApplyAdminVisibility) window.revealxApplyAdminVisibility(data.user);
    initE2EEKeys();
    resetInactivityTimers();
    // A resumed session is the same sign-in on a new socket: stay in the open
    // conversation and refetch it, rather than dropping back to the welcome
    // screen as a fresh sign-in does.
    if (data.resumed && state.selectedRecipientId) {
        late.requestChatHistory(state.selectedRecipientId);
    } else {
        late.showWelcome();
    }
});

/** Put the sign-in dialog back, e.g. when a session could not be resumed. */
function showSignIn(reason) {
    state.currentUser = null;
    state.resumeToken = null;
    state.resuming = false;
    share2Memory.clear();
    late.setChatEnabled(false);
    authModal.style.display = '';
    authError.textContent = reason || 'Please sign in again.';
    state.signedOut = true;
    updateConnectionBanner();
}

// The server sends this from every handler that needs a session, where it
// used to return silently -- which, after a dropped connection, left a page
// that looked signed in and did nothing at all.
socket.on('auth_required', (data) => {
    const failedResume = Boolean(data && data.resume_failed);
    if (state.resuming && !failedResume) return; // a resume is already on its way
    if (state.resumeToken && !failedResume) {
        state.resuming = true;
        socket.emit('resume_session', { token: state.resumeToken });
        late.showTransientError('Reconnecting your session. Try that again in a moment.');
        return;
    }
    showSignIn(data && data.error);
});

socket.on('auth_error', (data) => {
    const totpChallenge = document.getElementById('totpModal');
    if (totpChallenge && !totpChallenge.classList.contains('hidden')) {
        totpChallenge.classList.add('hidden');
        window.revealxFirebase.signOut();
    }
    // Report exactly what the server said. It already explains a lockout and
    // how long it lasts; a second, invented counter only contradicted it.
    authError.textContent = data.error || 'Sign-in failed';
});

// admin.js reads the signed-in user from here.
window.state = state;

socket.on('registration_submitted', (data) => {
    // Not an error, so it must not go through the red auth-error line.
    authError.textContent = '';
    const notice = document.getElementById('authNotice');
    if (notice) {
        notice.textContent = data.message || 'Request submitted.';
        notice.classList.remove('hidden');
    }
    const username = document.getElementById('usernameInput');
    const password = document.getElementById('passwordInput');
    if (username) username.value = '';
    if (password) password.value = '';
    if (confirmPasswordInput) confirmPasswordInput.value = '';
    showUsernameHint(null);
});

socket.on('force_signed_out', (data) => {
    share2Memory.clear();
    // An admin disabled, deleted, or reset the password on this account. The
    // server has already dropped the session; this only explains why.
    localStorage.removeItem('revealx_username');
    // Read before the reload, so it is acknowledged rather than flashed.
    window.revealxDialogs.showNotice({
        title: 'You have been signed out',
        body: data && data.reason ? data.reason : 'Your session was ended by an administrator.',
    }).then(() => window.location.reload());
});

/**
 * Transient failure banner.
 *
 * The server was already reporting these -- "Recipient must be online",
 * "Message is too long", "Unsupported image type" -- and nothing listened, so
 * a failed send looked to the user exactly like a successful one. Announced to
 * screen readers as well, since a visual-only banner is no use to anyone who
 * cannot see it.
 */
