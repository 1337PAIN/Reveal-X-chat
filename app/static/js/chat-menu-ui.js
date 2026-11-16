/**
 * Reveal-X: the chat menu (⋮ beside a contact's name) and what it changes.
 *
 * The chat header had one lone "Remove contact" icon. It is now a menu with
 * everything you can do with that conversation: Contact info, Mute, Pin,
 * Archive, Lock, Clear chat, Block and Remove contact. Pin, archive, mute and
 * lock are kept per account on the server and arrive with the roster
 * (handlers/contacts.py); this file decides what the menu offers and how the
 * contact list is ordered and grouped because of them:
 *   - pinned chats first, the most recently pinned on top;
 *   - archived chats out of the list, behind an "Archived" row;
 *   - locked chats out of the list, behind a "Locked chats" row, until the
 *     chat-lock PIN is entered; then they show, marked as locked;
 *   - a muted or locked chat raises no sound or popup, and a locked chat's
 *     notification would name nobody.
 *
 * Built with createElement and textContent. A classic script, like
 * contacts-ui.js, so tests/js/chat-menu-ui.test.mjs loads the shipped file.
 */

(function () {
    /** The menu for one contact, in order. `danger` items ask first. */
    function menuItems(user) {
        if (!user) return [];
        return [
            { action: 'info', label: 'Contact info' },
            { action: 'mute', label: user.muted ? 'Unmute notifications' : 'Mute notifications' },
            { action: 'pin', label: user.pinned ? 'Unpin chat' : 'Pin chat' },
            { action: 'archive', label: user.archived ? 'Unarchive chat' : 'Archive chat' },
            { action: 'lock', label: user.locked ? 'Unlock chat' : 'Lock chat' },
            { action: 'clear', label: 'Clear chat', danger: true },
            { action: 'block', label: 'Block', danger: true },
            { action: 'remove', label: 'Remove contact', danger: true },
        ];
    }

    /** The chat_pref a menu action sends, or null if it is not a plain toggle. */
    function prefChange(action, user) {
        const pref = { mute: 'muted', pin: 'pinned', archive: 'archived' }[action];
        if (!pref || !user) return null;
        return { peer_id: user.id, pref, value: !user[pref] };
    }

    /**
     * Group and order the roster for the list.
     * @returns {{main, archived, lockedHidden}} main and archived are lists;
     *   lockedHidden is how many locked chats are out of sight.
     */
    function organise(users, { lockOpen } = {}) {
        const visible = [];
        let lockedHidden = 0;
        (users || []).forEach((user) => {
            if (user.locked && !lockOpen) {
                lockedHidden += 1;
                return;
            }
            visible.push(user);
        });
        // Stable: the server's alphabetical order stays within each group.
        const pinned = visible.filter((u) => u.pinned && !u.archived)
            .sort((a, b) => String(b.pinned_at || '').localeCompare(String(a.pinned_at || '')));
        const rest = visible.filter((u) => !u.pinned && !u.archived);
        return {
            main: pinned.concat(rest),
            archived: visible.filter((u) => u.archived),
            lockedHidden,
        };
    }

    /** Is this chat out of sight in the list as it stands? */
    function isHidden(user, { lockOpen, showArchived } = {}) {
        if (!user) return true;
        return (user.locked && !lockOpen) || (user.archived && !showArchived);
    }

    /**
     * The alert for an incoming message: null for none (muted), a generic
     * one for a locked chat, otherwise the sender and the preview.
     */
    function notification(user, sender, preview) {
        if (user && user.muted) return null;
        if (user && user.locked) return { title: 'Reveal-X', body: 'New message', sound: true };
        return { title: `New message from ${sender}`, body: preview, sound: true };
    }

    /** Words for screen readers after the name: ", pinned, muted, locked". */
    function flagWords(user) {
        const words = [];
        if (user.pinned) words.push('pinned');
        if (user.muted) words.push('muted');
        if (user.locked) words.push('locked');
        return words.length ? `, ${words.join(', ')}` : '';
    }

    /** A PIN is 4 to 8 digits; returns the problem, or '' when it is fine. */
    function pinProblem(pin) {
        return /^[0-9]{4,8}$/.test(String(pin || '')) ? '' : 'Use 4 to 8 digits.';
    }

    /** Fill a role="menu" element with one menuitem per action. */
    function buildMenu(doc, menu, items, onPick) {
        menu.textContent = '';
        items.forEach((item) => {
            const button = doc.createElement('button');
            button.setAttribute('type', 'button');
            button.setAttribute('role', 'menuitem');
            button.className = `message-menu-item${item.danger ? ' danger' : ''}`;
            button.dataset.action = item.action;
            button.textContent = item.label;
            button.addEventListener('click', () => onPick(item.action));
            menu.append(button);
        });
    }

    window.revealxChatMenuUI = {
        menuItems,
        prefChange,
        organise,
        isHidden,
        notification,
        flagWords,
        pinProblem,
        buildMenu,
    };
})();
