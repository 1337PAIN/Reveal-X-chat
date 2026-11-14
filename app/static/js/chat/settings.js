// Settings, theme, exports, passcode lock and profile.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import {
    STATUS_LABELS,
    accountName,
    closeSettingsBtn,
    deleteAccountBtn,
    profileImageInput,
    profilePicker,
    saveSettingsBtn,
    settingsBio,
    settingsBtn,
    settingsModal,
    settingsStatus,
    settingsUsername,
    share2Memory,
    socket,
    state,
    themeToggle,
} from './core.js';
import {
    announceToScreenReader,
    showOwnInitial,
} from './basics.js';
import {
    logoutUser,
} from './session.js';
import {
    requestChatHistory,
} from './messaging.js';

registerLate({ closeSettings, getPref });

// ==========================================
// 14. Settings, Theme, and Data Exports
// ==========================================

settingsBtn.addEventListener('click', openSettings);
closeSettingsBtn.addEventListener('click', closeSettings);
saveSettingsBtn.addEventListener('click', saveSettings);

// Every preference below is read at the point of use, so toggling one changes
// real behaviour rather than only being written to storage.
const PREFERENCES = {
    notifications: { id: 'settingsNotifications', default: true },
    sounds: { id: 'settingsSounds', default: true },
    desktopNotifications: { id: 'settingsDesktopNotifications', default: true },
    mentionsOnly: { id: 'settingsMentionsOnly', default: false },
    readReceipts: { id: 'settingsReadReceipts', default: true },
    typingIndicators: { id: 'settingsTypingIndicators', default: true },
};

export function getPref(name) {
    const spec = PREFERENCES[name];
    if (!spec) return false;
    const stored = localStorage.getItem(`revealx_pref_${name}`);
    return stored === null ? spec.default : stored === 'true';
}

function setPref(name, value) {
    localStorage.setItem(`revealx_pref_${name}`, String(Boolean(value)));
}

function settingsFeedback(message, isError = false) {
    const node = document.getElementById('settingsFeedback');
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('error', isError);
    if (message) setTimeout(() => { if (node.textContent === message) node.textContent = ''; }, 4000);
}

export function openSettings() {
    if (!state.currentUser) return;

    settingsUsername.value = state.currentUser.username;
    settingsBio.value = state.currentUser.bio || '';
    settingsStatus.value = state.currentUser.status || 'online';

    Object.entries(PREFERENCES).forEach(([name, spec]) => {
        const input = document.getElementById(spec.id);
        if (input) input.checked = getPref(name);
    });

    const passcodeToggle = document.getElementById('settingsPasscodeLock');
    if (passcodeToggle) passcodeToggle.checked = hasPasscode();

    const totpToggle = document.getElementById('settingsTotp');
    if (totpToggle) totpToggle.checked = Boolean(state.currentUser.totp_enabled);

    const preview = document.getElementById('settingsProfilePreview');
    if (preview && state.currentUser.profile_image) {
        preview.src = state.currentUser.profile_image;
        preview.classList.remove('empty');
    } else if (preview) {
        preview.classList.add('empty');
    }

    settingsFeedback('');
    settingsModal.classList.remove('hidden');
    announceToScreenReader('Settings panel opened.');
}

function closeSettings() {
    settingsModal.classList.add('hidden');
}

function saveSettings() {
    const username = settingsUsername.value.trim();
    const bio = settingsBio.value.trim();
    const status = settingsStatus.value;

    if (!validateUsername(username)) {
        settingsFeedback('Username must be 3-30 letters, numbers, dots, dashes, or underscores', true);
        return;
    }

    Object.keys(PREFERENCES).forEach(name => {
        const input = document.getElementById(PREFERENCES[name].id);
        if (input) setPref(name, input.checked);
    });

    // Only ask for the OS permission when the user actually wants desktop popups.
    if (getPref('notifications') && getPref('desktopNotifications')
        && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    // Server-side profile fields. The username is validated again on the server,
    // which is what settles clashes; the UI only updates on its confirmation.
    if (username !== state.currentUser.username) {
        socket.emit('update_username', { new_username: username });
    }
    if (bio !== (state.currentUser.bio || '')) {
        socket.emit('update_bio', { bio });
    }
    if (status !== (state.currentUser.status || 'online')) {
        socket.emit('update_status', { status });
    }

    settingsFeedback('Settings saved.');
}

// The server is the authority on these; the UI reflects what it confirms.
socket.on('settings_saved', (data) => {
    if (!state.currentUser) return;

    if (data.username) {
        state.currentUser.username = data.username;
        accountName.textContent = data.username;
        showOwnInitial(data.username);
        settingsUsername.value = data.username;
        localStorage.setItem('revealx_username', data.username);
    }
    if (data.bio !== undefined) state.currentUser.bio = data.bio;
    if (data.status) {
        state.currentUser.status = data.status;
        const own = document.getElementById('userStatus');
        if (own) own.textContent = STATUS_LABELS[data.status] || data.status;
    }
    settingsFeedback('Settings saved.');
});

socket.on('settings_error', (data) => {
    if (data && data.field === 'delete_account') {
        if (deleteAccountError) deleteAccountError.textContent = data.error;
        return;
    }
    const message = data.error || 'Could not save settings';

    // A two-factor error raised while the setup dialog is open belongs in that
    // dialog; the settings footer behind it would never be seen.
    const setupModal = document.getElementById('totpSetupModal');
    if (data.field === 'totp' && setupModal && !setupModal.classList.contains('hidden')) {
        const setupError = document.getElementById('totpSetupError');
        if (setupError) setupError.textContent = message;
        return;
    }

    if (data.field === 'totp') {
        // Enabling failed, so the checkbox must not look switched on.
        const totpToggle = document.getElementById('settingsTotp');
        if (totpToggle) totpToggle.checked = Boolean(state.currentUser && state.currentUser.totp_enabled);
    }

    settingsFeedback(message, true);
    if (data.field === 'username') settingsUsername.value = state.currentUser.username;
});

// Profile photo can be changed from the sidebar or from Settings.
const settingsProfileInput = document.getElementById('settingsProfileInput');
if (settingsProfileInput) {
    settingsProfileInput.addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (!file) return;
        if (file.size > 512 * 1024) {
            settingsFeedback('Profile image must be smaller than 512 KB', true);
            return;
        }
        const reader = new FileReader();
        reader.onload = (readerEvent) => {
            const dataUrl = readerEvent.target.result;
            const preview = document.getElementById('settingsProfilePreview');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('empty');
            }
            socket.emit('update_profile', { profile_image: dataUrl });
            settingsFeedback('Profile photo updated.');
        };
        reader.readAsDataURL(file);
        settingsProfileInput.value = '';
    });
}

// ==========================================
// 14a. Two-factor enrolment (from Settings)
// ==========================================

const totpSetupModal = document.getElementById('totpSetupModal');
const totpSetupCodeInput = document.getElementById('totpSetupCodeInput');
const totpSetupError = document.getElementById('totpSetupError');
const settingsTotp = document.getElementById('settingsTotp');

function closeTotpSetup() {
    totpSetupModal.classList.add('hidden');
    totpSetupCodeInput.value = '';
    totpSetupError.textContent = '';
    // The checkbox only sticks once the server confirms enrolment.
    if (settingsTotp) settingsTotp.checked = Boolean(state.currentUser && state.currentUser.totp_enabled);
}

if (settingsTotp) {
    settingsTotp.addEventListener('change', () => {
        if (settingsTotp.checked) {
            socket.emit('totp_begin_enrol', {});
            return;
        }
        // Turning it off needs proof of a current code, so the server is asked
        // with one rather than trusting the click.
        askSecret({
            title: 'Turn off two-factor authentication',
            body: 'Enter a current code from your authenticator app. Without one the '
                + 'second factor stays on, so a borrowed session cannot quietly '
                + 'remove it.',
            label: 'Authenticator code',
            placeholder: '000000',
            numeric: true,
            maxLength: 6,
            autocomplete: 'one-time-code',
            okLabel: 'Turn it off',
            danger: true,
            validate: (value) => /^\d{6}$/.test(value.trim())
                ? null : 'Enter the six digits shown in your app.',
        }).then((code) => {
            if (code === null) {
                settingsTotp.checked = true;
                return;
            }
            socket.emit('totp_disable', { code: code.trim() });
        });
    });
}

socket.on('totp_enrolment', (data) => {
    document.getElementById('totpQr').src = data.qr_data_url;
    document.getElementById('totpSecret').textContent = data.secret;
    totpSetupError.textContent = '';
    totpSetupCodeInput.value = '';
    totpSetupModal.classList.remove('hidden');
    totpSetupCodeInput.focus();
});

socket.on('totp_state', (data) => {
    if (state.currentUser) state.currentUser.totp_enabled = Boolean(data.enabled);
    if (settingsTotp) settingsTotp.checked = Boolean(data.enabled);
    if (data.enabled) {
        totpSetupModal.classList.add('hidden');
        settingsFeedback('Two-factor authentication enabled.');
    } else {
        settingsFeedback('Two-factor authentication disabled.');
    }
});

document.getElementById('totpSetupConfirmBtn').addEventListener('click', () => {
    const code = (totpSetupCodeInput.value || '').trim();
    if (code.length !== 6) {
        totpSetupError.textContent = 'Enter the 6-digit code';
        return;
    }
    socket.emit('totp_confirm_enrol', { code });
});

totpSetupCodeInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') document.getElementById('totpSetupConfirmBtn').click();
});
document.getElementById('totpSetupCancelBtn').addEventListener('click', closeTotpSetup);
document.getElementById('totpSetupCloseBtn').addEventListener('click', closeTotpSetup);

// ==========================================
// 14b. Local passcode lock
// ==========================================

/*
 * A device-local screen lock. The passcode is never sent anywhere and is stored
 * only as a PBKDF2-SHA256 hash with a random salt, so reading localStorage does
 * not reveal it.
 *
 * Scope, stated plainly: this stops someone picking up an unlocked browser. It
 * is not a second factor and it does not encrypt anything - the E2EE key in
 * IndexedDB is what protects message content.
 */

const PASSCODE_KEY = 'revealx_passcode';
const PASSCODE_ITERATIONS = 210000;
const LOCK_AFTER_HIDDEN_MS = 60 * 1000;

export const lockScreen = document.getElementById('lockScreen');
const lockPasscodeInput = document.getElementById('lockPasscodeInput');
const lockError = document.getElementById('lockError');
let hiddenSince = null;

function hasPasscode() {
    return Boolean(localStorage.getItem(PASSCODE_KEY));
}

function toBase64(buffer) {
    return btoa(String.fromCharCode(...new Uint8Array(buffer)));
}

async function derivePasscodeHash(passcode, saltB64, iterations) {
    const salt = Uint8Array.from(atob(saltB64), ch => ch.charCodeAt(0));
    const material = await crypto.subtle.importKey(
        'raw', new TextEncoder().encode(passcode), 'PBKDF2', false, ['deriveBits']
    );
    const bits = await crypto.subtle.deriveBits(
        { name: 'PBKDF2', salt, iterations, hash: 'SHA-256' }, material, 256
    );
    return toBase64(bits);
}

async function setPasscode(passcode) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const saltB64 = toBase64(salt);
    const hash = await derivePasscodeHash(passcode, saltB64, PASSCODE_ITERATIONS);
    localStorage.setItem(PASSCODE_KEY, JSON.stringify({
        salt: saltB64, hash, iterations: PASSCODE_ITERATIONS,
    }));
}

async function verifyPasscode(passcode) {
    const stored = localStorage.getItem(PASSCODE_KEY);
    if (!stored) return false;
    try {
        const { salt, hash, iterations } = JSON.parse(stored);
        return await derivePasscodeHash(passcode, salt, iterations) === hash;
    } catch (error) {
        return false;
    }
}

function lockApp() {
    if (!hasPasscode() || !lockScreen) return;
    lockError.textContent = '';
    lockPasscodeInput.value = '';
    lockScreen.classList.remove('hidden');
    lockPasscodeInput.focus();
}

function unlockApp() {
    if (lockScreen) lockScreen.classList.add('hidden');
    hiddenSince = null;
}

async function handleUnlock() {
    const passcode = lockPasscodeInput.value;
    if (!passcode) return;

    if (await verifyPasscode(passcode)) {
        unlockApp();
        announceToScreenReader('Unlocked.');
    } else {
        lockError.textContent = 'Incorrect passcode';
        lockPasscodeInput.value = '';
        lockPasscodeInput.focus();
    }
}

if (lockScreen) {
    document.getElementById('lockUnlockBtn').addEventListener('click', handleUnlock);
    lockPasscodeInput.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') handleUnlock();
    });
    document.getElementById('lockSignOutBtn').addEventListener('click', () => {
        unlockApp();
        logoutUser();
    });
}

// Re-lock after the tab has been away long enough to have changed hands.
document.addEventListener('visibilitychange', () => {
    if (!hasPasscode()) return;
    if (document.hidden) {
        hiddenSince = Date.now();
    } else if (hiddenSince && Date.now() - hiddenSince > LOCK_AFTER_HIDDEN_MS) {
        lockApp();
    }
});

const passcodeToggle = document.getElementById('settingsPasscodeLock');
if (passcodeToggle) {
    passcodeToggle.addEventListener('change', async () => {
        if (passcodeToggle.checked) {
            // One dialog, not two: the old flow asked again to confirm, and a
            // mismatch threw away both entries and closed.
            const passcode = await askSecret({
                title: 'Set a device passcode',
                body: 'This locks the app on this device only. It is stored as a '
                    + 'PBKDF2-SHA256 hash in local storage and is never transmitted. '
                    + 'It is not a second factor and it encrypts nothing.',
                label: 'New passcode',
                confirmLabel: 'Confirm passcode',
                okLabel: 'Enable lock',
                maxLength: 12,
                validate: (value) => (value.length < 4 || value.length > 12)
                    ? 'The passcode must be 4 to 12 characters.' : null,
            });
            if (passcode === null) {
                passcodeToggle.checked = false;
                return;
            }
            await setPasscode(passcode);
            settingsFeedback('Passcode lock enabled for this device.');
        } else {
            const passcode = await askSecret({
                title: 'Turn off the passcode lock',
                label: 'Current passcode',
                okLabel: 'Turn it off',
                danger: true,
                maxLength: 12,
            });
            if (passcode === null) {
                passcodeToggle.checked = true;
                return;
            }
            if (!await verifyPasscode(passcode)) {
                passcodeToggle.checked = true;
                settingsFeedback('Incorrect passcode', true);
                return;
            }
            localStorage.removeItem(PASSCODE_KEY);
            settingsFeedback('Passcode lock disabled.');
        }
    });
}

// Lock straight away when the page loads with a passcode already set.
if (hasPasscode()) lockApp();

// Theme Selection
const curTheme = localStorage.getItem('revealx_theme') || 'dark';
if (curTheme === 'light') {
    document.body.setAttribute('data-theme', 'light');
}
updateThemeIcon();

themeToggle.addEventListener('click', () => {
    const isDark = document.body.getAttribute('data-theme') !== 'light';
    document.body.setAttribute('data-theme', isDark ? 'light' : 'dark');
    localStorage.setItem('revealx_theme', isDark ? 'light' : 'dark');
    updateThemeIcon();
});

function updateThemeIcon() {
    const icon = document.querySelector('.theme-icon');
    const isDark = document.body.getAttribute('data-theme') !== 'light';
    if (icon) window.revealxIcons.setIcon(icon, isDark ? 'moon' : 'sun');
    // The name says what pressing it will do, not the current state.
    themeToggle.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
}

// Data Export all messages as JSON
const exportDataBtn = document.getElementById('exportDataBtn');
if (exportDataBtn) {
    exportDataBtn.addEventListener('click', () => {
        if (!state.chatHistoryCache || state.chatHistoryCache.length === 0) {
            window.revealxDialogs.toast('There is no chat history to export.');
            return;
        }
        const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(state.chatHistoryCache, null, 2));
        const dlAnchor = document.createElement('a');
        dlAnchor.setAttribute("href", dataStr);
        dlAnchor.setAttribute("download", "revealx_chat_export.json");
        document.body.appendChild(dlAnchor);
        dlAnchor.click();
        dlAnchor.remove();
        announceToScreenReader('Chat data exported successfully.');
    });
}

// Clear cached data.
//
// This used to call sessionStorage.clear() and report success, and nothing
// else. At the time Share 2 was the only thing written there; it now lives in
// share2Memory, which is what this has to empty. sessionStorage.clear() stays
// for anything an older build left behind in this tab.
//
// What it must NOT clear is the IndexedDB keypair. The private half is
// generated non-extractable and never leaves the browser, so destroying it
// does not "clear a cache": it permanently ends this device's ability to read
// any message already sent to it. That is a different action, and it needs to
// be asked for explicitly rather than hidden behind the word cache.
const clearCacheBtn = document.getElementById('clearCacheBtn');
if (clearCacheBtn) {
    clearCacheBtn.addEventListener('click', async () => {
        const cleared = [];

        sessionStorage.clear();
        if (share2Memory.size) {
            cleared.push('received image shares');
            share2Memory.clear();
        }

        if (state.chatHistoryCache && state.chatHistoryCache.length) {
            cleared.push('the loaded conversation');
            state.chatHistoryCache = [];
        }

        if (window.caches) {
            try {
                const names = await caches.keys();
                await Promise.all(names.map(name => caches.delete(name)));
                if (names.length) {
                    cleared.push(names.length + ' offline asset cache'
                                 + (names.length === 1 ? '' : 's'));
                }
            } catch (err) {
                console.warn('Could not clear the asset caches:', err);
            }
        }

        window.revealxDialogs.toast(cleared.length
            ? 'Cleared ' + cleared.join(' and ') + '. Your encryption key was kept: '
              + 'deleting it would make every message already sent to this device unreadable.'
            : 'Nothing was cached on this device.');
        announceToScreenReader('Cached data cleared.');
    });
}

// Account deletion.
//
// This used to be prompt() for a reason the server never read, then confirm().
// Neither proved who was asking, so an unlocked tab could destroy the account.
// The server now re-authenticates; this collects the proof it wants, in a
// masked field rather than a prompt() box that shows what is typed.
const deleteAccountModal = document.getElementById('deleteAccountModal');
const deleteAccountPassword = document.getElementById('deleteAccountPassword');
const deleteAccountUsername = document.getElementById('deleteAccountUsername');
const deleteAccountTotp = document.getElementById('deleteAccountTotp');
const deleteAccountError = document.getElementById('deleteAccountError');

// --- Masked secret prompt -------------------------------------------------
//
// Replaces window.prompt() everywhere a secret is collected. prompt() renders
// the typed value in cleartext and cannot mask it, cannot validate without
// closing first, and is drawn by the browser rather than the application.
// Four flows used it: the administrator password reset, turning two-factor
// off, and setting or clearing the device passcode.
//
// Resolves with the entered value, or null if the user cancelled. If the
// markup is missing it resolves null rather than falling back to prompt(),
// because silently reverting to a cleartext dialog is the one outcome this
// function exists to prevent.
export function askSecret(options) {
    const modal = document.getElementById('secretPromptModal');
    if (!modal) return Promise.resolve(null);

    const opts = options || {};
    const titleEl = document.getElementById('secretPromptTitle');
    const bodyEl = document.getElementById('secretPromptBody');
    const labelEl = document.getElementById('secretPromptLabel');
    const input = document.getElementById('secretPromptInput');
    const confirmGroup = document.getElementById('secretPromptConfirmGroup');
    const confirmLabelEl = document.getElementById('secretPromptConfirmLabel');
    const confirmInput = document.getElementById('secretPromptConfirm');
    const errorEl = document.getElementById('secretPromptError');
    const okBtn = document.getElementById('secretPromptOkBtn');
    const cancelBtn = document.getElementById('secretPromptCancelBtn');

    titleEl.textContent = opts.title || 'Confirm';
    // textContent, not innerHTML: callers interpolate a username into the body.
    bodyEl.textContent = opts.body || '';
    bodyEl.classList.toggle('hidden', !opts.body);

    labelEl.textContent = opts.label || 'Password';
    // A one-time code is masked nowhere else in this application, so masking
    // it here would be inconsistent rather than safer; passwords and
    // passcodes are masked.
    input.type = opts.numeric ? 'text' : 'password';
    input.inputMode = opts.numeric ? 'numeric' : '';
    input.autocomplete = opts.autocomplete || 'off';
    input.maxLength = opts.maxLength || 128;
    input.placeholder = opts.placeholder || '';
    input.value = '';

    const wantsConfirm = Boolean(opts.confirmLabel);
    confirmGroup.classList.toggle('hidden', !wantsConfirm);
    confirmInput.type = input.type;
    confirmInput.maxLength = input.maxLength;
    confirmInput.value = '';
    if (wantsConfirm) confirmLabelEl.textContent = opts.confirmLabel;

    errorEl.textContent = '';
    okBtn.textContent = opts.okLabel || 'Confirm';
    okBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');

    modal.classList.remove('hidden');
    input.focus();

    return new Promise((resolve) => {
        function cleanup() {
            modal.classList.add('hidden');
            // Never leave a typed secret sitting in the DOM behind a hidden
            // modal -- the same hygiene the delete-account dialog applies.
            input.value = '';
            confirmInput.value = '';
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            modal.removeEventListener('keydown', onKey);
        }

        function onOk() {
            const value = input.value;
            if (wantsConfirm && confirmInput.value !== value) {
                errorEl.textContent = 'The two entries do not match.';
                confirmInput.value = '';
                confirmInput.focus();
                return;
            }
            const problem = opts.validate ? opts.validate(value) : null;
            if (problem) {
                // Correct it in place. prompt() had to close and re-open,
                // which is why the old passcode flow lost the typed value.
                errorEl.textContent = problem;
                input.focus();
                return;
            }
            cleanup();
            resolve(value);
        }

        function onCancel() {
            cleanup();
            resolve(null);
        }

        function onKey(event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                onOk();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                onCancel();
            }
        }

        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
        modal.addEventListener('keydown', onKey);
    });
}

function usesLocalPassword() {
    // Password sign-ins do not carry auth_provider at all; a Firebase one
    // reports 'firebase' and has no local password to confirm against.
    const provider = state.currentUser && state.currentUser.auth_provider;
    return !provider || provider === 'password';
}

function openDeleteAccount() {
    if (!state.currentUser || !deleteAccountModal) return;
    const local = usesLocalPassword();

    document.getElementById('deleteAccountPasswordGroup').classList.toggle('hidden', !local);
    document.getElementById('deleteAccountUsernameGroup').classList.toggle('hidden', local);
    document.getElementById('deleteAccountTotpGroup')
        .classList.toggle('hidden', !state.currentUser.totp_enabled);

    [deleteAccountPassword, deleteAccountUsername, deleteAccountTotp]
        .forEach(field => { if (field) field.value = ''; });
    deleteAccountError.textContent = '';

    deleteAccountModal.classList.remove('hidden');
    (local ? deleteAccountPassword : deleteAccountUsername).focus();
}

function closeDeleteAccount() {
    if (!deleteAccountModal) return;
    deleteAccountModal.classList.add('hidden');
    // Do not leave a typed password sitting in the DOM behind a hidden modal.
    [deleteAccountPassword, deleteAccountUsername, deleteAccountTotp]
        .forEach(field => { if (field) field.value = ''; });
}

if (deleteAccountBtn) {
    deleteAccountBtn.addEventListener('click', openDeleteAccount);
}

const deleteAccountCancelBtn = document.getElementById('deleteAccountCancelBtn');
if (deleteAccountCancelBtn) {
    deleteAccountCancelBtn.addEventListener('click', closeDeleteAccount);
}

const deleteAccountConfirmBtn = document.getElementById('deleteAccountConfirmBtn');
if (deleteAccountConfirmBtn) {
    deleteAccountConfirmBtn.addEventListener('click', () => {
        const payload = {};
        if (usesLocalPassword()) {
            payload.password = deleteAccountPassword.value;
        } else {
            payload.confirm_username = deleteAccountUsername.value.trim();
        }
        if (state.currentUser.totp_enabled) {
            payload.totp = deleteAccountTotp.value.trim();
        }
        deleteAccountError.textContent = '';
        socket.emit('delete_account', payload);
    });
}

// Profile upload handlers
if (profilePicker) profilePicker.addEventListener('click', () => profileImageInput.click());
if (profileImageInput) {
    profileImageInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (file.size > 512 * 1024) {
            window.revealxDialogs.toast('Profile photos must be smaller than 512 KB.', { kind: 'error' });
            return;
        }

        const reader = new FileReader();
        reader.onload = (readerEvent) => {
            const dataUrl = readerEvent.target.result;
            const preview = document.getElementById('profilePreview');
            if (preview) {
                preview.src = dataUrl;
                preview.classList.remove('empty');
            }
            socket.emit('update_profile', { profile_image: dataUrl });
        };
        reader.readAsDataURL(file);
        profileImageInput.value = '';
    });
}

socket.on('profile_updated', (data) => {
    const preview = document.getElementById('profilePreview');
    if (preview && data.profile_image) {
        preview.src = data.profile_image;
        preview.classList.remove('empty');
    }
    if (state.selectedRecipientId) {
        requestChatHistory(state.selectedRecipientId);
    }
});

