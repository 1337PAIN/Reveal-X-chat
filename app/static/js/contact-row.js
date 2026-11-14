/**
 * Reveal-X: one row of the contact list.
 *
 * Rows used to be <li> elements with a click listener and an innerHTML body.
 * An <li> is not focusable and is not announced as a control, so the contact
 * list -- the way into every conversation -- could not be reached by
 * keyboard at all. Each row now carries a <button type="button">: Tab
 * reaches it, Enter and Space open the chat with no key handling of our own,
 * and the open chat is marked aria-current="true".
 *
 * Everything from another user (name, bio, photo) is set as text or as a
 * property, never as markup. The photo in particular came from a data URL
 * that used to be interpolated into innerHTML; see tests/test_profile_image.py.
 *
 * A classic script, like sanitize.js, so tests/js/contact-row.test.mjs can
 * load the shipped file through harness.mjs.
 */

(function () {
    /**
     * @param {Document} doc
     * @param {object} user  {id, username, status, online, bio, profile_image}
     * @param {object} options
     * @param {boolean} options.selected     this is the open chat
     * @param {number}  options.unread       unread message count
     * @param {string}  options.statusLabel  human label for user.status
     * @param {function(string)} [options.onSelect]
     * @returns {HTMLLIElement}
     */
    /** Text for screen readers only (.sr-only in settings.css). */
    function hiddenText(doc, text) {
        const span = doc.createElement('span');
        span.className = 'sr-only';
        span.textContent = text;
        return span;
    }

    function buildContactRow(doc, user, options) {
        const opts = options || {};
        const status = user.status || (user.online ? 'online' : 'offline');

        const li = doc.createElement('li');
        li.dataset.userId = user.id;
        li.dataset.status = status;
        if (opts.selected) li.classList.add('active');
        if (!user.online) li.classList.add('offline');

        const button = doc.createElement('button');
        button.setAttribute('type', 'button');
        button.className = 'contact-button';
        if (opts.selected) button.setAttribute('aria-current', 'true');

        let avatar;
        if (user.profile_image) {
            avatar = doc.createElement('img');
            avatar.className = 'avatar-img';
            // The name is right beside it, so the photo adds nothing for a
            // screen reader; an alt repeating the name would read it twice.
            avatar.alt = '';
            avatar.src = user.profile_image;
        } else {
            avatar = doc.createElement('span');
            avatar.className = 'avatar-dot';
            avatar.setAttribute('aria-hidden', 'true');
            avatar.textContent = (user.username || '?').charAt(0).toUpperCase();
        }

        const meta = doc.createElement('span');
        meta.className = 'user-meta';
        const name = doc.createElement('span');
        name.className = 'user-name';
        name.textContent = user.username;
        const line = doc.createElement('span');
        line.className = 'user-status-line';
        const statusText = user.bio || opts.statusLabel || status;
        line.textContent = statusText;
        meta.append(name, hiddenText(doc, ', '), line);

        button.append(avatar, meta);

        // The button is named by its content, not an aria-label. An
        // aria-label ("alice, Online") left out the avatar initial that is
        // visibly part of the button, so the name did not contain what is on
        // screen -- and speech-input users say what they see. Read from
        // content, the name and status ran together ("e2ebobOffline"), so a
        // visually hidden ", " separates them.
        const unread = Number(opts.unread) || 0;
        if (unread > 0) {
            // The badge is hidden and the count said in words instead, since
            // aria-label on a plain <span> is not allowed.
            button.append(hiddenText(doc, `, ${unread} unread messages`));
            const badge = doc.createElement('span');
            badge.className = 'unread-badge';
            badge.setAttribute('aria-hidden', 'true');
            badge.textContent = String(unread);
            button.append(badge);
        }

        if (typeof opts.onSelect === 'function') {
            button.addEventListener('click', () => opts.onSelect(user.id));
        }
        li.append(button);
        return li;
    }

    window.revealxContactRow = { buildContactRow };
}());
