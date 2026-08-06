# Administration and account approval

Reveal-X is **closed registration**. Anyone can *request* an account, but nobody
can sign in until an administrator approves it.

## Accounts have two independent properties

| Property | Values | Meaning |
| --- | --- | --- |
| `role` | `user`, `admin`, `superadmin` | What may the account manage? |
| `account_state` | `pending`, `active`, `disabled` | May the account sign in at all? |

### The three ranks

| Rank | Can manage | Notes |
| --- | --- | --- |
| `superadmin` | admins **and** users | Exactly one. Named by the environment. Cannot be modified by anyone, including itself. |
| `admin` | users only | Approve requests, create users, disable, reset passwords, delete. Cannot touch another admin or the superadmin. |
| `user` | nothing | |

The rule is one line: **an account may only act on one of strictly lower rank,
and never on itself.** That single comparison produces every behaviour above —
an admin cannot disable a peer, cannot promote a friend to peer rank, and cannot
reach the superadmin; and nobody can strand the deployment by demoting or
disabling their own account.

Only the superadmin can promote a user to admin or demote one back, and only the
superadmin can create an account that is already an admin. If an ordinary admin
could promote, they could manufacture a peer and route around the rank
entirely.

`account_state` is deliberately **not** the existing `status` column — that one
is presence (online / away / do-not-disturb) and is unrelated.

Only `active` accounts can authenticate, and only `active` accounts appear in
anyone's chat list. A pending request is invisible to everyone except an
administrator.

## Setting up the administrator

Set two environment variables and start the server:

```bash
REVEALX_ADMIN_USER=root
REVEALX_ADMIN_PASSWORD=choose-something-long
```

Windows PowerShell, for one session (substitute your own password -- do not
commit a real one to this file):

```powershell
$env:REVEALX_ADMIN_USER="root"; $env:REVEALX_ADMIN_PASSWORD="<your-admin-password>"; python run.py
```

On startup the server prints one of:

```text
[admin] Created administrator account "root".
[admin] Administrator "root" is active.
```

The password is never printed, so it will not end up in a log or a
screen-shared terminal.

This runs on **every** start and is safe to repeat. If the account already
exists it is promoted to `superadmin` and re-activated, so a fresh demo database
always ends up with a working administrator.

**There is exactly one superadmin, and only the environment can name it.** The
rank is not assignable through the panel — `set_role` refuses it. Pointing
`REVEALX_ADMIN_USER` at a different account on the next start *moves* the rank
(the previous holder is demoted to `admin`) rather than accumulating a second
one.

### The password is only used to create the account

If the account already exists, its password is left alone. This is deliberate:

- Otherwise anyone able to set an environment variable could silently take over
  an established admin account.
- And a stale value left in a shell profile would quietly revert a password the
  administrator had deliberately changed.

`test_ensure_admin_does_not_reset_an_existing_password` pins that behaviour. To
change an admin password, use the admin panel's **Reset password**.

### With nothing configured

The server prints a warning and carries on. Requests can be filed but nobody can
approve them, so the deployment is effectively closed until an admin is
configured. Existing accounts from before this feature default to `active` and
keep working, so upgrading never locks anyone out.

## What the administrator can do

Sign in and click **Admin** in the header. The badge shows the number of waiting
requests.

| Action | Who | Effect |
| --- | --- | --- |
| **Approve** | admin+ | `pending` → `active`. The account can now sign in. |
| **Reject** | admin+ | Deletes the request outright. |
| **Create** | admin+ | Makes an account that is `active` immediately. Creating it *as an admin* is superadmin-only. |
| **Make admin / Demote** | superadmin | Promotes a user to admin, or demotes one back. Ends their session, because the roster they may see depends on their rank. |
| **Disable** | admin+ | `active` → `disabled`. Blocks sign-in and **ends any live session**, but keeps the account and its history. Reversible. |
| **Enable** | admin+ | `disabled` → `active`. |
| **Reset password** | admin+ | Sets a new password and **ends any live session**. |
| **Delete** | admin+ | Removes the account, its messages, and its stored Share 1 files. Irreversible. |

Disable and reset both terminate live sessions on purpose. Without that, a
disabled account keeps chatting on the socket it already holds until the tab is
closed, and a password reset — whose whole point is locking someone out — leaves
them signed in.

## The authorisation model

**The panel decides what to draw. It decides nothing about what is allowed.**

Every admin action is a Socket.IO event that passes through one function,
`_require_admin()` in `app/routes.py`. It:

1. Requires an authenticated session on that socket, and
2. **Re-reads the caller's role from the database** rather than trusting the
   copy cached in the session, so an admin demoted mid-session stops being one
   immediately.

Rank is checked the same way, in `_require_target()`, which every action that
names a target passes through. Unhiding the Admin button in devtools therefore
achieves nothing, and neither does re-enabling a greyed-out button: an ordinary
admin firing all four superadmin-targeted events by hand gets

```text
ERR: The superadmin account cannot be modified
ERR: Only the superadmin can do that
ERR: Only the superadmin can create administrators
```

There is a test for exactly this — every admin event, fired from both a normal user's
socket and an anonymous one, asserting the refusal:

```bash
pytest tests/test_admin.py -k "cannot_use_any_admin_event or backdoor or demotion" -v
```

### Lockout guards

Falling out of the rank comparison, not bolted on beside it:

- Nobody can disable, delete, or demote **their own** account — self-management
  is excluded from `can_manage()` outright.
- The **superadmin cannot be modified by anyone**, so the deployment always
  retains one account that can administer it.

`test_the_superadmin_cannot_disable_or_demote_itself` and
`test_an_admin_cannot_touch_the_superadmin` pin both.

### Google sign-in does not bypass approval

A first-time Firebase/Google sign-in creates a **pending** account, exactly like
filling in the request form. Without this the gate would be decorative: anyone
with a Google account could walk in while password sign-ups waited.
`test_a_first_google_sign_in_only_requests_an_account` pins it.

### Registration does not confirm whether a username exists

Requesting an account with a name that is already taken returns the same
"Request submitted" message as any other request. Since requests are invisible
to the requester, an honest "already exists" would turn the form into a way to
enumerate who holds an account.

Separately, a wrong password is reported as *"Invalid username or password"*
before any "awaiting approval" message — otherwise the state of an account would
leak to someone who could not authenticate to it.

## Resetting the demo

```bash
rm app/data/reveal_x.db
```

Then restart with the environment variables set. The admin is recreated
automatically; everything else starts empty.
