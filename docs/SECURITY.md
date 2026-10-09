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

Closing this gap properly would mean splitting the image in the browser and
uploading only Share 1 — a real design change, recorded here as a known
limitation rather than glossed over.

---

## Controls

### Authentication

| Control | Where | Held by |
| --- | --- | --- |
| Argon2id password hashing | `models.py`, `PasswordHasher` | `test_auth.py` |
| Login throttling: 8 per account, 50 per address, 15-minute window | `routes.py`, `_login_blocked` | `test_rate_limit.py` |
| Only *wrong passwords* count toward the throttle | `routes.py`, `handle_login` | `test_rate_limit.py` |
| TOTP second factor (RFC 6238) | `auth/totp.py` | `test_auth.py` |
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
- Compared with `hmac.compare_digest`, not `==`.
- **One-time access.** Share 1 is served only against an unguessable
  `secrets.token_urlsafe(32)` capability, and fetching it burns the access
  (`routes.py`, `get_share1`).
- **ML tamper detection** as a second, independent signal: a RandomForest over
  13 statistical features, runnable on the server and in the browser.

HMAC and ML are deliberately redundant. HMAC is exact and catches any change to
the bytes; it cannot say *where* or *what*. The classifier is approximate but
localises damage and survives a re-encode. Either alone would be weaker.

### Transport and browser

- CSP with `object-src 'none'`, `base-uri 'self'`, and an explicit allowlist per
  directive (`templates/index.html`).
- `HttpOnly`, `SameSite=Lax`, and `Secure` when `REVEAL_X_COOKIE_SECURE=true`.
- HSTS when the proxy reports HTTPS; optional HTTP→HTTPS redirect.
- Socket.IO origins default to same-origin only.
- All interpolated text goes through `escapeHtml`, which fails **closed** when
  DOMPurify is unavailable (`sanitize.js`, `tests/js/sanitize.test.mjs`).

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

**Passwords typed into `prompt()`.** Four flows — admin password reset, TOTP
disable, passcode set and unlock — collect secrets through a browser dialog that
shows the typed value in cleartext and cannot mask it. Known; not yet fixed.

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
