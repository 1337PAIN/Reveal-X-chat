// Contacts: the "Add by username" field, the requests list, Remove contact.
// The rules (what is sent, what each answer says, the rows) are in
// contacts-ui.js and tested there; this connects them to the socket.

import { socket, state } from './core.js';
import { announceToScreenReader } from './basics.js';

const ui = window.revealxContactsUI;
const form = document.getElementById('addContactForm');
const input = document.getElementById('addContactInput');
const section = document.getElementById('contactRequests');
const list = document.getElementById('contactRequestsList');
const removeBtn = document.getElementById('removeContactBtn');

let requests = null;     // the last contacts_updated, to spot new requests

if (form && input) {
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const username = ui.normaliseUsername(input.value);
        if (!username) {
            window.revealxDialogs.toast('Type their exact username, without spaces.', { kind: 'error' });
            return;
        }
        socket.emit('contact_request', { username });
    });
}

// A new sign-in (perhaps as someone else) starts the comparison afresh, so
// the requests waiting at sign-in are not announced as new.
socket.on('auth_success', () => {
    requests = null;
});

socket.on('contact_result', (result) => {
    const shown = ui.resultMessage(result);
    window.revealxDialogs.toast(shown.text, { kind: shown.kind });
    if (result && result.ok && input) input.value = '';
});

function answer(entry, accept) {
    socket.emit('contact_respond', { user_id: entry.id, accept });
}

function renderRequests(data) {
    if (!section || !list) return;
    const incoming = (data && data.incoming) || [];
    const outgoing = (data && data.outgoing) || [];
    list.replaceChildren(
        ...incoming.map((entry) => ui.buildRequestRow(document, entry, {
            incoming: true,
            onAccept: (e) => answer(e, true),
            onDecline: (e) => answer(e, false),
        })),
        ...outgoing.map((entry) => ui.buildRequestRow(document, entry, {
            onCancel: (e) => socket.emit('contact_remove', { user_id: e.id }),
        })),
    );
    section.hidden = incoming.length + outgoing.length === 0;
}

socket.on('contacts_updated', (data) => {
    // The first list after sign-in is not news; requests that arrive later are.
    if (requests) {
        ui.newIncoming(requests, data).forEach((entry) => {
            window.revealxDialogs.toast(`${entry.username} wants to add you as a contact.`);
            announceToScreenReader(`${entry.username} wants to add you as a contact.`);
        });
    }
    requests = data;
    renderRequests(data);
});

if (removeBtn) {
    removeBtn.addEventListener('click', () => {
        const user = state.users.find((u) => u.id === state.selectedRecipientId);
        if (!user) return;
        window.revealxDialogs.askConfirm({
            title: `Remove ${user.username}?`,
            body: 'You will not be able to message each other until one of you adds the other again.',
            confirmLabel: 'Remove',
        }).then((agreed) => {
            if (agreed) socket.emit('contact_remove', { user_id: user.id });
        });
    });
}
