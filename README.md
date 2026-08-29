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
SSIM, rather than silently reporting the better number. ESRGAN super-resolution
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
- **End-to-end encrypted text and voice messages** (see section 2).
- Voice notes recorded in-browser with `MediaRecorder`, capped at 60 seconds and 2 MB.
- Typing indicators, read receipts, reactions, forwarding, deletion, profile image support, and responsive UI.
- Messages and shares auto-delete after 1 hour.
- **Deletion scope depends on who is asking.** The sender owns what they sent, so their delete removes the message for *everyone* — the row and any stored Share 1 are destroyed on both sides. The recipient can only delete their own copy; the sender keeps theirs. The delete control is labelled accordingly (`Delete for everyone` vs `Delete for me`). A recipient who deletes a share also gives up their access to it.
- XSS protection: message bodies are rendered with `textContent`, so they can never introduce markup. DOMPurify and server-side escaping cover the remaining surfaces.
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
- Requesting an account with a **name that is already taken** returns the same
  "Request submitted" message as any other request, because requests are
  invisible to the requester and an honest answer would enumerate accounts.
- The system refuses any operation that would leave it unadministrable: you
  cannot disable or delete your own account, and the last active admin cannot be
  removed.

Full details: [docs/ADMIN.md](docs/ADMIN.md).

### 2c. Brute-force protection and the audit trail

Sign-in failures are throttled **on the server**. The browser shows an attempts
counter, but that lives in the client and an attacker scripting the socket never
sees it -- before this, sixty wrong passwords in a row were accepted and the
right one still worked immediately afterwards.

Two buckets, because either alone is easy to sidestep:

| Bucket | Limit | Stops |
| --- | --- | --- |
| Per account | 8 per 15 min | Grinding a password list against one victim |
| Per address | 20 per 15 min | Spraying one common password across many accounts |

The refusal message is identical whether or not the account exists, so this does
not become a way to test which usernames are real, and a successful sign-in
clears the counter so a few typos leave nothing behind. Locking one account does
not lock others.

Administrative actions are written to an audit line -- who did what, to whom,
when. Passwords never appear in it, and a **refused** action is not recorded as
though it happened, which would be worse than no trail at all.

### 3. End-to-End Encryption for Text and Voice

Text messages and voice notes are encrypted in the browser before they reach the server.

| Stage | Mechanism |
| --- | --- |
| Key agreement | ECDH on P-256 |
| Key derivation | HKDF-SHA256, salted with both public keys |
| Message encryption | AES-GCM 256 with a fresh 96-bit IV per message |

- The private key is generated **non-extractable** and stored in IndexedDB. Script cannot read it back out, so an XSS bug cannot exfiltrate it.
- Only the base64 SPKI public key is published to the server.
- The server stores ciphertext and the IV. It cannot read message contents, so reply previews and notifications show `Encrypted message` / `Voice message` rather than the body.
- Voice notes take the same path: the recording is encrypted as raw bytes, and the server stores base64 ciphertext plus the IV and the container type (`audio/webm`, `audio/wav`, …). The recipient decrypts to a `Blob` and plays it from an object URL — the audio never exists in the clear on the server.
- If a peer has not published a key yet, the message is sent in plaintext and is **labelled `[not encrypted]`** in the UI. The padlock badge appears only on messages that were genuinely encrypted.
- Key fingerprints are shown in the key manager for out-of-band verification.

**Limitation:** the private key is per-browser. Signing in from a different browser generates a new key pair, and messages encrypted to the previous key cannot be opened. Since messages expire after an hour, the practical impact is bounded.

### 4. Visual Cryptography Image Sharing

- Images are converted to grayscale and split into two XOR shares.
- `Share 1` is stored/transmitted as the network share. Its mask comes from `os.urandom`, not `np.random`.
- `Share 2` is delivered live to the receiver and is never persisted in the database.
- Reconstruction uses XOR:

```text
Reconstructed = Share 1 XOR Share 2
```

- Share 1 is served through an unguessable capability token and has **one-time access**: once downloaded, the link is dead.
- **Revoking is the sender's control**, on their own message bubble. The recipient legitimately holds both shares, so their panel offers **Download** for Share 1, Share 2 and the reconstruction instead.
- The reconstruction panel has two modes. Opened from a message, both shares load themselves and no file pickers are shown. Opened from the 🔄 button in the sidebar it is a manual tool, and the **Select Image** pickers appear.

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

The final dashboard reports MSE, PSNR, SSIM, ML confidence, HMAC result, processing time, and the suspicious-region heatmap.

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
| Export chat history / Clear cache / Delete account | Unchanged, and working |

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

Two layout fixes came with the restyle: the sidebar now docks beside the chat above 1100px instead of staying an off-canvas drawer at every width (delete the `@media (min-width: 1100px)` block in `glass.css` to restore the drawer), and the composer's icon buttons are square 44px touch targets, which stops the Send button being pushed off-screen on a 375px display.

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
pytest
```

254 tests cover VC round-trips, share secrecy, the tamper detector and its fallback, metrics, HMAC integrity, one-time share access, deletion, retention, SQL portability across SQLite/PostgreSQL, the HTTP endpoints, and the Socket.IO handlers (voice, E2EE payloads, key registration, account settings), Firebase token verification, the TOTP second factor, the ONNX export (faithfulness to scikit-learn across all seven attacks, and that the browser assets are actually shipped and served), and administration (the approval gate, the superadmin rank hierarchy, the lockout guards, and that every admin event is refused for non-admins and anonymous sockets), brute-force throttling with its audit trail, and the image-sharing handler itself -- that Share 1 XOR Share 2 returns the original bit for bit, that neither share alone correlates with it, that the sender never receives Share 2 or the capability token, and that the recorded HMAC covers the bytes on disk rather than the array in memory.

JavaScript/NumPy feature parity cannot be checked from pytest — it needs a real
browser — so it lives at `/lab/parity` instead. `test_parity_harness_is_wired`
guarantees that harness and its reference endpoint keep working.

---

## How to Demo in FYP

### Chat demo (shows the feature working in a real application)

1. Open two browsers and register two accounts.
2. Open the key manager (🔑) and show that both sides have exchanged real ECDH public keys, with fingerprints.
3. Send a text message. Show in the database that only ciphertext is stored:

```bash
python -c "import sqlite3;print([dict(r) for r in sqlite3.connect('app/data/reveal_x.db').execute('select content, extra from messages')])"
```

4. Send an image. The recipient's bubble immediately shows **✅ Share 1 verified intact**, HMAC match, ML confidence and the check time.
5. Open **Attack simulation (demo)**, pick *Block replacement*, and run it.
6. The verdict flips live to **⚠️ Tampering detected**, with the HMAC mismatch, the ML confidence, the heatmap and the model's reasoning.
7. Click Reconstruct. The panel repeats the warning, and the reconstructed image is visibly damaged.
8. Try to open the share a second time — access is one-time, so it is refused.

### Lab demo (shows the measurements)

1. Upload an image at `/lab`.
2. Select `None / clean share` and run analysis. Both shares look random, HMAC passes, ML says clean.
3. Select `Block replacement` and run again. Show the HMAC mismatch, ML confidence, heatmap, damaged reconstruction, and the PSNR/SSIM drop.

---

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
    js/chat.js              # chat UI, live tamper panel
    js/crypto.js            # client-side XOR reconstruction
    js/e2ee.js              # ECDH + HKDF + AES-GCM message encryption
    js/rx_lab.js            # AI Lab frontend
    js/tamper-onnx.js       # in-browser detector: feature extraction + ONNX inference
    js/lab-parity.js        # browser-vs-server feature parity harness
    js/firebase-auth.js     # optional Google / email sign-in
    models/
      tamper_detector.onnx        # the forest, exported for the browser
      tamper_detector.meta.json   # feature order + export verification record
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
  routes.py                 # HTTP + Socket.IO handlers
scripts/
  train_tamper_detector.py
  export_tamper_detector_onnx.py   # joblib -> ONNX, refuses to write unless verified
  validate_final_product.py
  migrate_from_sqlite.py
tests/                      # pytest suite
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
PORT=5000
FLASK_DEBUG=true
```

`REVEAL_X_SECRET_KEY` is generated randomly at startup if unset, which means share HMAC tags do not survive a restart. Set it explicitly for any deployment where shares must outlive the process.

Socket.IO origins default to **same-origin only**. Set `REVEAL_X_ALLOWED_ORIGINS` only if the frontend is served from a different host.

By default, local development uses normal HTTP at `http://127.0.0.1:5000`.
