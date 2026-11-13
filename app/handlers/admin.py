"""Administration events. Every one funnels through _require_admin
(in common), which re-reads the caller's role from the database.
"""

from app.handlers.common import *  # noqa: F401,F403


@socketio.on('admin_list_accounts')
@session_required
def handle_admin_list_accounts():
    if not _require_admin():
        return
    accounts = chat_room.list_accounts()
    pending = sum(1 for a in accounts if a['account_state'] == 'pending')
    emit('admin_accounts', {'accounts': accounts, 'pending': pending}, to=request.sid)


@socketio.on('admin_set_state')
@session_required
def handle_admin_set_state(data):
    """Approve a request, disable an account, or re-enable a disabled one."""
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')
    state = data.get('state')

    if not _require_target(admin, target_id):
        return

    ok, error = chat_room.set_account_state(target_id, state)
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return

    _audit(admin, 'set_state', target_id, f'state={state}')
    if state != 'active':
        _disconnect_user_sessions(target_id, 'Your account was disabled by an administrator.')
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f'Account set to {state}.'}, to=request.sid)
    emit_users()


@socketio.on('admin_create_account')
@session_required
def handle_admin_create_account(data):
    """Create an account directly, already approved."""
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    make_admin = bool(data.get('make_admin'))
    if make_admin and not chat_room.is_superadmin(admin['id']):
        emit('admin_error', {'error': 'Only the superadmin can create administrators'},
             to=request.sid)
        return

    user, error = chat_room.create_user(
        data.get('username'), data.get('password'),
        account_state='active',
        role='admin' if make_admin else 'user',
    )
    if error:
        emit('admin_error', {'error': error}, to=request.sid)
        return
    _audit(admin, 'create_account', user['id'],
           f"role={'admin' if make_admin else 'user'}")
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f"Created {user['username']}."}, to=request.sid)
    emit_users()


@socketio.on('admin_reset_password')
@session_required
def handle_admin_reset_password(data):
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    if not _require_target(admin, data.get('user_id')):
        return
    ok, error = chat_room.set_password(data.get('user_id'), data.get('password'))
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return
    _audit(admin, 'reset_password', data.get('user_id'))
    # Old sessions keep working after a reset, which would defeat the point of
    # resetting a compromised account's password.
    _disconnect_user_sessions(data.get('user_id'),
                              'Your password was changed by an administrator.')
    emit('admin_ok', {'message': 'Password updated.'}, to=request.sid)


@socketio.on('admin_delete_account')
@session_required
def handle_admin_delete_account(data):
    admin = _require_admin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')

    if not _require_target(admin, target_id):
        return
    target = chat_room.get_user(target_id)

    _audit(admin, 'delete_account', target)
    _disconnect_user_sessions(target_id, 'Your account was removed by an administrator.')
    chat_room.purge_user(target_id)
    _push_accounts_to_admins()
    emit('admin_ok', {'message': f"Deleted {target['username']} and their messages."},
         to=request.sid)
    emit_users()


@socketio.on('admin_set_role')
@session_required
def handle_admin_set_role(data):
    """Promote a user to administrator, or demote one back.

    Superadmin only, and it cannot mint another superadmin: that rank comes
    from the environment at startup and nowhere else, so there is exactly one
    and it cannot be granted through the UI.
    """
    admin = _require_superadmin()
    if not admin:
        return
    data = data or {}
    target_id = data.get('user_id')
    if not _require_target(admin, target_id):
        return

    ok, error = chat_room.set_role(target_id, data.get('role'))
    if not ok:
        emit('admin_error', {'error': error}, to=request.sid)
        return

    _audit(admin, 'set_role', target_id, f"role={data.get('role')}")

    # Their session caches a role, and the roster they can see depends on it.
    _disconnect_user_sessions(target_id, 'Your account permissions were changed.')
    _push_accounts_to_admins()
    emit('admin_ok', {'message': 'Role updated.'}, to=request.sid)
    emit_users()

# Create a pending account request and keep authentication disabled
# until an administrator approves the new account.
