# Threat model

What this system defends, against whom, and — the part that makes a threat model
worth reading — what it does **not** defend.

Every control below names the file that implements it and the test that holds it
in place. Anything stated without one of those is a claim, not a control.

---

## Assets

| Asset | Where it lives |
| --- | --- |
| Message plaintext | The two participants' browsers only |
| A shared image | The sender's browser, the server briefly, the recipient's browser |
| Share 1 | `app/shares/` on the server, and the recipient's browser once fetched |
| Share 2 | Delivered live to the recipient; **never written down** |
| Account passwords | Argon2 hashes in the database |
| TOTP secrets | The database |
| ECDH private keys | Non-extractable `CryptoKey` in each browser's IndexedDB |

## Adversaries

1. **A passive network observer** — reads traffic between browser and server.
2. **An active network attacker** — modifies traffic in flight.
3. **Another signed-in user** — holds a valid account and tries to reach data
   that is not theirs.
4. **An unauthenticated attacker** — can reach the server over HTTP.
5. **Someone who obtains a stored Share 1** — a backup, a stolen disk, a
   misconfigured directory listing.
6. **A curious or compromised server operator.**

---

## The boundary that matters most

**Text and voice messages are end-to-end encrypted. Shared images are not.**

This distinction is easy to miss and an examiner is entitled to ask about it.

Messages are encrypted in the browser with a key derived from ECDH, so the
server stores ciphertext it cannot read. Images are different: the browser
uploads the image, and **the server decodes it and performs the visual
cryptography split itself** (`routes.py`, `handle_image` →
`parse_image_data_url`). For that moment the plaintext image is in server
memory.

So the guarantee for images is not confidentiality from the server. It is this:

> **Nothing persisted on the server is enough to reconstruct the image.**

Share 1 is written to disk; Share 2 is sent to the recipient live and never
stored. Share 1 alone is an `os.urandom` one-time pad — statistically
independent of the original. An attacker with the entire server filesystem and
the entire database has random noise.

`FORBIDDEN_EXTRA_KEYS = {'share2', 'share2_b64', 'share2_filename',
'share2_live'}` exists to keep it that way: it is a hard refusal in the storage
layer, so a future change cannot casually start persisting Share 2.

The browser follows the same rule. Share 2 is held in an in-memory `Map` in
`chat.js`, emptied on logout, forced sign-out, **Clear cache**, delete and
revoke. It used to be copied into `sessionStorage`, which any script on the
origin can read and which outlives logout while the tab stays open. The
consequence is stated plainly: reloading the page loses Share 2, so a recipient
who reloads before opening a share can no longer reconstruct it.

Closing this gap properly would mean splitting the image in the browser and
uploading only Share 1 — a real design change, recorded here as a known
limitation rather than glossed over.

---

## Controls

### Authentication

| Control | Where | Held by |
| --- | --- | --- |
| Argon2id password hashing | `models.py`, `PasswordHasher` | `test_auth.py` |
| Login throttling: 8 per account, 50 per address, 15-minute window; each attempt reserved under a lock *before* verification, so parallel guesses cannot outrun it | `routes.py`, `_reserve_login_attempt` | `test_rate_limit.py` |
| Only *wrong passwords* count toward the throttle | `routes.py`, `handle_login` | `test_rate_limit.py` |
| Unknown usernames and password-less (Google-only) accounts are rejected after an Argon2 verify against a dummy hash, so response time does not reveal which accounts exist (it was ~0.3 ms against ~50 ms) | `models.py`, `authenticate` | `test_auth.py` |
| TOTP second factor (RFC 6238) | `auth/totp.py` | `test_auth.py` |
| Secrets collected in a masked in-app dialog, never `window.prompt()` | `chat.js`, `askSecret` | `test_secret_prompts.py`, `tests/js/secret-prompt.test.mjs` |
| Wrong TOTP codes counted against the **account and address**, not the socket | `routes.py`, `handle_verify_totp` | `test_auth.py` |
| Re-authentication (`totp_disable`, `totp_confirm_enrol`, `delete_account`) draws on the **same** sign-in buckets; past the limit a correct secret is refused until the window passes | `routes.py`, `_reserve_reauthentication` | `test_auth.py` |
| Account requests capped at 5 per address per 10 minutes, checked **before** the hash | `routes.py`, `_register_rate_limited` | `test_auth.py` |
| Firebase ID tokens verified by the Admin SDK | `auth/firebase_auth.py` | `test_auth.py` |
| Firebase accounts keyed on **UID only, never email** | `claims_to_identity` | `test_firebase_account_never_matched_on_email_alone` |
| Clock-drift tolerance on token timestamps | `_clock_skew_seconds` | `test_auth.py` |

Two of these are worth their reasoning.

**"Awaiting approval" does not count as a failed login.** It is returned *after*
the password already verified, so it is not a guess. Counting it meant someone
retrying while they waited got locked out the moment an admin approved them.

**Firebase identity is the UID, never the email.** Matching on email would let
anyone who signs up with an unverified address matching an existing account take
it over.

**The second factor is throttled durably.** The five-attempt limit was once
counted against `request.sid`, so disconnecting bought five fresh guesses for
ever, and a correct password cleared the buckets before the code was even
asked for. Six digits with a one-step drift tolerance leaves roughly three
valid codes at any instant - about 333,000 expected guesses, which at that
rate is a couple of hours for anyone already holding the password. Wrong
codes are now recorded against the account and the address, the throttle is
consulted before each guess, and the password path no longer clears it.

**Reconnecting does not mean signing in again, and a session cannot outlive
its account.** A dropped socket comes back as a new one with no session, so
sign-in issues a resume token: `secrets.token_urlsafe(32)`, returned once and
held in page memory, stored server-side only as SHA-256 with the account's
`session_epoch` and a 12-hour expiry (`issue_resume_token` /
`redeem_resume_token` in `models.py`). Resuming skips the second factor for
those 12 hours, which is the point and also the cost: whoever holds the token
within that window is the user, so it is never written to web storage, and
`bump_session_epoch` ends every outstanding token on logout, password reset,
role change, disable and deletion. Handlers without a session now answer
`auth_required` instead of returning silently (`session_required`,
`tests/test_resume.py`, which enumerates the server's handler table so a new
handler cannot be missed).

**Registration is rate limited because it is expensive.** `register` is
unauthenticated and the first thing it did was an Argon2id hash - 64 MiB and a
measured 55 ms. On a single worker that is a denial of service in a handful of
requests a second. The cap is evaluated before `create_user`, not after, which
is the entire point of having it.

### Authorisation

- Roles are ranked `user < admin < superadmin`, and an action requires a
  **strictly greater** rank than its target — so admins cannot act on each
  other, and nobody can act on themselves (`models.py`, `can_manage`).
- Every message read goes through `get_message_for_user`, which filters on
  `sender_id OR recipient_id`. Knowing a message id is not access.
- Revoking, deleting and reading are each tested from the perspective of an
  uninvolved third user (`test_message_lifecycle.py`).

### Share integrity

- **HMAC-SHA256** over the bytes **as written to disk**, not the array in
  memory — so the tag covers what a later read actually returns.
- **Keyed separately from the session.** The tag uses
  `REVEALX_SHARE_HMAC_KEY`, or a one-way `sha256("revealx/share-hmac/v1|" +
  SECRET_KEY)` when that is unset. It was previously keyed with the session
  signing key, so leaking one secret granted both the ability to forge a
  session and to re-tag an altered share so it verified clean (CWE-323).
- Compared with `hmac.compare_digest`, not `==`.
- **One-time access.** Share 1 is served only against an unguessable
  `secrets.token_urlsafe(32)` capability, and fetching it burns the access
  (`routes.py`, `get_share1`). The burn is a conditional `UPDATE ... WHERE
  share1_accessed` is unset, and only the request whose UPDATE changed the row
  is served. It was a SELECT followed by an UPDATE, which let eight concurrent
  requests all receive the same one-time share.
- **ML tamper detection** as a second, independent signal: a RandomForest over
  13 statistical features, runnable on the server and in the browser.

HMAC and ML are deliberately redundant. HMAC is exact and catches any change to
the bytes; it cannot say *where* or *what*. The classifier is approximate but
localises damage and survives a re-encode. Either alone would be weaker.

### Transport and browser

- CSP sent as a response header (`routes.py`, `add_security_headers`), with
  `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`, no
  `'unsafe-inline'` for scripts, and no CDN at all: socket.io, DOMPurify, Font
  Awesome and Inter are vendored, so the only third-party origins left are
  Google's, for Firebase sign-in. Inline *styles* are still allowed. A second
  copy of the policy used to sit in a `<meta>` tag in `index.html`, listing
  `'unsafe-inline'` scripts and three CDNs; both were enforced, so it changed
  nothing but misled anyone reading it, and it has been removed.
- `HttpOnly`, `SameSite=Lax`, and `Secure` when `REVEAL_X_COOKIE_SECURE=true`.
- HSTS on HTTPS requests, and an optional HTTP→HTTPS redirect that runs before the request is handled. `X-Forwarded-Proto` is believed only with `REVEALX_TRUST_PROXY`; it used to be read from any client.
- `Permissions-Policy: camera=(), microphone=(self), geolocation=()`. It was
  `microphone=()`, which disables the microphone for the app's own origin as
  well, so voice notes could not be recorded in browsers that enforce it.
- Socket.IO origins default to same-origin only.
- All interpolated text goes through `escapeHtml`, which fails **closed** when
  DOMPurify is unavailable (`sanitize.js`, `tests/js/sanitize.test.mjs`).
- **Nothing the app cannot start without is loaded from another origin.**
  socket.io and DOMPurify are vendored under `app/static/vendor/`. Both used
  to be blocking CDN scripts, and `chat.js` opens with `const socket = io()`
  with no fallback: a slow or blocked CDN left `io` undefined, threw at the
  top of the file, and left a page that rendered and responded to nothing.
  The DOMPurify URL was additionally wrong and had been returning 404 since
  it was written, so the client-side sanitiser was never once loaded in
  production; `cleanHTML()` fails closed, which is why nothing looked broken.
  `tests/test_startup_path.py` fails the build if a third-party script or
  stylesheet reappears in any template. Fonts used to come from Google Fonts
  and cdnjs, which told both who opened the app and when.

### Data lifetime

- Messages and shares expire after `MESSAGE_TTL_HOURS = 1`.
- A sender's delete removes the row *and* Share 1 from disk; a recipient's
  delete hides only their copy.
- Deleting an account removes its messages without touching anyone else's.

---

## What this does not defend against

Stated plainly, because a threat model that only lists wins is marketing.

**A compromised endpoint.** E2EE protects data in transit and at rest on the
server. Malware or a malicious extension in either participant's browser reads
the plaintext before encryption and after decryption. Nothing here changes that.

**A malicious server, for images.** See the boundary section above — the server
sees uploaded images in plaintext at split time. Text and voice are safe from
it; images are not.

**Metadata.** Who talks to whom, how often, when, and how large each message is
are all visible to the server and to anyone watching the network. Message
*contents* are encrypted; the social graph is not.

**Replay is detected, not prevented, against a malicious server.** Since
protocol v2 a ciphertext is bound to its sender, recipient and `msg_uuid`, and
each direction has its own key, so the server can no longer reflect a message
back to its author or move it to another conversation. It can still store a
second copy of a ciphertext under a new row id; the client recognises the
repeated `msg_uuid` among the messages it has loaded and shows a notice in its
place. A replay of a message that has already expired, or that the client has
not loaded in the same tab, is not caught. v1 had none of this: one key for
both directions and no associated data.

**Plaintext is never a fallback.** Both send paths used to catch an encryption
error, log it, and send the message unencrypted. A failure now stops the send;
a peer with no key, or a browser without Web Crypto, needs the user's explicit
consent for that conversation before anything is sent in the clear
(`send-policy.js`, `tests/js/send-policy.test.mjs`,
`tests/test_send_policy_wiring.py`). Consent lasts for the tab, so a reload
asks again. It cannot stop the user agreeing; it stops it happening unseen.

**Key substitution is caught after first contact, not before it.** The server
distributes public keys, so it could hand Alice a key it holds instead of
Bob's. Each browser pins the first key it sees per contact and blocks sending
when a different one appears, until the user accepts it in the safety-number
dialog (`checkPeerKey` / `acceptPeerKey` in `e2ee.js`,
`tests/js/e2ee.test.mjs`, `tests/test_key_pinning.py`). This is trust on first
use: a key swapped *before* the first contact is pinned as genuine, and only
comparing the 60-digit safety number out of band (then ticking **Mark as
verified**) detects that. Incoming messages are still shown while a change is
pending review; only sending is blocked. Pins live in the browser, so a new
browser or cleared site data starts from first use again.

**No forward secrecy.** ECDH key pairs are long-lived. Compromising one
participant's private key allows decryption of every past message in that
conversation that an attacker captured. A ratchet (Double Ratchet or similar)
would fix this and is not implemented.

**Unauthenticated compute, now capped.** `/api/lab/process` and
`/api/lab/features` still take an 8 MB image with no authentication, because
the demonstration has to run without an account. They are no longer unlimited:
both share one budget of 20 requests per address per minute
(`_lab_rate_limited`, held by `test_rate_limit.py`), and a request over the
budget gets a 429 rather than a core.

What that is and is not: a ceiling on abuse, not an authentication control. The
counter is in-process, so it resets on restart and does not coordinate across
workers — the deployment runs a single worker because Socket.IO keeps sessions
in process, so here that covers it. Behind a proxy it keys on whatever
`_client_address()` resolves to, so a misconfigured proxy would put every
visitor in one bucket. An attacker with many addresses is unaffected.

**Database at rest.** SQLite is a file with no encryption at rest. Anyone with
filesystem access reads the hashes, the TOTP secrets and the message ciphertext.
Password hashes are Argon2 and messages are E2EE, so this is less bad than it
sounds — but TOTP secrets are stored recoverable.

**Denial of service generally.** There is no global rate limit, no request size
throttling beyond `MAX_CONTENT_LENGTH = 8 MB`, and one worker process.

**A malicious administrator.** An admin can reset passwords and disable
accounts. They cannot read messages — those are E2EE and the keys never leave
the participants' browsers — but they can take over an account and receive
*future* messages. Admin actions are audited; the audit log does not prevent
them.

---

## Reporting

This is a final-year university project, not a maintained product. If you find
something, open an issue on the repository. There is no bounty and no SLA.
