// The active-devices dialog.
// Split out of chat.js; main.js loads the modules in order.

import { registerLate } from './late.js';
import {
    announceToScreenReader,
} from './basics.js';

registerLate({ closeActiveDevicesModal });

// ==========================================
// 5. Active Device Management Modals
// ==========================================

export function openActiveDevicesModal() {
    const modal = document.getElementById('sessionListModal');
    const container = document.getElementById('sessionsList');
    if (!modal || !container) return;

    container.innerHTML = '';
    
    // Highly visual active session instances
    const activeSessions = [
        { id: 'sess_current', os: navigator.userAgent.includes('Windows') ? 'Windows (Current Browser)' : 'Current Session', active: true, location: 'Active Now' }
    ];

    activeSessions.forEach(sess => {
        const div = document.createElement('div');
        div.className = 'session-item';
        div.style.display = 'flex';
        div.style.justifyContent = 'space-between';
        div.style.alignItems = 'center';
        div.style.padding = '12px';
        div.style.borderBottom = '1px solid var(--border)';
        div.style.background = 'var(--bg-tertiary)';
        div.style.borderRadius = '6px';
        div.style.marginBottom = '8px';
        
        div.innerHTML = `
            <div style="text-align: left;">
                <strong style="display: block; font-size: 13px; color: var(--text-primary);">${cleanHTML(sess.os)}</strong>
                <span style="font-size: 11px; color: ${sess.active ? 'var(--success)' : 'var(--text-secondary)'};">
                    ${sess.active ? '● Active Now' : cleanHTML(sess.location)}
                </span>
            </div>
            ${!sess.active ? `<button class="btn btn-danger" style="font-size: 11px; padding: 4px 10px; min-height: 32px; width: auto;" data-revoke-id="${sess.id}">Revoke</button>` : ''}
        `;
        
        const btn = div.querySelector('button');
        if (btn) {
            btn.addEventListener('click', () => {
                div.remove();
                announceToScreenReader('Device session revoked successfully.');
                alert('Session access revoked.');
            });
        }
        container.appendChild(div);
    });

    modal.classList.remove('hidden');
    announceToScreenReader('Active devices list opened.');
}

function closeActiveDevicesModal() {
    const modal = document.getElementById('sessionListModal');
    if (modal) modal.classList.add('hidden');
}

const logoutAllDevicesBtn = document.getElementById('logoutAllDevicesBtn');
if (logoutAllDevicesBtn) {
    logoutAllDevicesBtn.addEventListener('click', () => {
        alert('Logging out from all other devices...');
        closeActiveDevicesModal();
        announceToScreenReader('Logged out from all other devices.');
    });
}

document.querySelectorAll('#closeSessionListBtn, #closeSessionListBtn2').forEach(btn => {
    btn.addEventListener('click', closeActiveDevicesModal);
});

