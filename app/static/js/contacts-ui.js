/**
 * Reveal-X: adding contacts by username, and the requests list.
 *
 * Every account used to see every other account in the sidebar and could
 * message any of them. Now the sidebar lists contacts only: people are added
 * by exact username, the person asked accepts or declines, and the server
 * refuses messages between anyone else (handlers/contacts.py). This draws
 * the parts of that the user touches -- what the field sends, what each
 * answer says, and a row per pending request -- with createElement and
 * textContent, since every name in it is another user's input.
 *
 * A classic script, like voice-ui.js, so tests/js/contacts-ui.test.mjs loads
 * the shipped file through harness.mjs. The socket wiring is in
 * chat/contacts.js.
 */

(function () {
    const MAX_USERNAME = 64;

    /** What the field sends: trimmed, without a leading @, or '' if unusable. */
    function normaliseUsername(value) {
        const name = String(value == null ? '' : value).trim().replace(/^@+/, '').trim();
        if (!name || name.length > MAX_USERNAME || /\s/.test(name)) return '';
        return name;
    }

    /**
     * The toast for a contact_result. `kind` is 'error' for a refusal.
     * Usernames are only ever put in as text by the caller.
     */
    function resultMessage(result) {
        if (!result || !result.ok) {
            return { text: (result && result.error) || 'That did not work. Try again.', kind: 'error' };
        }
        const name = result.username || 'them';
        const text = {
            requested: `Request sent to ${name}. You can chat once they accept.`,
            already_requested: `You already asked ${name}. Waiting for them to accept.`,
            accepted: `${name} had already asked you, so you are now contacts.`,
            already_contacts: `${name} is already one of your contacts.`,
        }[result.status] || `Request sent to ${name}.`;
        return { text, kind: 'info' };
    }

    /** Ids in `next.incoming` that were not in `previous.incoming`: new requests to announce. */
    function newIncoming(previous, next) {
        const seen = new Set(((previous && previous.incoming) || []).map((r) => r.id));
        return ((next && next.incoming) || []).filter((r) => !seen.has(r.id));
    }

    function button(doc, label, className, name, onClick) {
        const el = doc.createElement('button');
        el.setAttribute('type', 'button');
        el.className = className;
        el.textContent = label;
        // The visible word starts the accessible name (speech input), and
        // the name says whose request it is.
        el.setAttribute('aria-label', name);
        el.addEventListener('click', onClick);
        return el;
    }

    /**
     * One pending request. Incoming: Accept and Decline. Outgoing: a
     * "Waiting" note and Cancel.
     */
    function buildRequestRow(doc, entry, handlers = {}) {
        const incoming = Boolean(handlers.incoming);
        const row = doc.createElement('li');
        row.className = `request-row ${incoming ? 'incoming' : 'outgoing'}`;

        const avatar = doc.createElement('span');
        avatar.className = 'avatar-dot request-avatar';
        avatar.setAttribute('aria-hidden', 'true');
        avatar.textContent = (entry.username || '?').charAt(0).toUpperCase();

        const text = doc.createElement('span');
        text.className = 'request-text';
        const name = doc.createElement('span');
        name.className = 'request-name';
        name.textContent = entry.username;
        const note = doc.createElement('span');
        note.className = 'request-note';
        note.textContent = incoming ? 'wants to add you' : 'waiting for them to accept';
        text.append(name, note);

        const actions = doc.createElement('span');
        actions.className = 'request-actions';
        if (incoming) {
            actions.append(
                button(doc, 'Accept', 'request-btn accept', `Accept ${entry.username}`,
                    () => handlers.onAccept && handlers.onAccept(entry)),
                button(doc, 'Decline', 'request-btn decline', `Decline ${entry.username}`,
                    () => handlers.onDecline && handlers.onDecline(entry)),
            );
        } else {
            actions.append(button(doc, 'Cancel', 'request-btn decline', `Cancel request to ${entry.username}`,
                () => handlers.onCancel && handlers.onCancel(entry)));
        }

        row.append(avatar, text, actions);
        return row;
    }

    window.revealxContactsUI = {
        normaliseUsername,
        resultMessage,
        newIncoming,
        buildRequestRow,
    };
})();
