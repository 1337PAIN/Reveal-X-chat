# API reference

Everything the server exposes: 9 HTTP routes and 44 Socket.IO events, with the
fields each one actually reads. Extracted from `app/handlers/`, not written from
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
| `resume_session` | `token` | `auth_success` (with `resumed: true`) / `auth_required` (with `resume_failed: true`) |
| `logout` | — | `logout_success` |

`auth_success` carries `resume_token`, a 256-bit bearer token issued only once
the sign-in is complete (after the second factor, if one is enabled). A socket
that reconnects sends it as `resume_session` to get its session back without
the password or a TOTP code. It lasts **12 hours** from sign-in and is not
renewed by resuming. It stops working early on `logout` (on every device) and
when the account's password is reset, its role changes, it is disabled, or it
is deleted. The server stores only its SHA-256. The client keeps it in memory,
so a page reload means signing in again.

Every event other than `register`, `login`, `firebase_login`, `verify_totp`,
`resume_session` and `logout` needs a session. Without one it answers
`auth_required` with an `error` string and does nothing else. It used to return
silently, so a page whose connection had dropped looked signed in and quietly
ignored everything.

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
| `update_profile` | `profile_image` (`data:image/png|jpeg|webp;base64,...` or empty to clear) | `profile_updated` |
| `update_username` | `new_username` | `settings_saved`, `settings_error` |
| `update_bio` | `bio` | — |
| `update_status` | `status` | `users_updated` |

`update_profile` accepts exactly `data:image/(png|jpeg|webp);base64,<base64>`
and nothing else, up to 512 KB and 2048 × 2048 pixels. The server decodes it
with Pillow and stores a PNG it encoded itself, so what other users receive is
always a plain `data:image/png;base64,...`. Anything else answers `auth_error`.

`register_public_key` publishes the ECDH P-256 public half. The private key never
leaves the browser — it is generated non-extractable and kept in IndexedDB.

### Two-factor

| Event | In | Out |
| --- | --- | --- |
| `totp_begin_enrol` | — | `totp_enrolment` (secret + QR) |
| `totp_confirm_enrol` | `code` | `totp_state`, `settings_saved` |
| `totp_disable` | `code` | `totp_state`, `settings_saved` |

Disabling requires a **current valid code**, so someone with a borrowed session
cannot quietly remove the second factor. Confirming enrolment and disabling
both draw on the sign-in throttle (8 per account, 50 per address, 15 minutes);
past it they answer `settings_error` with "Too many failed attempts" and do not
check the code at all.

### Messaging

| Event | In | Out |
| --- | --- | --- |
| `select_chat` | `recipient_id`, `send_read_receipts` | `chat_history` |
| `send_message` | `recipient_id`, `message`, `encrypted`, `iv`, `v`, `msg_uuid`, `reply_to` | `new_message`, `message_error` |
| `send_voice` | `recipient_id`, `audio`, `mime`, `encrypted`, `iv`, `v`, `msg_uuid`, `reply_to` | `new_message`, `message_error` |
| `send_share` | `recipient_id`, `share1_png`, `envelope`, `iv`, `encrypted`, `v`, `msg_uuid`, `reply_to` | `new_message`, `image_error` |
| `send_image` | `recipient_id`, `image` | `image_error` (retired; see below) |
| `forward_message` | `message_id`, `recipient_id` | `new_message`, `message_error` |
| `react_message` | `message_id`, `emoji` | `message_reactions` |
| `mark_read` | `sender_id` | `messages_read` |
| `typing` | `recipient_id`, `is_typing` | `typing` |
| `delete_message` | `message_id` | `message_deleted` |
| `revoke_share` | `message_id` | `share_revoked` |

Sends are rate limited per user: `send_message` and `group_send` share a
bucket of 30 that refills at one a second, `send_voice` has 10 refilling every
6 seconds, `send_share` 5 refilling every 15 seconds. An empty bucket answers
`message_error` / `image_error` / `group_error` with "You are sending too
fast".

A plaintext `message` may be up to 2,000 characters. An encrypted one may be
up to 10,688 base64 characters: the ciphertext of 2,000 four-byte UTF-8
characters plus the tag, so nothing the composer accepts is refused once
encrypted. (It was 4,000, which refused long messages in Urdu or with emoji.)

`encrypted` + `iv` mean the body is AES-GCM ciphertext the server cannot read.
An encrypted send must also carry `v: 2` and `msg_uuid`, a lowercase RFC 4122
UUID. Both are stored and handed back in `extra` unchanged, because the
recipient authenticates the ciphertext against
`v2|<sender_id>|<recipient_id>|<msg_uuid>`. The server answers
`message_error` for a missing or malformed envelope, for `v` other than 2
("Unsupported encryption version"), and for a `msg_uuid` already used in the
conversation in either direction ("This message was already delivered"). A
UNIQUE index enforces the last one, so two simultaneous sends cannot both be
stored. Uniqueness lasts as long as the row: one hour.
With `encrypted` false the server stores plaintext — kept so the project can
demonstrate the difference side by side.

`send_share` carries an image the sender's browser has already split
(`vc-client.js`). `share1_png` is Share 1, base64 of an 8-bit single-channel
PNG between 8 and 1600 px a side and at most 3 MB; the server stores those
bytes unchanged, records its HMAC and runs the detector on it, and refuses one
whose histogram is far from uniform (it is not a share). `envelope` is the E2EE
v2 ciphertext of `{share2_png_b64, share1_sha256, width, height}`, at most
4.5 million characters, with the same `iv` / `v` / `msg_uuid` rules as a text
message. It is stored as the message `content`, so the recipient does not have
to be online. A payload that also carries `image` or any plaintext Share 2
field is refused, not stripped.

`send_image`, which uploaded the image itself for the server to split, is
retired. It answers `image_error` ("split in your browser, reload") and decodes
nothing.

`react_message` with an empty emoji clears the reaction. `delete_message` scope
depends on who asks — a sender deletes for everyone and Share 1 leaves the disk;
a recipient hides only their own copy, and the sender is not told, because that
would leak that it was read.

### Leaving

| Event | In | Out |
| --- | --- | --- |
| `delete_account` | `password` *or* `confirm_username`, plus `totp` when enabled | `logout_success`, `settings_error` |

Irreversible, so it **re-authenticates**: the password where the account has
one, a current code where two-factor is on, and — for a Firebase account,
which has no local password — the account's own username typed back. It also
draws on the same sign-in throttle as `login`, so a wrong password here and a
wrong password at sign-in count toward one limit. It also
refuses when the caller is the last active administrator, because an
unadministered server cannot approve a registration or reinstate anyone
without being restarted with `REVEALX_ADMIN_USER` set.

Like `admin_delete_account` it purges messages and stored shares, writes an
audit line, and signs out every session the account holds — not just the one
that asked.

### Groups

**Off by default, and there is no client for them.** Group conversations exist
only on the server: no page in the app creates, opens or sends to a group.
Unless `REVEALX_GROUPS=true` is set, every event below is refused with
`group_error` "Group conversations are not enabled on this server" before it
reads or writes anything. The handlers stay registered either way, so the
event count above does not change with the flag.

| Event | In | Out |
| --- | --- | --- |
| `group_create` | `name`, `description`, `member_ids` | `group_created`, `group_updated` to each member |
| `group_list` | — | `group_list` |
| `group_open` | `group_id` | `group_opened` with the group, its members and this caller's copy of the transcript |
| `group_send` | `group_id`, `payloads` (member id → `{content, iv}`), `encrypted`, `reply_to` | `new_group_message` to each member |
| `group_add_members` | `group_id`, `user_ids` | `group_ok`, `group_notice`, `group_updated` |
| `group_remove_member` | `group_id`, `user_id` | `group_ok`, `group_removed` to the target |
| `group_set_role` | `group_id`, `user_id`, `role` (`member` or `admin`) | `group_ok`, `group_updated` |
| `group_update_info` | `group_id`, `name`, `description` | `group_ok`, `group_updated` |
| `group_update_settings` | `group_id`, `only_admins_post`, `only_admins_edit_info` | `group_ok`, `group_updated` |
| `group_leave` | `group_id` | `group_ok`, `group_removed` to the caller |

**Ranks are per group and separate from the server's.** A group has one
`owner`, any number of `admin`s and the rest `member`s, and the rule is the
same as the server-wide one: an account may act only on one *strictly below*
its own rank, and never on itself. So an admin cannot remove or demote a peer
admin or the owner, and the owner cannot be demoted at all. Being a server
administrator confers nothing inside a group you are not a member of.

**A group message is fanned out, one row per member.** Reveal-X encrypts
pairwise — ECDH between two people — so there is no group key. The sender
encrypts one copy per member under that member's own key and sends them all in
`payloads`; the server writes a row per *current* member and silently drops any
id that is not one, so a crafted payload map cannot deliver to an outsider.
Removal therefore needs no revocation: a removed member simply stops being a
recipient.

**Images are refused in groups.** Image shares are one-to-one only: Share 1
carries one capability token and one-time access, for one recipient.

**Membership notices are live only.** `group_notice` announces joins, removals
and role changes to whoever is connected; it is deliberately not stored,
because the message table otherwise holds only ciphertext the server cannot
read, and writing "X removed Y" into it in plaintext would put group metadata
in the one place this project keeps unreadable.

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
`force_signed_out`, `auth_success`, `auth_required`, and the error channels `auth_error`,
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
