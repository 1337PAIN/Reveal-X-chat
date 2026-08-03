/**
 * Administration panel.
 *
 * This file decides what to *draw*. It decides nothing about what is allowed:
 * every action below is re-authorised on the server against the caller's own
 * session and a fresh read of their role, so unhiding the button in devtools
 * buys an attacker nothing but an `admin_error`. tests/test_admin.py asserts
 * that for every admin event, from both a normal user's socket and an
 * anonymous one.
 *
 * Loaded after chat.js, which owns `socket` and `state`.
 */

(function () {
    'use strict';

    const adminBtn = document.getElementById('adminBtn');
    const adminModal = document.getElementById('adminModal');
    const adminPendingBadge = document.getElementById('adminPendingBadge');
    const adminFeedback = document.getElementById('adminFeedback');

    if (!adminModal) return;

    function setFeedback(message, isError) {
        if (!adminFeedback) return;
        adminFeedback.textContent = message || '';
        adminFeedback.classList.toggle('error', !!isError);
    }

    // Mirrors ChatRoom.ROLE_RANK. Used only to decide what to draw -- the
    // server applies the same rule and is the one that matters.
    const RANK = { user: 0, admin: 1, superadmin: 2 };
    const rankOf = (role) => RANK[role] || 0;

    function viewerRole() {
        return (window.state && window.state.currentUser && window.state.currentUser.role) || 'user';
    }

    /** May the signed-in account act on this one? Strictly greater rank. */
    function canManage(account) {
        const isSelf = window.state && window.state.currentUser
            && account.id === window.state.currentUser.id;
        if (isSelf) return false;
        return rankOf(viewerRole()) > rankOf(account.role);
    }

    function stateLabel(account) {
        if (account.account_state === 'pending') return 'Awaiting approval';
        if (account.account_state === 'disabled') return 'Disabled';
        return account.online ? 'Online' : 'Offline';
    }

    /** Built with DOM calls, so a username can never introduce markup. */
    function buildRow(account, isPendingList) {
        const li = document.createElement('li');
        li.className = 'admin-row state-' + account.account_state;

        const info = document.createElement('div');
        info.className = 'admin-row-info';

        const nameRow = document.createElement('div');
        nameRow.className = 'admin-row-name-line';

        const name = document.createElement('span');
        name.className = 'admin-row-name';
        name.textContent = account.username;
        nameRow.appendChild(name);

        if (rankOf(account.role) >= 1) {
            const tag = document.createElement('span');
            tag.className = account.role === 'superadmin' ? 'admin-tag super' : 'admin-tag';
            tag.textContent = account.role === 'superadmin' ? 'superadmin' : 'admin';
            nameRow.appendChild(tag);
        }
        if (account.totp_enabled) {
            const tag = document.createElement('span');
            tag.className = 'admin-tag subtle';
            tag.textContent = '2FA';
            nameRow.appendChild(tag);
        }
        info.appendChild(nameRow);

        const meta = document.createElement('span');
        meta.className = 'admin-row-meta';
        meta.textContent = stateLabel(account)
            + (account.email ? ' · ' + account.email : '')
            + (account.auth_provider && account.auth_provider !== 'password'
                ? ' · ' + account.auth_provider : '');
        info.appendChild(meta);
        li.appendChild(info);

        const actions = document.createElement('div');
        actions.className = 'admin-row-actions';
        const isSelf = window.state && window.state.currentUser
            && account.id === window.state.currentUser.id;

        function addButton(label, className, handler) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn ' + className;
            button.textContent = label;
            button.addEventListener('click', handler);
            actions.appendChild(button);
            return button;
        }

        if (account.account_state === 'pending') {
            addButton('Approve', 'btn-primary', function () {
                socket.emit('admin_set_state', { user_id: account.id, state: 'active' });
            });
            addButton('Reject', 'btn-danger', function () {
                if (confirm('Reject and delete the request from "' + account.username + '"?')) {
                    socket.emit('admin_delete_account', { user_id: account.id });
                }
            });
        } else if (!isPendingList) {
            const allowed = canManage(account);
            const why = isSelf
                ? 'You cannot do this to your own account'
                : (account.role === 'superadmin'
                    ? 'The superadmin account cannot be modified'
                    : 'Only the superadmin can manage other administrators');

            function gated(label, className, handler) {
                const button = addButton(label, className, handler);
                if (!allowed) { button.disabled = true; button.title = why; }
                return button;
            }

            if (account.account_state === 'active') {
                gated('Disable', 'btn-secondary', function () {
                    socket.emit('admin_set_state', { user_id: account.id, state: 'disabled' });
                });
            } else {
                gated('Enable', 'btn-primary', function () {
                    socket.emit('admin_set_state', { user_id: account.id, state: 'active' });
                });
            }

            gated('Reset password', 'btn-secondary', function () {
                const next = prompt('New password for "' + account.username + '" (at least 8 characters):');
                if (next === null) return;
                socket.emit('admin_reset_password', { user_id: account.id, password: next });
            });

            // Promotion is the superadmin's alone: an admin who could promote
            // could manufacture a peer and route around the rank entirely.
            if (viewerRole() === 'superadmin' && account.role !== 'superadmin' && !isSelf) {
                if (account.role === 'admin') {
                    addButton('Demote', 'btn-secondary', function () {
                        socket.emit('admin_set_role', { user_id: account.id, role: 'user' });
                    });
                } else {
                    addButton('Make admin', 'btn-secondary', function () {
                        socket.emit('admin_set_role', { user_id: account.id, role: 'admin' });
                    });
                }
            }

            gated('Delete', 'btn-danger', function () {
                if (confirm('Delete "' + account.username + '" and all of their messages? This cannot be undone.')) {
                    socket.emit('admin_delete_account', { user_id: account.id });
                }
            });
        }

        li.appendChild(actions);
        return li;
    }

    function render(payload) {
        const accounts = (payload && payload.accounts) || [];
        const pending = accounts.filter(function (a) { return a.account_state === 'pending'; });

        if (adminPendingBadge) {
            adminPendingBadge.textContent = String(pending.length);
            adminPendingBadge.classList.toggle('hidden', pending.length === 0);
        }
        const count = document.getElementById('adminPendingCount');
        if (count) count.textContent = String(pending.length);

        const pendingList = document.getElementById('adminPendingList');
        if (pendingList) {
            pendingList.innerHTML = '';
            if (!pending.length) {
                const empty = document.createElement('li');
                empty.className = 'admin-empty';
                empty.textContent = 'No requests waiting.';
                pendingList.appendChild(empty);
            } else {
                pending.forEach(function (a) { pendingList.appendChild(buildRow(a, true)); });
            }
        }

        const allList = document.getElementById('adminAccountList');
        if (allList) {
            allList.innerHTML = '';
            accounts
                .filter(function (a) { return a.account_state !== 'pending'; })
                .forEach(function (a) { allList.appendChild(buildRow(a, false)); });
        }
    }

    socket.on('admin_accounts', render);
    socket.on('admin_ok', function (data) { setFeedback(data.message || 'Done.', false); });
    socket.on('admin_error', function (data) { setFeedback(data.error || 'Refused.', true); });

    if (adminBtn) {
        adminBtn.addEventListener('click', function () {
            setFeedback('', false);
            adminModal.classList.remove('hidden');
            socket.emit('admin_list_accounts');
        });
    }
    const closeBtn = document.getElementById('closeAdminBtn');
    if (closeBtn) {
        closeBtn.addEventListener('click', function () { adminModal.classList.add('hidden'); });
    }
    const createBtn = document.getElementById('adminCreateBtn');
    if (createBtn) {
        createBtn.addEventListener('click', function () {
            const username = document.getElementById('adminNewUsername');
            const password = document.getElementById('adminNewPassword');
            const makeAdmin = document.getElementById('adminNewIsAdmin');
            socket.emit('admin_create_account', {
                username: username.value,
                password: password.value,
                make_admin: !!(makeAdmin && makeAdmin.checked),
            });
            username.value = '';
            password.value = '';
            if (makeAdmin) makeAdmin.checked = false;
        });
    }

    /** Called by chat.js on auth_success. */
    window.revealxApplyAdminVisibility = function (user) {
        const isAdmin = !!user && rankOf(user.role) >= 1;
        if (adminBtn) adminBtn.classList.toggle('hidden', !isAdmin);
        if (!isAdmin) adminModal.classList.add('hidden');

        // Only the superadmin may create administrators, so hide the option
        // rather than offering one the server will refuse.
        const makeAdminRow = document.getElementById('adminMakeAdminRow');
        if (makeAdminRow) {
            makeAdminRow.classList.toggle('hidden', !user || user.role !== 'superadmin');
        }
    };
})();
