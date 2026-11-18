# Reveal-X Final Product

**Reveal-X** is a secure image-sharing and real-time chat application built for a Cyber Security FYP. The final version focuses on the strongest contribution of the project:

> **Visual cryptography + AI/ML tamper detection + image reconstruction/enhancement + measurable security/quality evaluation.**

Tamper detection runs **inside the live chat**, not only in the lab: every image share is verified with HMAC and classified by the ML detector at the moment the recipient receives it. The `/lab` dashboard remains available for the controlled, step-by-step evaluation demo.

---

## Milestone status

The planned milestones, and where the code actually stands. Two were descoped
deliberately; they are listed as not implemented rather than quietly reworded,
because a reader can check the repository in about a minute.

| # | Milestone | Status |
| --- | --- | --- |
| M1 | ML tamper detection — model training & evaluation | **Done** |
| M2 | ONNX in-browser tamper detection integration | **Done** |
| M3 | AI denoising CNN trained on a VC-specific dataset | **Not implemented** |
| M4 | ESRGAN upscaling + PSNR/SSIM dashboard | **Partial** — dashboard done, ESRGAN not implemented |
| M5 | Full ECDH key exchange + AES-GCM message encryption | **Done** |
| M6 | Advanced UI/UX enhancements + WCAG AAA upgrade | **Done** — AAA for contrast, audited |

**M1** — `RandomForestClassifier`, 13 statistical features, 93.33% held-out
accuracy. Trained by `scripts/train_tamper_detector.py` on a synthetic dataset
generated at runtime (seed 42, 240 samples). No external dataset is used, and
the detector has a documented blind spot on the `noise` attack — see
[Why both HMAC and ML?](FINAL_DEMO_GUIDE.md).

93.33% is the shipped model on one held-out split of 60 shares. For how far
that figure moves, [docs/EVALUATION.md](docs/EVALUATION.md) repeats stratified
5-fold cross-validation five times with fresh models (the shipped one is never
refitted): **92.42% ± 3.34** accuracy, ROC-AUC 0.9383 ± 0.0294. It misses
**random-noise injection** almost entirely (4.71% recall, near its 1.33%
false-positive rate) and, apart from that, only 2 of 85 scratch attacks; the
HMAC catches all of them. It also measures a limit of the shipped model:
trained on 96 × 96 shares only, it calls a **clean share under about 100 px
"tampered"** much of the time (64 × 64: every time), while from 128 px up no
clean share was flagged.

**M2** — the same forest, exported to ONNX and executed in the browser with
`onnxruntime-web`. Feature parity with NumPy/OpenCV is proven at `/lab/parity`
rather than assumed. See section 8b.

**M3 — not implemented.** `enhancement.py` is a classical OpenCV pipeline
(non-local means denoising + CLAHE + unsharp mask), not a neural network. There
is no PyTorch/TensorFlow dependency anywhere in the project. Training a denoising
CNN needs a GPU and a paired clean/noisy VC dataset that this project does not
have. It is also the milestone with the weakest rationale: XOR reconstruction is
**lossless**, so on an intact share there is nothing to denoise — the metrics
already report PSNR 99 dB / SSIM 1.0. Enhancement only does anything on a share
that was damaged, which is the case the system is designed to *reject* rather
than repair.

**M4 — half done.** The evaluation half is complete: `metrics.py` implements
MSE, PSNR and a dependency-free SSIM, and `/lab` reports reconstructed vs
enhanced side by side — including an explicit note when enhancement *lowers*
SSIM, rather than silently reporting the better number. The page shows metrics
to two decimals (the API keeps four), and a pair being compared gets as many
as it takes to tell them apart, so that note never reads "0.86 against 0.86". ESRGAN super-resolution
is not implemented; no upscaling of any kind happens. Same reason as M3, plus
the same objection: there is no resolution to recover on a lossless
reconstruction.

**M6 — AAA for contrast, measured.** The UI work (glassmorphism design system,
light and dark themes, `prefers-reduced-motion` / `prefers-reduced-transparency`
/ `backdrop-filter` fallbacks, responsive down to 375px, `aria-live` regions and
focus-visible indicators) is complete, and the contrast bar has now been
**audited rather than asserted**: 0 failures against WCAG 1.4.6 (AAA) across 114
text elements over chat, settings, sign-in and `/lab`, in both themes.

The audit found the light theme had **8 elements below even AA** — the selected
account row was white text at **1.30:1**, effectively invisible — so the "WCAG
2.1 AA" comment already in the CSS was not accurate when written. The cause was
structural: translucent fills have no fixed contrast, because it depends on
whatever sits behind them. The semantic buttons and the selected row now use
opaque fills.

Scope note: the claim is **contrast specifically** (1.4.3 / 1.4.6). WCAG AAA as
a whole includes criteria this project does not meet or claim, such as
sign-language alternatives for video and reading-level requirements.

Full numbers, the tool, and its known limits: [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md).

---

## Final Product Features

### 1. Real-Time Secure Chat

- User registration and login.
- Private one-to-one messaging with Flask-SocketIO.
- **Contacts, added by username.** The sidebar lists your contacts, not every account. Type someone's exact username under *Contacts* and press **Add**; they get a request to **Accept** or **Decline** (and you can **Cancel** it). Until they accept, neither of you can message the other, see the other in your list, or see whether they are online -- and the server enforces it on every send, typing notice and chat history, not only in the list. **Remove contact** in the chat header ends it for both. A name nobody has and a pending, disabled or administrator account all answer "No account with that username", and lookups are rate limited, so the field cannot be used to list accounts. People who had already chatted before this change were kept as contacts (migration 11). See [docs/API.md](docs/API.md#contacts).
- Group conversations exist on the server only, with **no client**, and are **off by default**: every `group_*` event is refused unless `REVEALX_GROUPS=true` is set (see [docs/API.md](docs/API.md#groups)). No page in the app creates or opens a group.
- **End-to-end encrypted text and voice messages** (see section 2).
- Voice notes recorded in-browser with `MediaRecorder`, capped at 60 seconds and 2 MB. Recording turns the composer into a recording bar -- discard, a pulsing dot, the clock against the one-minute cap, a live microphone level, and send -- and a voice note plays in a chat-style player: play/pause, a waveform drawn from the recording itself that fills as it plays (click or arrow keys to seek), the length (taken from the decoded audio, since a fresh WebM recording carries none), and a 1x/1.5x/2x speed button (`voice-ui.js`, [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md#voice-notes)). When the microphone cannot be opened the app says why -- no microphone connected, blocked for this site, blocked by the operating system, or in use by another app -- rather than "access denied" for all of them (`mic-errors.js`). A PC with no microphone gets no permission prompt at all, so it needs a device that has one.
- An image share is drawn as a picture message, not a box of text (`share-ui.js`): a sealed tile -- grain, since each share on its own is noise, and a lock -- with a caption saying who can rebuild it; the tamper verdict as one line with a shield, and the evidence (HMAC, both ML verdicts, the end-to-end hash, the heatmap) folded under "How it was checked", which opens by itself when a check fails. Once the recipient reconstructs the image it appears in the bubble, in that tab's memory only, framed to its own shape; Reconstruct and Re-check are then switched off, since Share 1 has been used, and the verdict it was opened on stays in place rather than the server's "already opened". The send dialog shows the picture on a dark stage with its file name, the size that will actually be split and the bytes, and says in three steps what Split & Send does.
- **A menu beside the contact's name (⋮)** holds everything you can do with the conversation: **Contact info** (photo, status, bio and the safety number), **Mute notifications**, **Pin chat** (pinned chats sit at the top, latest pin first), **Archive chat** (moved under an *Archived* row), **Lock chat**, **Clear chat** (deletes the messages for you only), **Block** and **Remove contact**. Pin, archive and mute are kept on the server per account, so they follow you to another device. **Lock chat** hides the chat behind a *Locked chats* row and a 4-8 digit chat-lock PIN (stored as an Argon2id hash, five wrong tries per 15 minutes); the server will not send a locked chat's history to a session that has not entered it, and a locked chat's notifications name nobody. A forgotten PIN can be reset from Settings → Privacy, which clears the locked chats for you. **Block** ends the contact and stops messages and requests from them without telling them; Settings → Privacy lists who you have blocked, with Unblock.
- Typing indicators, read receipts, emoji reactions, forwarding, deletion, profile image support, and responsive UI.
- **Failures are reported.** The server had always emitted `image_error` and `message_error`; nothing on the client listened, so a refused send -- recipient offline, message too long, unsupported image type -- looked exactly like a successful one. They now surface in a banner that is also announced to screen readers.
- Messages and shares auto-delete after 1 hour.
- **Deletion scope depends on who is asking.** The sender owns what they sent, so their delete removes the message for *everyone* — the row and any stored Share 1 are destroyed on both sides. The recipient can only delete their own copy; the sender keeps theirs. The delete control is labelled accordingly (`Delete for everyone` vs `Delete for me`). A recipient who deletes a share also gives up their access to it.
- XSS protection: message bodies are rendered with `textContent`, so they can never introduce markup. DOMPurify and server-side escaping cover the remaining surfaces. DOMPurify is **vendored locally**, not loaded from a CDN -- its CDN URL was wrong and had been 404ing, so until 6 Nov 2026 the client-side sanitiser had never actually loaded and `cleanHTML()` had been failing closed to full escaping the whole time. It is DOMPurify 3.4.18 (3.0.6 had twenty-three published advisories); `app/static/vendor/vendor.json` records every vendored release with its hashes, and CI asks OSV about each one on every push and weekly (`scripts/check_vendored_advisories.py`).
- Password hashing with Argon2id when available, otherwise Werkzeug secure hashing.
- PWA manifest and service worker support.

### 2. Sign-in: Google, email, and two-factor

Three ways in, and Firebase is optional:

| Method | Needs Firebase | Works offline |
| --- | --- | --- |
| Username + password (built in) | No | Yes |
| Google | Yes | No |
| Email + password via Firebase | Yes | No |
| Two-factor (TOTP) on top of any of the above | No | Yes |

With no Firebase config the app behaves exactly as before: the Google block stays hidden, the SDK is never downloaded, and the built-in login is used.

**Firebase.** The browser signs in with Firebase and hands the resulting ID token to the server, which verifies it with the Firebase Admin SDK — signature, issuer, audience, expiry and revocation — before trusting any identity. Nothing the client claims about itself is believed. Accounts are keyed on the **Firebase UID only, never on email address**, so nobody can take over an existing account by signing up with a matching address.

**Two-factor (TOTP).** RFC 6238, 6 digits, 30-second step, ±1 step for clock drift. It sits *after* whichever first factor was used, so a stolen password or a live Google session is not enough on its own: the socket gets **no session at all** until a valid code arrives. A correct code is single-use, so an observed code cannot be replayed, and five wrong codes abandon the sign-in. The secret is only written to the account after a live code proves the authenticator has it, and disabling requires a current code.

TOTP is implemented in-app rather than with Firebase MFA, which would require upgrading to Identity Platform on the Blaze plan. It therefore works on the free tier and with no internet.

Setup instructions: [docs/FIREBASE_SETUP.md](docs/FIREBASE_SETUP.md).

### 2b. Closed registration and administration

Registration is **request-and-approve**: anyone can ask for an account, nobody
can sign in until an administrator approves it. Only `active` accounts can
authenticate, and only `active` accounts appear in anyone's chat list — a
pending request is invisible to everyone but an admin.

The administrator is named by environment variables and created (or promoted) on
every startup, so a freshly reset demo database always has a working admin:

```bash
REVEALX_ADMIN_USER=root
REVEALX_ADMIN_PASSWORD=choose-something-long
```

There are three ranks — **superadmin**, **admin**, **user** — and one rule:
*an account may only act on one of strictly lower rank, and never on itself.*
That single comparison produces every behaviour that matters: an admin manages
users but cannot disable a peer, cannot promote a friend to peer rank, and
cannot reach the superadmin; and nobody can strand the deployment by demoting or
disabling their own account.

The **superadmin** is the account named by `REVEALX_ADMIN_USER`. There is
exactly one, the rank is not assignable through the panel, and it cannot be
modified by anyone — so the deployment always retains one account that can
administer it. Only the superadmin can promote or demote admins; if an ordinary
admin could promote, they could manufacture a peer and route around the rank
entirely.

Admins can approve or reject requests, create accounts directly, disable and
re-enable, reset passwords, and delete accounts along with their messages and
stored shares. Disabling and password resets **end any live session**, because
otherwise a disabled account keeps chatting on the socket it already holds and a
password reset leaves the person it was meant to lock out still signed in.

**The panel decides what to draw; it decides nothing about what is allowed.**
Every admin action passes through one server-side check that requires an
authenticated session *and* re-reads the caller's role from the database — so an
admin demoted mid-session stops being one immediately, and unhiding the Admin
button in devtools achieves nothing. Every admin event is tested from both a
normal user's socket and an anonymous one.

Three details worth knowing:

- A first-time **Google sign-in creates a pending account too**. Without that
  the gate would be decorative.
- **A username belongs to one account, whatever the capitals** ("Alice" and
  "alice" are the same name; a unique index on `LOWER(username)` enforces it),
  and sign-in accepts any capitals. The **Create account** form checks the name
  as it is typed and says "That username is already taken" or "available", and
  asks for the password twice. That answer reveals whether a name is in use --
  as adding a contact by username must anyway -- so the check is rate limited
  (30 a minute per address). Until 17 Nov 2026 a taken name got "Request
  submitted" while nothing was created, which kept names private but left
  people wondering why they never got in.
- The system refuses any operation that would leave it unadministrable: you
  cannot disable or delete your own account, and the last active admin cannot be
  removed.

Full details: [docs/ADMIN.md](docs/ADMIN.md).

### 2c. Brute-force protection and the audit trail

A dropped connection no longer strands the page: sign-in issues a 12-hour
resume token, held in memory only, that a reconnecting socket uses to get its
session back without a second TOTP prompt. Logout and any change to the account
(password reset, role, disable, deletion) end it. Anything sent without a
session is answered with `auth_required` rather than silently ignored, and the
page shows the sign-in dialog if the session cannot be resumed, with a
banner that says "Reconnecting..." while the socket is down and "Signed out -
sign in again" when it could not.

Sign-in failures are throttled **on the server**. The browser shows an attempts
counter, but that lives in the client and an attacker scripting the socket never
sees it -- before this, sixty wrong passwords in a row were accepted and the
right one still worked immediately afterwards.

Two buckets, because either alone is easy to sidestep:

| Bucket | Limit | Stops |
| --- | --- | --- |
| Per account | 8 per 15 min | Grinding a password list against one victim |
| Per address | 50 per 15 min | Spraying one common password across many accounts |

The refusal message is identical whether or not the account exists, so this does
not become a way to test which usernames are real, and a successful sign-in
clears the counter so a few typos leave nothing behind. Locking one account does
not lock others.

Each attempt is counted **before** the password is checked, under a lock. It
used to be counted after, and Argon2 is slow on purpose, so forty guesses sent
in parallel all saw an under-limit bucket and all reached the password check.
Now at most eight do.

Administrative actions are written to an audit log -- who did what, to whom,
when -- as one JSON object per line on stdout (`app/log.py`), separate from the
human-readable app log on stderr. JSON escaping means a crafted username cannot
forge a second entry. Passwords never appear in it, and a **refused** action is
not recorded as though it happened, which would be worse than no trail at all.

### 3. End-to-End Encryption for Text and Voice

Text messages and voice notes are encrypted in the browser before they reach the server.

| Stage | Mechanism |
| --- | --- |
| Key agreement | ECDH on P-256 |
| Key derivation | A fresh ECDH key pair **per message**. HKDF-SHA256 over two agreements -- the ephemeral key with the recipient's key, and the two long-term keys -- salted with both long-term keys, with info naming the sender's key, the recipient's and the ephemeral one: one key per message and per direction |
| Message encryption | AES-GCM 256 with a fresh 96-bit IV per message, authenticating `v4\|conversation\|sender_id\|recipient_id\|msg_uuid\|sent_at\|epk` as additional data |

- The private key is generated **non-extractable** and stored in IndexedDB. Script cannot read it back out, so an XSS bug cannot exfiltrate it.
- Only the base64 SPKI public key is published to the server.
- Every message carries `v: 4`, a random `msg_uuid`, `sent_at` (the sender's clock in milliseconds) and `epk`, its ephemeral public key. Because the conversation, sender, recipient, uuid, time and ephemeral key are authenticated, the server cannot hand Alice's own message back to her as though Bob wrote it (one key per direction stops that), move it to another conversation, or change its time. The client refuses to show a message whose authenticated `sent_at` is more than 65 minutes old or 5 minutes ahead (*Can't be verified*), and remembers each `msg_uuid` it has shown in IndexedDB, so a second row carrying one is caught even after a reload. Messages from older versions are no longer decrypted at all -- a fallback would be a downgrade path -- and show *Sent before an update; can't be opened*; they expire within the hour. What freshness does and does not prove is in [docs/SECURITY.md](docs/SECURITY.md).
- **Forward secrecy, for the sender's side.** The per-message private key is never stored, so a sender's long-term key taken later opens none of the messages she sent; the sender still authenticates, because the long-term pair is mixed in. It does **not** protect against the recipient's key being taken later -- that needs a ratchet, which is not implemented. The price: after a reload, your own sent messages show *Sent with forward secrecy - only the recipient can open it now*; until then this page keeps them in memory, cleared at sign-out.
- The server stores ciphertext and the IV. It cannot read message contents, so reply previews and notifications show `Encrypted message` / `Voice message` rather than the body.
- Voice notes take the same path: the recording is encrypted as raw bytes, and the server stores base64 ciphertext plus the IV and the container type (`audio/webm`, `audio/wav`, …). The recipient decrypts to a `Blob` and plays it from an object URL — the audio never exists in the clear on the server.
- **No silent plaintext.** If encryption fails, the message is not sent and the text is given back; it used to be logged and sent in plaintext. If a peer has not published a key yet (or this browser cannot encrypt), the app asks once per conversation before sending unencrypted, and such messages carry a **Not encrypted** chip. Every message shows one status chip in words -- **E2EE**, **Not encrypted**, or for an image share **Opens once - N min left** -- and red is kept for tamper and error states. The rules are one pure function, `send-policy.js`, tested on its own.
- **Key pinning and safety numbers.** The first public key seen for each contact is pinned in IndexedDB. If the server later hands out a different one, sending to that contact stops and a **Safety number changed** dialog asks for an explicit decision; nothing is adopted silently. The key manager shows a 60-digit safety number per contact, computed from both keys so both people see the same number, with a **Mark as verified** toggle that is cleared if the key ever changes. It replaces an 8-byte fingerprint of one key.

**Limitation:** the private key is per-browser. Signing in from a different browser generates a new key pair, and messages encrypted to the previous key cannot be opened. Since messages expire after an hour, the practical impact is bounded.

### 4. Visual Cryptography Image Sharing

- **The split happens in the sender's browser** (`vc-client.js`); the server never receives the image. Images are converted to grayscale and split into two XOR shares.
- `Share 1` is fresh `crypto.getRandomValues` bytes and is the only thing uploaded, as a single-channel PNG the server stores byte for byte.
- `Share 2` travels inside an end-to-end encrypted (v4) message, together with the SHA-256 of Share 1's bytes. The server stores that ciphertext and cannot open it, so the recipient does not have to be online. In the browser the decrypted Share 2 is held in memory only.
- The recipient hashes the Share 1 it downloads against the sealed hash before decoding it. The tamper panel shows four checks: HMAC (server), server ML, in-browser ML, and this end-to-end hash — the only one that does not trust the server.
- The server-side split this replaced (`send_image`) is retired; the `/lab` dashboard still splits on the server, as a demonstration with no recipient.
- Reconstruction uses XOR:

```text
Reconstructed = Share 1 XOR Share 2
```

- Share 1 is served through an unguessable capability token and has **one-time access**: once downloaded, the link is dead.
- **Revoking is the sender's control**, on their own message bubble, which reads "Only <name> can rebuild this image. You can revoke it until they open it." Revoke is a secondary button that asks first, not a red one: it is a choice, not an alarm. The recipient legitimately holds both shares, so their panel offers **Download** for Share 1, Share 2 and the reconstruction instead.
- The reconstruction panel opens only from a received image share, with both shares already loaded. The manual tool (the sidebar's **Rebuild** button and its **Select Image** pickers, which took two share files from disk) was removed on 19 Nov 2026: in the app, a share only ever arrives in a message. On a phone the panel takes the full screen while it is open.

### 5. AI/ML Tamper Detection

Detects whether a received VC share has been changed before reconstruction.

- Model type: `RandomForestClassifier`.
- Features: entropy, histogram uniformity, pixel mean/std, neighbor correlation, Laplacian variance, block statistics, zero/saturation ratios.
- Output:
  - clean/tampered label
  - confidence score
  - tampering probability
  - suspicious-region heatmap
  - human-readable explanation

The pretrained model is included at:

```text
app/ai_security/models/tamper_detector.joblib
```

If the model file is missing, the detector degrades to a deterministic statistical scorer rather than failing.

Training script:

```bash
python scripts/train_tamper_detector.py
```

### 6. Tamper Detection in the Live Chat

This is the part that makes the feature real rather than a demo.

1. When a share is sent, the server writes Share 1 to disk and records a **SHA-256 digest and an HMAC-SHA256 tag** of the bytes that actually landed on disk.
2. When the share arrives in the recipient's chat, their client automatically calls `GET /api/share/<message_id>/analysis?token=...`.
3. The server re-reads Share 1 from disk, re-computes the HMAC, and runs the ML detector over it.
4. The chat bubble shows the verdict inline: HMAC match/mismatch, the ML label and confidence, the processing time, and an expandable heatmap with the model's reasoning.
5. Analysis **does not consume** the share's one-time access, so the recipient is warned *before* deciding to open the image.
6. The reconstruction panel repeats the verdict at the moment of reconstruction.

That retrieval moment is the meaningful one to check: the share has been at rest on the server and has crossed the network since it was generated.

The chat itself ships **no way to cause tampering** - it only detects it. Attack simulation lives in `/lab`, where it belongs. To demonstrate detection in the chat, modify a stored share from outside the app (which is what a real attacker would do) and press **Re-check integrity** on the message:

```bash
python -c "import cv2,glob,sys; sys.path.insert(0,'.'); from app.ai_security.attacks import tamper_share; p=sorted(glob.glob('app/shares/*.png'))[-1]; cv2.imwrite(p, tamper_share(cv2.imread(p,0), attack='block', strength=0.4)); print('tampered:', p)"
```

### 7. Cryptographic Integrity Verification

The AI detector is supported by exact cryptographic verification:

- SHA-256 digest comparison.
- HMAC-SHA256 verification with the server key.

```text
HMAC = exact tamper verification
ML   = intelligent suspicious pattern analysis and heatmap explanation
```

The two are genuinely complementary, and the test suite proves it — see **Why both HMAC and ML?** below.

### 8. Attack Simulation

The `/lab` dashboard can simulate these tampering attacks against Share 1:

- block replacement
- random noise injection
- blur region
- brightness shift
- crop-resize distortion
- scratch/line attack
- JPEG compression damage
- no attack / clean share

### 8b. In-Browser Tamper Detection (ONNX)

The Random Forest is trained in Python, but it **runs in the browser**. The
fitted estimator is exported to ONNX and executed with `onnxruntime-web`, so the
recipient can classify a share without uploading it anywhere.

Why this matters, and not just as an architecture diagram: `/share1/<id>` grants
**one-time** access. The moment the recipient fetches the share, the server will
refuse to look at it again — a re-check returns *"already opened"*. After that
point the browser is the **only** party that can say anything about the bytes
that actually arrived. Asking the server "is the file you gave me intact?" also
asks the wrong party: it is the one that stored it.

So the chat now shows three checks, and is explicit about what each one covers:

| Check | Runs on | Answers |
| --- | --- | --- |
| HMAC-SHA256 | Server (needs the secret key) | Is the stored share bit-for-bit what was generated? |
| Server ML | The share as stored | Does the server's copy look tampered? |
| **In-browser ML** | **The bytes you received** | **Does what I actually got look tampered?** |

If the ML rows disagree, the UI says so plainly — the two are the same forest and
agree to ~1e-7 on identical input, so a split verdict means they were shown
different data, not that one is buggy.

**Feature parity is the hard part, and it is proven, not asserted.** The model
was fitted on 13 statistics computed by NumPy/OpenCV; the browser must reproduce
them or the tree splits are meaningless. Open **`/lab/parity`** and press the
button: it generates shares, computes the features on both sides and diffs them,
including the awkward cases (dimensions not divisible by 16, single-row and
single-column images, zero-variance input, all-black). Measured worst case:

- **max relative feature difference: 3.8e-13**
- **max probability difference vs server scikit-learn: 6.2e-8**
- **inference: ~0.1–0.3 ms**

One case is designed to **fail** the naive way and pass the honest way. A colour
image is **refused** by the browser detector rather than scored. OpenCV decodes
colour PNGs through libpng and lands ±1 away from the BT.601 result on a fraction
of a percent of pixels, so no browser-side formula can guarantee the server's
exact pixels — and features from *nearly* the right pixels produce a
confident-looking verdict computed on the wrong data. It defers to the server
instead. Nothing real is lost: VC shares are always single-channel.

Notes:

- The runtime is **vendored** in `app/static/vendor/onnxruntime/`, not loaded
  from a CDN, so the demo does not depend on conference wifi.
- The ~11 MB wasm binary is fetched **lazily** — only when something first asks
  for a verdict — and is not precached by the service worker.
- CSP needed `'wasm-unsafe-eval'`, which permits WebAssembly compilation only;
  unlike `'unsafe-eval'` it does not re-enable `eval()`.
- Exporting also **decoupled the model from scikit-learn's version treadmill**:
  the `.joblib` already warns that it was pickled under 1.8 and loaded under 1.9.
  ONNX has no such coupling.

Regenerate the model after retraining:

```bash
python scripts/export_tamper_detector_onnx.py
```

That script refuses to write the file unless the ONNX output matches
scikit-learn on real shares across all seven attack types.

### 9. Image Reconstruction and Enhancement

After tamper analysis, the system reconstructs the image with XOR and applies a **classical** enhancement pipeline — fixed OpenCV operations, not a learned model:

- non-local means denoising
- local contrast improvement (CLAHE)
- gentle edge sharpening (unsharp mask)
- quality-safe fallback: if enhancement would *lower* SSIM, the plain reconstruction is reported instead — and the dashboard **says so explicitly**, showing both scores, rather than swapping silently

### 10. Evaluation Dashboard

`/lab` reports MSE, PSNR, SSIM, ML confidence, HMAC result, processing time, and the suspicious-region heatmap. The uploaded image is converted to grayscale, scaled to 512 px on its longest side, processed and discarded; the page says so where the image is chosen.

### 11. Account Settings

Everything in the Settings panel does something. Profile fields are stored on the server and visible to the other account; preferences are local to the browser and are read at the point of use, so toggling one changes behaviour immediately.

| Setting | Effect |
| --- | --- |
| Username | Renamed on the server, with the same validation as registration; clashes are refused case-insensitively and the old name is released |
| Profile photo | Uploaded from the sidebar or Settings; broadcast to other accounts |
| Bio | Shown under the account in the list and in the chat header |
| Status | Online / Away / Do Not Disturb, shown as a coloured pip and label; reverts to Offline when disconnected |
| Enable notifications | Master switch for the chime and desktop popups |
| Sound notifications | A short WebAudio chime on incoming messages (no audio asset shipped) |
| Desktop notifications | Separate opt-in for OS popups, so the chime can be kept without them |
| Mentions-only | Only alerts when the decrypted text contains `@yourname` |
| Send read receipts | When off, the app never reports messages as read - including on opening a chat - so the sender keeps seeing "Sent" |
| Show typing indicators | When off, no typing event is emitted at all |
| Passcode lock | See below |
| Export chat history | Unchanged, and working |
| Clear cache | Clears the loaded conversation and the asset caches, and names what it cleared. The E2EE key in IndexedDB is deliberately kept: deleting it would end this device's ability to read messages already sent to it. It previously called `sessionStorage.clear()`, which nothing in the app ever writes to, and reported success |
| Delete account | Re-authenticates first - password where one exists, a current TOTP code where two-factor is on, or the username typed back for Firebase accounts, which carry an empty password hash. The last administrator cannot delete themselves, the deletion is audited, and every other signed-in device is kicked |

**Passcode lock** is a device-local screen lock. The passcode is stored only as a PBKDF2-SHA256 hash (210,000 iterations, random salt) in localStorage and is never transmitted. The app locks on load and after the tab has been hidden for a minute.

Stated plainly for the viva: this stops someone picking up an unlocked browser. It is not a second factor and it does not encrypt anything — the non-extractable E2EE key in IndexedDB is what protects message content.

*Removed:* the old "Block screenshots" checkbox. A web page cannot prevent screenshots, so the control could never have worked; shipping it in a security project would have been a false claim.

### 12. Interface

Both the chat and the lab use one glassmorphism system: translucent panes with backdrop blur, hairline top highlights, and a slow-drifting aurora field behind everything so the glass has colour to refract.

| File | Role |
| --- | --- |
| `static/css/style.css` | Layout plus the design tokens (`:root` and `[data-theme="light"]`) |
| `static/css/glass.css` | The glass surface layer for the chat, loaded after style.css |
| `static/css/rx_lab.css` | The same system applied to `/lab` |

The colour tokens are translucent, so any rule painting with `--bg-secondary`, `--bg-tertiary` or `--border` becomes glass without being rewritten. To retune the look, edit the `--glass-*`, `--blur-*` and `--aurora-*` variables in `style.css`; nothing else needs to change.

Both light and dark themes are supported (🌙 in the sidebar). Light mode is not just inverted: the semantic buttons switch to near-opaque fills, because the white text on them is unreadable over translucent tint on a light backdrop.

**Graceful degradation** — the effect is layered so it can be removed without breaking the layout:

- `@supports not (backdrop-filter)` → opaque panes instead of unreadable transparent ones.
- `prefers-reduced-transparency: reduce` → blur and the aurora are dropped.
- `prefers-reduced-motion: reduce` → the aurora stops drifting.
- Below 560px → blur radii roughly halve and the aurora animation stops, since blur is the most GPU-expensive part of the design.

Two layout fixes came with the restyle: the sidebar now docks beside the chat above 900px instead of staying an off-canvas drawer at every width (delete the `@media (min-width: 900px)` block in `glass.css` to restore the drawer; 900 rather than 1100 so an iPad in landscape, at 1024px, does not fall in the gap), and the composer's icon buttons are square 44px touch targets, which stops the Send button being pushed off-screen on a 375px display.

---

## Installation

### 1. Create virtual environment

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

### 2. Install requirements

```bash
pip install -r requirements.txt
```

### 3. Run the app

```bash
python run.py
```

Open:

```text
http://127.0.0.1:5000
```

AI/ML final demo dashboard:

```text
http://127.0.0.1:5000/lab
```

---

## Running the tests

```bash
pip install -r requirements-dev.txt
```

```bash
pytest                          # 1190 server tests
node --test "tests/js/*.test.mjs"   # 264 browser tests
```

Both run on every push — see [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
CI also boots the production server from the `Procfile` and asserts it serves,
completes a Socket.IO handshake, and reports `firebase: null` when unconfigured,
because gunicorn does not run on Windows and an unverified deployment command is
just a note.

**Browser end-to-end tests** (`tests/e2e`, `pytest -m e2e`; the default run
leaves them out, so the counts above need no browser). Two people in two real
Chromium contexts sign in, open a chat by keyboard, send text both ways and see
the **E2EE** chip, send an image that is split in the browser, checked and
reconstructed, revoke a share, lose and regain the server and watch the session
resume, get the blocking warning when a peer's key changes, add a contact by
username and accept it, remove one, be told a username is taken when
creating an account, and pin, archive, lock (with a wrong PIN, then the right
one) and block a chat from its menu. axe also scans that menu and Contact info. axe-core then
scans login, chat, settings, the image send dialog, a received share with its
evidence open, a contact request, and `/lab` at 1440 and 375 px in both themes with
the AAA contrast rule on, failing on any violation; and the vendored DOMPurify
is run against XSS payloads in the page itself. Setup:
`pip install -r requirements-dev.txt`, `python -m playwright install chromium`,
`npm ci` in `tests/e2e`. CI runs them in their own job and keeps the
screenshots.

Production runs on PostgreSQL, so a second job runs the whole server suite
against a real PostgreSQL 16 service with `DATABASE_URL` set. There every test
shares one database: `tests/conftest.py` empties every table except
`schema_version` before and after each test (the first time before the app is
even imported, since importing it runs `init_db`), and the SQLite-only tests
-- the WAL settings and the upgrades of historical SQLite files -- skip with
a reason.

**1190 server tests** cover VC round-trips, share secrecy, the tamper detector and its fallback, metrics, HMAC integrity, one-time share access (including eight requests racing for the same share), deletion, retention, SQL portability across SQLite/PostgreSQL, the HTTP endpoints, and the Socket.IO handlers (voice, E2EE payloads, key registration, account settings), Firebase token verification including clock-drift tolerance, the TOTP second factor, the ONNX export (faithfulness to scikit-learn across all seven attacks, and that the browser assets are actually shipped and served), administration (the approval gate, the superadmin rank hierarchy, the lockout guards, and that every admin event is refused for non-admins and anonymous sockets), brute-force throttling with its audit trail, the message lifecycle (that an uninvolved user can neither revoke nor delete someone else's message, that a sender's delete removes Share 1 from disk while a recipient's does not, and that deleting an account leaves unrelated conversations intact), and the image path -- that the server stores the browser's Share 1 byte for byte and refuses anything that is not plausibly a share or that carries the image, that the sender never receives the capability token, that the recorded HMAC covers the bytes on disk, and that the numbers quoted in these docs match the constants in the code. The count includes the handful of tests that skip by design.

**264 browser tests** run the shipped `app/static/js` files verbatim on Node's
built-in runner — no build step, no npm dependency added to a Python project.
They cover the E2EE session (two independent sessions, so a symmetrically broken key agreement cannot pass by talking to itself; protocol v4 reflection, associated-data, freshness and replay cases, the replay check surviving a reload, forward secrecy for the sender (her own reloaded key cannot open what she sent) and its documented limit on the recipient's side, sender authenticity against a forged message; key pinning and the safety number; the send policy that decides when plaintext is allowed; the browser-side visual cryptography split, its PNG codec and the end-to-end hash; and the shipped v1, v2 and v3 clients as fixtures, so their payloads are proven to be refused rather than decrypted), the escaping layer, the image share card's verdict rules (any failing check means "tampered", which copy each check describes, bad news opening its own evidence), and the service worker's request scoping.

### Feature parity (M2)

The browser detector reproducing the server's was previously demonstrated at
`/lab/parity` — a page someone had to open and read. That page is still there
and is still the best way to *show* it, but the claim is now also asserted
automatically: `scripts/generate_feature_parity_fixture.py` records the server's
feature vectors for ten images, and `tests/js/tamper-features.test.mjs` compares
them against the browser extractor at 1e-9 relative tolerance. CI regenerates
the fixture and fails on any diff, so the server's implementation cannot drift
away from what the fixture claims it does.

Regenerate after changing either extractor:

```bash
python scripts/generate_feature_parity_fixture.py
```

---

## How to Demo in FYP

### Chat demo (shows the feature working in a real application)

1. Open two browsers and register two accounts. In one, type the other's username under **Contacts** and press **Add**; in the other, press **Accept** on the request.
2. Open the key manager (🔑) and show that both sides have exchanged real ECDH public keys, and that both screens show the same 60-digit safety number.
3. Send a text message. Show in the database that only ciphertext is stored:

```bash
python -c "import sqlite3;print([dict(r) for r in sqlite3.connect('app/data/reveal_x.db').execute('select content, extra from messages')])"
```

4. Send an image. It is split in the sender's browser; the recipient's bubble shows a green shield and **Share 1 verified intact**; **How it was checked** lists the HMAC match, ML confidence and the check time.
5. Modify the stored Share 1 from outside the app with the one-liner in [Tamper Detection in the Live Chat](#6-tamper-detection-in-the-live-chat) -- the chat itself has no control for causing tampering -- then press **Re-check** on the message.
6. The verdict flips to a red shield and **Tampering detected in Share 1**, and the evidence opens by itself: the HMAC mismatch, the ML confidence, the heatmap and the model's reasoning. On Reconstruct, the end-to-end SHA-256 row reports a mismatch as well.
7. Click Reconstruct. The panel repeats the warning, and the reconstructed image is visibly damaged; it also replaces the sealed tile in the bubble.
8. Try to open the share a second time — access is one-time, so it is refused.

### Lab demo (shows the measurements)

1. Upload an image at `/lab`.
2. Select `None / clean share` and run analysis. Both shares look random, HMAC passes, ML says clean.
3. Select `Block replacement` and run again. Show the HMAC mismatch, ML confidence, heatmap, damaged reconstruction, and the PSNR/SSIM drop. The heatmap is drawn over the received share; its opacity slider goes from the share alone (0%) to the heatmap alone (100%). Click any result image (or focus it and press Enter) to open it large with crisp pixels, where a scratch or a replaced block on the noise-like share is easy to see. The **Original vs reconstructed** card puts both in one frame with a divider you drag (or move with the arrow keys).
4. Press **Run all attacks** to fill the table of which check catches each of the seven attacks on this image.
5. Pick an attack and press **Sweep strength** to chart it from 5% to 75%: reconstruction SSIM and the ML's tamper probability, drawn as an inline SVG with the same numbers in a table. Swept with random noise, SSIM collapses while the ML's probability stays flat, which is the case the HMAC exists for.

---

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | The 27 functional and 14 non-functional requirements from the FYP report, each with the test that verifies it or its measured outcome |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model: assets, adversaries, every control mapped to the code and test that holds it — and an explicit list of what is **not** defended |
| [docs/API.md](docs/API.md) | All 9 HTTP routes and 57 Socket.IO events, with the fields each reads |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, Render, and running it from Windows behind a tunnel, on a fixed address on your own domain |
| [docs/FIREBASE_SETUP.md](docs/FIREBASE_SETUP.md) | Google sign-in, start to finish, including the failures and what each means |
| [docs/ADMIN.md](docs/ADMIN.md) | Roles, the approval gate, and the rank rules |
| [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md) | WCAG contrast measurements, the audit tool, the axe-core gate in CI, and the correction to an earlier claim |
| [docs/EVALUATION.md](docs/EVALUATION.md) | The tamper classifier under repeated cross-validation: spread of its accuracy, recall per attack, ROC curve, and how the shipped model behaves on share sizes it was not trained on. Generated by `scripts/evaluate_tamper_detector.py` |
| [FINAL_DEMO_GUIDE.md](FINAL_DEMO_GUIDE.md) | Running the demo |

Two of these are worth reading before a viva. **SECURITY.md** explains the
boundary: text, voice and images are end-to-end encrypted from the server. The
image used to be split on the server, which therefore saw it; the split now
runs in the sender's browser, and only random Share 1 and a sealed Share 2
reach the server. It also lists what that still does not protect.
**ACCESSIBILITY.md** records an audit that was confidently wrong and how it was
corrected.

## Project Structure

```text
app/
  ai_security/
    attacks.py              # controlled tamper simulations
    enhancement.py          # reconstruction enhancement pipeline
    integrity.py            # SHA-256 / HMAC-SHA256 verification
    metrics.py              # MSE, PSNR, SSIM
    tamper_detector.py      # ML model wrapper and heatmap logic
    utils.py                # image/base64 helpers
    models/
      tamper_detector.joblib
      tamper_detector_metrics.json
  static/
    css/rx_lab.css          # AI Lab styles
    js/chat/                # chat UI as native ES modules (no bundler), loaded by main.js
                            #   in the order the old single chat.js ran; see late.js
    js/crypto.js            # client-side XOR reconstruction
    js/e2ee.js              # ECDH + HKDF + AES-GCM message encryption
    js/rx_lab.js            # AI Lab frontend
    js/tamper-onnx.js       # in-browser detector: feature extraction + ONNX inference
    js/lab-parity.js        # browser-vs-server feature parity harness
    js/firebase-auth.js     # optional Google / email sign-in
    models/
      tamper_detector.onnx        # the forest, exported for the browser
      tamper_detector.meta.json   # feature order + export verification record
    vendor/vendor.json      # release, source and sha256 of every vendored file
    vendor/onnxruntime/     # vendored onnxruntime-web (no CDN, works offline)
    css/style.css           # chat layout + design tokens
    css/glass.css           # glassmorphism surface layer
  templates/
    lab.html                # AI/ML final demo dashboard
    lab_parity.html         # /lab/parity evidence page
    index.html              # chat interface
  vc_core/
    share_generator.py
    reconstructor.py
    utils.py
  models.py                 # SQLite/PostgreSQL storage layer
  routes.py                 # compatibility shim: imports app/handlers/
  handlers/                 # HTTP + Socket.IO handlers, one module per concern
    common.py               #   limits, throttles, session plumbing, shared helpers
    http.py auth.py admin.py settings.py messaging.py shares.py groups.py
  state.py                  # throttles, replay cache, sessions (memory or Redis)
  migrations.py             # numbered schema migrations
  sweeper.py                # retention sweep every 60 s
  log.py timeutil.py        # logging + JSON audit log; the one clock
scripts/
  train_tamper_detector.py
  export_tamper_detector_onnx.py   # joblib -> ONNX, refuses to write unless verified
  validate_final_product.py
  migrate_from_sqlite.py
tests/                      # pytest suite, plus tests/js for the browser tests
run.py
requirements.txt
requirements-dev.txt
```

---

## Viva Explanation

**Why not just WhatsApp?**

Reveal-X is not a WhatsApp replacement. It is a cybersecurity research prototype for sensitive image sharing. Its contribution is visual cryptography, AI/ML tamper detection, local reconstruction, and measurable image-security evaluation.

**Where is AI/ML used?**

AI/ML is used in the tamper detection module. A Random Forest model classifies received VC shares as clean or tampered using statistical image features, and produces a heatmap highlighting suspicious regions. It runs automatically on every image share in the chat, not just in the lab.

**Why use HMAC if ML already detects tampering?**

Because there is a concrete attack the ML provably cannot see. Share 1 is uniform random data, so **overwriting random pixels with other random pixels changes nothing measurable** — entropy, histogram and neighbour correlation all stay put. Measured over the seven attacks:

| Attack | ML detects | HMAC detects |
| --- | --- | --- |
| Block replacement | yes | yes |
| Blur region | yes | yes |
| Brightness shift | yes | yes |
| Crop + resize | yes | yes |
| Scratch/line | yes | yes |
| JPEG damage | yes | yes |
| **Random noise injection** | **no** | **yes** |

The HMAC catches all seven. The ML adds confidence scoring and a heatmap that localises *where* the change is, which a hash cannot do. This is verified in `tests/test_tamper_detector.py::test_noise_is_invisible_to_ml_but_caught_by_hmac`.

`/lab`'s **Run all attacks** reproduces this table on any uploaded image, at the chosen strength. The table above is the test suite's measurement; on other images the ML can miss more. A 160×120 test image at 35% strength also let the scratch through, with the HMAC still catching all seven, which is the point of having both.

**Why is there no denoising CNN or ESRGAN, when they were planned?**

Both were scoped and neither was implemented; the milestone table above says so
plainly. The honest answer is two-part.

The practical part: a denoising CNN needs a GPU and a paired clean/noisy
VC-specific dataset, and ESRGAN needs a pretrained super-resolution model and
the inference stack to run it. Neither was available for this project.

The more important part is that **XOR reconstruction is lossless**. Share 1 XOR
Share 2 returns the original bit-for-bit — the dashboard measures PSNR 99 dB and
SSIM 1.0 on a clean run. There is no noise to remove and no resolution to
recover. Enhancement and upscaling only do anything on a share that was
*damaged*, and a damaged share is precisely the case this system is built to
**detect and refuse**, not to repair. Restoring a tampered reconstruction would
arguably be the wrong behaviour: it would make a compromised image look
trustworthy.

The engineering effort went into M2 instead, where there was a real security
argument to make — moving detection into the browser so the recipient does not
have to trust the server that stored the share.

**Main limitations:**

- The included model is trained on synthetic tampering patterns for FYP demonstration. For production it should be retrained on real share manipulation attacks.
- M3 (denoising CNN) and the ESRGAN half of M4 are **not implemented**. See the milestone status table.
- The WCAG AAA claim covers **contrast only** (1.4.6), not the full Level AAA criteria set. See [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md).
- **The 93.33% is not a ceiling, and the gap is the interesting part.**
  `scripts/train_tamper_detector.py` includes `noise` among the tampered
  training classes. Overwriting random pixels of a uniform-random share with
  more random pixels changes nothing measurable, so those samples are
  statistically identical to the clean class — the model is being shown the
  same distribution under both labels.

  Measured, holding everything else fixed (same seed, same split, same
  hyper-parameters):

  | Training attacks | Held-out accuracy | Confusion matrix |
  | --- | --- | --- |
  | All seven, including `noise` (shipped) | **93.33%** | `[[29, 1], [3, 27]]` |
  | The six detectable ones | **100.00%** | `[[30, 0], [0, 30]]` |

  So **every error the shipped model makes is a `noise` sample**. On the
  attacks that are detectable by statistics at all, it is perfect.

  The shipped model deliberately keeps `noise` in, and keeps the 93.33% that
  the submitted documentation reports. Two reasons: the repository should not
  contradict the submission, and 100% on a synthetic dataset invites more
  scepticism than a figure that comes with an explanation. The 6.67-point gap
  *is* the explanation — it is the same finding as
  `test_noise_is_invisible_to_ml_but_caught_by_hmac`, expressed as a number,
  and it is the empirical case for pairing the detector with an HMAC rather
  than trusting it alone.
- The E2EE private key is per-browser (see section 2).

---

## Environment Variables

Optional:

```text
REVEAL_X_SECRET_KEY=change-this-secret       # also keys the share HMAC

# Administrator account - see docs/ADMIN.md
# Created or promoted on every startup. Registration is request-and-approve,
# so a deployment with no admin cannot approve anyone.
REVEALX_ADMIN_USER=root
REVEALX_ADMIN_PASSWORD=choose-something-long

# Optional Firebase sign-in - see docs/FIREBASE_SETUP.md
FIREBASE_API_KEY=...
FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
FIREBASE_PROJECT_ID=your-project
FIREBASE_APP_ID=1:123:web:abc
FIREBASE_CREDENTIALS_FILE=/path/to/serviceAccountKey.json
REVEAL_X_ALLOWED_ORIGINS=https://example.com # comma-separated; unset = same-origin only
REVEAL_X_COOKIE_SECURE=true
REVEAL_X_FORCE_HTTPS=true
REVEAL_X_ADHOC_SSL=true
DATABASE_URL=postgresql://...                # falls back to SQLite when unset
REVEALX_GROUPS=true                          # server-side group events; off by default, no client
PORT=5000
FLASK_DEBUG=true
```

`REVEAL_X_SECRET_KEY` is generated randomly at startup if unset, which means share HMAC tags do not survive a restart. Set it explicitly for any deployment where shares must outlive the process.

Socket.IO origins default to **same-origin only**. Set `REVEAL_X_ALLOWED_ORIGINS` only if the frontend is served from a different host.

By default, local development uses normal HTTP at `http://127.0.0.1:5000`.
