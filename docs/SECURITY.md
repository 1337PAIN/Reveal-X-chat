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
| A shared image | The sender's and the recipient's browsers only |
| Share 1 | `app/shares/` on the server, and the recipient's browser once fetched |
| Share 2 | Inside an E2EE envelope stored as the message body; plaintext only in the two browsers' memory |
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

**Text, voice and images are all end-to-end encrypted from the server. The
`/lab` dashboard is not part of the chat and is the one place the server still
sees an image.**

Messages are encrypted in the browser with a key derived from ECDH, so the
server stores ciphertext it cannot read. Images used to be the exception: the
browser uploaded the picture and the server decoded it and performed the
visual cryptography split itself, so for that moment the plaintext image was
in server memory, and Share 2 could only be handed over live to an online
recipient. That path (`send_image`) is retired; the event now only tells a
stale page to reload, without decoding anything.

The split now happens in the sender's browser (`vc-client.js`, `send_share`):

1. The image is converted to grey with the same integer BT.601 weights the
   in-browser detector uses.
2. **Share 1** is `crypto.getRandomValues` bytes; **Share 2** is image XOR
   Share 1. Both are written as 8-bit single-channel PNGs.
3. Only Share 1 is uploaded. On its own it is a one-time pad, statistically
   independent of the image. The server stores it byte for byte, records its
   HMAC and runs the tamper detector on it, as it always has.
4. Share 2 travels inside an E2EE v3 message whose plaintext is
   `{share2_png_b64, share1_sha256, width, height}`. The server stores that
   ciphertext as the message body and cannot open it, so the recipient no
   longer has to be online.
5. The recipient downloads Share 1 through its one-time capability, hashes the
   bytes **before decoding them**, and compares the hash with the one sealed in
   the envelope, then XORs the two shares exactly.

So the guarantee is now confidentiality from the server, not only that nothing
persisted can reconstruct the image: the server holds random noise and a
ciphertext. The four checks the recipient sees answer different questions:

| Check | Computed by | Catches |
| --- | --- | --- |
| HMAC-SHA256 | Server, keyed | Share 1 changed on disk since upload |
| Server ML | Server | Share 1 *looks* edited (localises damage) |
| In-browser ML | Recipient | The bytes that arrived look edited |
| End-to-end SHA-256 | Sender, checked by recipient | Share 1 is not the one the sender made — even if the server changed it *and* its own HMAC record |

Only the last does not trust the server. It cannot say *where* a change is;
the detectors can.

The server still refuses anything that is not plausibly a share: not a PNG,
more than one channel, outside 8–1600 px a side, over 3 MB, or a histogram far
from uniform (a Pearson chi-square ten standard deviations out). That last
check catches a client uploading the picture by mistake. It is not a defence
against a sender who wants to leak their own image — nothing could be — and a
histogram-equalised picture would pass it. A payload that carries the image or
any plaintext Share 2 field is refused outright, and
`FORBIDDEN_EXTRA_KEYS = {'share2', 'share2_b64', 'share2_filename',
'share2_live'}` remains a hard refusal in the storage layer.

In the browser, a decrypted Share 2 is held in an in-memory `Map` in `chat/core.js`,
emptied on logout, forced sign-out, **Clear cache**, delete and revoke; it is
never written to web storage. Because the envelope is stored, a reload no
longer loses it: the recipient decrypts it again.

Two limits, stated plainly. Images are greyscale: colour images are converted
before the split, as they were before, and a per-channel colour split is not
implemented. And the `/lab` dashboard, which demonstrates the split, attacks
and metrics step by step, still uploads its image to the server; it is a
teaching tool with no recipient, and nothing it processes is stored.

---

## Controls

### Authentication

| Control | Where | Held by |
| --- | --- | --- |
| Argon2id password hashing | `models.py`, `PasswordHasher` | `test_auth.py` |
| Login throttling: 8 per account, 50 per address, 15-minute window; each attempt reserved under a lock *before* verification, so parallel guesses cannot outrun it | `handlers/common.py`, `_reserve_login_attempt` | `test_rate_limit.py` |
| Only *wrong passwords* count toward the throttle | `handlers/auth.py`, `handle_login` | `test_rate_limit.py` |
| Unknown usernames and password-less (Google-only) accounts are rejected after an Argon2 verify against a dummy hash, so response time does not reveal which accounts exist (it was ~0.3 ms against ~50 ms) | `models.py`, `authenticate` | `test_auth.py` |
| TOTP second factor (RFC 6238) | `auth/totp.py` | `test_auth.py` |
| Secrets collected in a masked in-app dialog, never `window.prompt()` | `chat/settings.js`, `askSecret` | `test_secret_prompts.py`, `tests/js/secret-prompt.test.mjs` |
| Wrong TOTP codes counted against the **account and address**, not the socket | `handlers/auth.py`, `handle_verify_totp` | `test_auth.py` |
| Re-authentication (`totp_disable`, `totp_confirm_enrol`, `delete_account`) draws on the **same** sign-in buckets; past the limit a correct secret is refused until the window passes | `handlers/common.py`, `_reserve_reauthentication` | `test_auth.py` |
| Account requests capped at 5 per address per 10 minutes, checked **before** the hash | `handlers/common.py`, `_register_rate_limited` | `test_auth.py` |
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
- Messages, voice notes, image shares, forwards, typing notices and group
  invitations reach **active** accounts only (`is_active_user`, checked in the
  handlers and again in `add_message`). Pending and disabled accounts were
  hidden from the roster but could still be written to by anyone holding the
  id. A member disabled after joining a group stays a member and gets nothing
  until reinstated. An inactive id is refused exactly as an unknown one is
  (`test_active_targets.py`).
- **Group conversations are off by default.** They have no client -- no page
  in the app uses them -- so in a default deployment every `group_*` event is
  refused before it touches the database (`handlers/groups.py`,
  `groups_required`; `tests/test_groups_flag.py`). `REVEALX_GROUPS=true`
  turns them on; the rules above then apply to them.
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
  (`handlers/http.py`, `get_share1`). The burn is a conditional `UPDATE ... WHERE
  share1_accessed` is unset, and only the request whose UPDATE changed the row
  is served. It was a SELECT followed by an UPDATE, which let eight concurrent
  requests all receive the same one-time share.
- **ML tamper detection** as a second, independent signal: a RandomForest over
  13 statistical features, runnable on the server and in the browser.

HMAC and ML are deliberately redundant. HMAC is exact and catches any change to
the bytes; it cannot say *where* or *what*. The classifier is approximate but
localises damage and survives a re-encode. Either alone would be weaker.

### Transport and browser

- CSP sent as a response header (`handlers/http.py`, `add_security_headers`), with
  `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`, no
  `'unsafe-inline'` for scripts, and no CDN at all: socket.io, DOMPurify and
  Inter are vendored (Font Awesome has been dropped), so the only third-party
  origins left are Google's, for Firebase sign-in, loaded only when Firebase
  is configured and chosen. `tests/test_startup_path.py` holds that line:
  no element in a template, no `url()` or `@import` in any stylesheet, and no
  script other than the Firebase loader names another origin. Inline *styles* are still
  allowed. The app's own pages no longer use any -- no `style` attributes or
  `<style>` blocks in the templates or in markup built by script
  (`tests/test_styles_in_stylesheets.py`) -- but Google's sign-in scripts
  are not ours, and removing `'unsafe-inline'` for styles waits until Google
  sign-in has been tested with it gone. A second
  copy of the policy used to sit in a `<meta>` tag in `index.html`, listing
  `'unsafe-inline'` scripts and three CDNs; both were enforced, so it changed
  nothing but misled anyone reading it, and it has been removed.
- `HttpOnly`, `SameSite=Lax`, and `Secure` when `REVEAL_X_COOKIE_SECURE=true`.
- HSTS on HTTPS requests, and an optional HTTP→HTTPS redirect that runs before the request is handled. `X-Forwarded-Proto` is believed only with `REVEALX_TRUST_PROXY`; it used to be read from any client.
- `Permissions-Policy: camera=(), microphone=(self), geolocation=()`. It was
  `microphone=()`, which disables the microphone for the app's own origin as
  well, so voice notes could not be recorded in browsers that enforce it.
- Profile photos are re-encoded by the server: only a strict
  `data:image/(png|jpeg|webp);base64,` value is accepted, decoded with Pillow
  and stored as a fresh PNG, and the client builds the `<img>` with
  `createElement` and `.src`. The old check read only the MIME type, so a
  quote in the data-URL header was stored and then broke out of an
  `innerHTML` attribute in every contact's sidebar
  (`handlers/settings.py`, `_normalise_profile_image`;
  `tests/test_profile_image.py`). The CSP adds `form-action 'self'`, so
  injected markup cannot post a form elsewhere either.
- Socket.IO origins default to same-origin only.
- All interpolated text goes through `escapeHtml`, which fails **closed** when
  DOMPurify is unavailable (`sanitize.js`, `tests/js/sanitize.test.mjs`).
- **Nothing the app cannot start without is loaded from another origin.**
  socket.io and DOMPurify are vendored under `app/static/vendor/`. Both used
  to be blocking CDN scripts, and the chat client (now `chat/core.js`) opens with `const socket = io()`
  with no fallback: a slow or blocked CDN left `io` undefined, threw at the
  top of the file, and left a page that rendered and responded to nothing.
  The DOMPurify URL was additionally wrong and had been returning 404 since
  it was written, so the client-side sanitiser was never once loaded in
  production; `cleanHTML()` fails closed, which is why nothing looked broken.
  `tests/test_startup_path.py` fails the build if a third-party script or
  stylesheet reappears in any template. Fonts used to come from Google Fonts
  and cdnjs, which told both who opened the app and when.
- **Every vendored file is a recorded release.** `app/static/vendor/vendor.json`
  lists each library's npm name, version, source tarball and the sha256 of
  every file; `tests/test_vendored_assets.py` checks the hashes and that the
  version banner in each file matches, and `.gitattributes` keeps `vendor/`
  byte-exact so a Windows checkout hashes the same as CI.
  `scripts/check_vendored_advisories.py` asks OSV about each release and fails
  on any advisory; CI runs it on every push and weekly. DOMPurify was 3.0.6,
  against which OSV lists twenty-three advisories, and is now 3.4.18; in a
  real browser (`tests/e2e/test_accessibility.py`, run in CI) the library strips `onerror`, `javascript:` URLs, script
  and iframe tags and the known mutation-XSS shapes, and leaves plain
  formatting alone. `cleanHTML()` currently has no caller in the app; the
  one place DOMPurify runs is on an already-escaped username in
  `chat/render.js`, so it is a second layer there, not the first.

### Data lifetime

- Messages and shares expire after `MESSAGE_TTL_HOURS = 1`. A background
  sweeper removes expired messages, their Share 1 files and expired resume
  tokens every 60 seconds (`app/sweeper.py`, `tests/test_sweeper.py`).
  Before it, expiry ran only when someone sent a message or opened a
  conversation, so an idle server kept expired data indefinitely.
- A sender's delete removes the row *and* Share 1 from disk; a recipient's
  delete hides only their copy.
- Deleting an account removes its messages without touching anyone else's.

---

## What this does not defend against

Stated plainly, because a threat model that only lists wins is marketing.

**A compromised endpoint.** E2EE protects data in transit and at rest on the
server. Malware or a malicious extension in either participant's browser reads
the plaintext before encryption and after decryption. Nothing here changes that.

**A malicious server, for images, partly.** The server no longer sees images
(see the boundary section). It can still withhold or delete Share 1, or serve a
different one; the end-to-end hash turns the last into a visible mismatch, not
a silent wrong picture. It sees each share's size and dimensions, which give
away the image's.

**Metadata.** Who talks to whom, how often, when, and how large each message is
are all visible to the server and to anyone watching the network. Message
*contents* are encrypted; the social graph is not.

**Protocol v3: what is bound, and what freshness does and does not prove.**
Each direction of a conversation has its own key (HKDF info
`reveal-x-message-key-v3|<sender public key>|<recipient public key>`), and
AES-GCM authenticates
`v3|<conversation>|<sender_id>|<recipient_id>|<msg_uuid>|<sent_at>`, where
conversation is `dm` (`group:<id>` is reserved) and `sent_at` is the sender's
clock in integer milliseconds (`e2ee.js`). So the server cannot reflect a
message back to its author, move it to another conversation, relabel its
sender, recipient or uuid, or change its time: any of those breaks the tag.
No field may contain `|`, so two different contexts cannot encode alike.

The chat opens every encrypted row through `openMessage`, in this order:
decrypt (only `v: 3` is ever tried -- v1 and v2 decryption is removed, not
kept as a fallback, and such a row shows *Sent before an update; can't be
opened*); then **freshness** -- an authenticated `sent_at` more than 65
minutes old or more than 5 minutes ahead is shown as *Can't be verified*;
then **replay** -- the first row id seen for
`<conversation>|<sorted user ids>|<msg_uuid>` is recorded in IndexedDB until
`sent_at` + 65 minutes, and a second row id with the same key is shown as a
repeated message (`tests/js/e2ee.test.mjs`, including after a reload against
the same IndexedDB). The record is written only after the message
authenticates, so a forged row cannot poison it, and pruning an expired
entry opens no window because that message is by then refused as stale.

What freshness proves: the *sender's* browser produced the message within
that window by the sender's clock. What it does not prove: when the server
delivered it, or that the sender's clock is right. A server can still hold a
message back for up to about an hour and deliver it late, or drop it; a
sender whose clock is wrong produces messages others see as unverifiable.
Replay detection is per browser: a second browser of the same user keeps its
own record. The server also refuses a `sent_at` more than five minutes from
its own clock, but that is a sanity check on honest clients, not a defence;
the client's check of the authenticated value is the boundary.

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

**Database at rest.** The database itself is not encrypted. Anyone with a copy
reads the Argon2 hashes, the message ciphertext and the metadata. TOTP secrets
are the exception, **if `REVEALX_DATA_KEY` is set**: each is AES-GCM encrypted
under that key with the user id as associated data, existing plaintext secrets
are migrated at startup, and the app refuses to start when encrypted secrets
exist and the key is missing or wrong, rather than reading them as "2FA off"
(`seal_secret` / `open_secret` in `models.py`, `tests/test_totp_at_rest.py`).
Without the key they are stored as before, recoverably. The key lives in the
environment next to the app, so this protects copies of the database (backups,
dumps, snapshots), not a compromised server. There is no key rotation yet.

**Denial of service generally.** Sending is limited per user by token buckets
(`SEND_LIMITS` in `handlers/common.py`): text and group posts 30 at once then one a
second, voice 10 then one every 6 s, images 5 then one every 15 s, checked
before an image is decoded (`tests/test_send_limits.py`). Before this, one
signed-in socket could loop sends without limit. Beyond that there is no global
rate limit, no request size throttling beyond `MAX_CONTENT_LENGTH = 8 MB`, and
one worker process; like the other limits, the buckets are in-process.

**A malicious administrator.** An admin can reset passwords and disable
accounts. They cannot read messages — those are E2EE and the keys never leave
the participants' browsers — but they can take over an account and receive
*future* messages. Admin actions are audited; the audit log does not prevent
them.

---

## Reporting

This is a final-year university project, not a maintained product. If you find
something, open an issue on the repository. There is no bounty and no SLA.
