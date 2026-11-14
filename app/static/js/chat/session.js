// Session warning and the inactivity timeout.
// Split out of chat.js; main.js loads the modules in order.

import {
    share2Memory,
    socket,
    state,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';

// ==========================================
// 4. Session Warning & Inactivity Monitor
// ==========================================

export function resetInactivityTimers() {
    clearTimeout(state.sessionTimeoutTimer);
    clearTimeout(state.sessionWarningTimer);
    if (state.countdownInterval) clearInterval(state.countdownInterval);

    // Prompt user 2 minutes before the mock 15 minute inactivity timeout
    state.sessionWarningTimer = setTimeout(() => {
        const modal = document.getElementById('sessionTimeoutModal');
        if (modal) {
            modal.classList.remove('hidden');
            announceToScreenReader('Inactivity Alert: Your session will expire in 2 minutes.');
            
            let remaining = 120;
            const countdownEl = document.getElementById('timeoutCountdown');
            if (countdownEl) countdownEl.textContent = '2:00';
            
            state.countdownInterval = setInterval(() => {
                remaining--;
                if (countdownEl) {
                    const mins = Math.floor(remaining / 60);
                    const secs = remaining % 60;
                    countdownEl.textContent = `${mins}:${secs < 10 ? '0' : ''}${secs}`;
                }
                if (remaining <= 0) {
                    clearInterval(state.countdownInterval);
                    logoutUser();
                }
            }, 1000);
        }
    }, 13 * 60 * 1000);

    // Hard auto-logout at 15 minutes of continuous inactivity
    state.sessionTimeoutTimer = setTimeout(logoutUser, 15 * 60 * 1000);
}

function extendSession() {
    const modal = document.getElementById('sessionTimeoutModal');
    if (modal) modal.classList.add('hidden');
    if (state.countdownInterval) clearInterval(state.countdownInterval);
    
    resetInactivityTimers();
    announceToScreenReader('Session extended successfully.');
    
    // session refresh activity flash indicator
    const header = document.querySelector('.header');
    if (header) {
        header.classList.add('activity-flash');
        setTimeout(() => header.classList.remove('activity-flash'), 500);
    }
}

export function logoutUser() {
    share2Memory.clear();
    // Plaintext of messages sent from this page (E2EE v4 keeps it because the
    // sender cannot decrypt them again). Sign-out does not reload, so drop it.
    if (window.revealxE2EE) window.revealxE2EE.forgetSentCopies();
    state.resumeToken = null;
    clearTimeout(state.sessionTimeoutTimer);
    clearTimeout(state.sessionWarningTimer);
    if (state.countdownInterval) clearInterval(state.countdownInterval);
    
    socket.emit('logout');
}

// Activity listeners to keep session alive
['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart'].forEach(eventName => {
    document.addEventListener(eventName, () => {
        if (state.currentUser) {
            resetInactivityTimers();
        }
    }, { passive: true });
});

const extendSessionBtn = document.getElementById('extendSessionBtn');
const endSessionBtn = document.getElementById('endSessionBtn');
if (extendSessionBtn) extendSessionBtn.addEventListener('click', extendSession);
if (endSessionBtn) endSessionBtn.addEventListener('click', logoutUser);

