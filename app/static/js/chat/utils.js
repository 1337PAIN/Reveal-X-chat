// Search debouncing and swipe gestures.
// Split out of chat.js; main.js loads the modules in order.

import { late } from './late.js';
import {
    messageSearchInput,
    messagesContainer,
    state,
    userSearchInput,
} from './core.js';
import {
    announceToScreenReader,
} from './basics.js';

// ==========================================
// 7. Search Debouncing & Utilities
// ==========================================

// debounce() is in sanitize.js, loaded before this file.

// Attach debounced filter elements
messageSearchInput.addEventListener('input', debounce((...args) => late.filterVisibleMessages(...args), 300));
userSearchInput.addEventListener('input', debounce(() => {
    state.userSearch = userSearchInput.value.trim().toLowerCase();
    late.updateUsersList(state.users);
}, 300));

// Mobile swipe listeners for gesture responses
let touchStartX = 0;
let touchStartY = 0;
messagesContainer.addEventListener('touchstart', (event) => {
    touchStartX = event.touches[0].clientX;
    touchStartY = event.touches[0].clientY;
}, { passive: true });

messagesContainer.addEventListener('touchend', (event) => {
    const diffX = event.changedTouches[0].clientX - touchStartX;
    const diffY = event.changedTouches[0].clientY - touchStartY;
    
    // Horizontal swipe reply action
    if (Math.abs(diffX) > 100 && Math.abs(diffY) < 30) {
        const messageNode = event.target.closest('.message');
        if (messageNode) {
            const messageId = messageNode.dataset.messageId;
            if (messageId) {
                // Trigger swipe reply preview shortcut
                announceToScreenReader('Swipe gesture triggered reply.');
                const replyTitle = document.getElementById('replyTitle');
                const replyText = document.getElementById('replyText');
                const replyBar = document.getElementById('replyBar');
                
                if (replyBar && replyText && replyTitle) {
                    const contentNode = messageNode.querySelector('.message-content');
                    replyText.textContent = contentNode ? contentNode.textContent : 'File Share';
                    replyBar.classList.remove('hidden');
                    state.pendingReplyId = messageId;
                }
            }
        }
    }
}, { passive: true });

