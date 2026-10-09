# API reference

Everything the server exposes: 9 HTTP routes and 32 Socket.IO events, with the
fields each one actually reads. Extracted from `app/routes.py`, not written from
memory.

**One rule runs through all of it:** no handler trusts what the client says
about its own identity. The caller is resolved from the socket session
(`chat_room.get_session_user(request.sid)`), never from a `user_id` in the
payload. Where a `user_id` *is* accepted — the admin events — it names the
*target*, and the caller's authority to act on it is checked separately.

---

## HTTP

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/` | none | The chat application shell |
| GET | `/lab` | none | AI/ML visual cryptography dashboard |
| GET | `/lab/parity` | none | Browser-vs-server feature parity harness (M2 evidence) |
| GET | `/sw.js` | none | Service worker, served from root for whole-origin scope |
| GET | `/api/auth/config` | none | Which sign-in methods this deployment offers |
| POST | `/api/lab/features` | none | The server's own feature vector for an image |
| POST | `/api/lab/process` | none | Full lab pipeline: shares, attack, detect, reconstruct, score |
| GET | `/api/share/<message_id>/analysis` | session | Verify and classify a stored Share 1 |
| GET | `/share1/<message_id>?token=…` | capability | Serve Share 1, consuming its one-time access |

### `GET /api/auth/config`

```json
{ "firebase": { "apiKey": "…", "authDomain": "…", "projectId": "…", "appId": "…" },
  "password_login": true,
  "totp": true }
```

`firebase` is `null` unless the web config is present **and** the server can
verify tokens. Advertising a button that leads nowhere is worse than hiding it.

### `GET /share1/<message_id>`

Authorised by an unguessable `secrets.token_urlsafe(32)` capability in `?token=`,
not by session. Fetching **burns** the access; a second request 404s. The token
is the only thing standing between a stored share and anyone who can reach the
server, so it is never logged or echoed back.

### The two unauthenticated lab endpoints

`/api/lab/features` and `/api/lab/process` take an image and do real CPU work
with **no authentication** — about 0.73 s and 0.35 s per request. That is
deliberate, so the lab can be demonstrated without an account.

They are rate limited together: **20 requests per address per minute**, one
budget shared across both routes because they cost the same CPU. Over the
budget the response is `429` with

```json
{ "ok": false, "error": "Too many requests. Try again in 43s." }
```

The limit is per address and in-process. What it does and does not cover is
set out in [SECURITY.md](SECURITY.md).

---

## Socket.IO

### Lifecycle

| Event | Direction | Notes |
| --- | --- | --- |
| `connect` | in | Session established |
| `disconnect` | in | Clears the session and announces presence |

### Authentication

| Event | In | Out |
| --- | --- | --- |
| `register` | `username`, `password` | `registration_submitted`, `auth_error` |
| `login` | `username`, `password` | `auth_success` / `totp_required` / `auth_error` |
| `firebase_login` | `id_token` | `auth_success` / `totp_required` / `auth_error` |
| `verify_totp` | `code` | `auth_success`, `totp_error`, `auth_error` |
| `logout` | — | `logout_success` |

`register` **does not sign anyone in.** Accounts start `pending` and an
administrator approves them.

`login` returns one of three generic failures — bad credentials, awaiting
approval, disabled — and only *bad credentials* counts toward the throttle (8
per account, 50 per address, 15-minute window). The other two are returned after
the password already verified, so they are not guesses.

`firebase_login` takes a Google-signed JWT. The UID comes only from verified
claims; nothing the client asserts about itself is read. Accounts are matched on
**UID, never email**.

### Identity and profile

| Event | In | Out |
| --- | --- | --- |
| `register_public_key` | `public_key` (base64 SPKI) | `public_key_registered` |
| `update_profile` | `profile_image` | `profile_updated` |
| `update_username` | `new_username` | `settings_saved`, `settings_error` |
| `update_bio` | `bio` | — |
| `update_status` | `status` | `users_updated` |

`register_public_key` publishes the ECDH P-256 public half. The private key never
leaves the browser — it is generated non-extractable and kept in IndexedDB.

### Two-factor

| Event | In | Out |
| --- | --- | --- |
| `totp_begin_enrol` | — | `totp_enrolment` (secret + QR) |
| `totp_confirm_enrol` | `code` | `totp_state`, `settings_saved` |
| `totp_disable` | `code` | `totp_state`, `settings_saved` |

Disabling requires a **current valid code**, so someone with a borrowed session
cannot quietly remove the second factor.

### Messaging

| Event | In | Out |
| --- | --- | --- |
| `select_chat` | `recipient_id`, `send_read_receipts` | `chat_history` |
| `send_message` | `recipient_id`, `message`, `encrypted`, `iv`, `reply_to` | `new_message`, `message_error` |
| `send_voice` | `recipient_id`, `audio`, `mime`, `encrypted`, `iv`, `reply_to` | `new_message`, `message_error` |
| `send_image` | `recipient_id`, `image`, `reply_to` | `new_message`, `image_error` |
| `forward_message` | `message_id`, `recipient_id` | `new_message`, `message_error` |
| `react_message` | `message_id`, `emoji` | `message_reactions` |
| `mark_read` | `sender_id` | `messages_read` |
| `typing` | `recipient_id`, `is_typing` | `typing` |
| `delete_message` | `message_id` | `message_deleted` |
| `revoke_share` | `message_id` | `share_revoked` |

`encrypted` + `iv` mean the body is AES-GCM ciphertext the server cannot read.
With `encrypted` false the server stores plaintext — kept so the project can
demonstrate the difference side by side.

`send_image` is the exception to end-to-end encryption: the server receives the
**plaintext image**, splits it, stores Share 1 and sends Share 2 live. The
recipient must be online. See the boundary section of
[SECURITY.md](SECURITY.md).

`react_message` with an empty emoji clears the reaction. `delete_message` scope
depends on who asks — a sender deletes for everyone and Share 1 leaves the disk;
a recipient hides only their own copy, and the sender is not told, because that
would leak that it was read.

### Administration

| Event | In | Rank required |
| --- | --- | --- |
| `admin_list_accounts` | — | admin |
| `admin_set_state` | `user_id`, `state` | admin, above target |
| `admin_create_account` | `username`, `password`, `make_admin` | admin (superadmin to make admins) |
| `admin_reset_password` | `user_id`, `password` | admin, above target |
| `admin_delete_account` | `user_id` | admin, above target |
| `admin_set_role` | `user_id`, `role` | superadmin |

Authority is **strictly greater rank** (`user < admin < superadmin`), so admins
cannot act on each other and nobody can act on themselves. Every one of these is
tested for refusal from a non-admin and from an anonymous socket
(`tests/test_admin.py`). Admin actions are written to an audit log.

### Server-initiated

`new_message`, `users_updated`, `chat_history`, `message_reactions`,
`message_deleted`, `share_revoked`, `profile_updated`, `public_key_registered`,
`totp_state`, `totp_enrolment`, `totp_required`, `admin_accounts`, `admin_ok`,
`force_signed_out`, `auth_success`, and the error channels `auth_error`,
`admin_error`, `settings_error`, `message_error`, `image_error`, `totp_error`.

`force_signed_out` is pushed when an account is disabled or deleted while a
session is live — the change takes effect immediately rather than at next login.

---

## Error handling

Errors arrive on a channel matching the request (`auth_error`, `admin_error`,
`settings_error`, `message_error`, `image_error`, `totp_error`) with a single
`error` string meant to be shown to the user.

Messages are deliberately uninformative where detail would help an attacker.
"Invalid username or password" does not say which was wrong. "Sign-in token was
rejected" does not say whether the token was expired, forged, or from another
project — that distinction describes the server's state and is written to the
server log instead.
