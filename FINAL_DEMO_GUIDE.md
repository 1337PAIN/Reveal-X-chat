# Reveal-X Final Demo Guide

Two demos. Run the **chat demo first** — it shows the security features working inside a real application. Then use `/lab` to show the measurements behind them.

---

## Demo 1 — Tamper detection in the live chat

Open two browser windows at `http://127.0.0.1:5000` and register two accounts (say **alice** and **bob**).

### 1. Show that E2EE is real

Click the 🔑 button in the sidebar. Both accounts show **🔒 Key exchanged** with a key fingerprint.

Say:

> Each browser generated an ECDH P-256 key pair. The private key is non-extractable and lives in IndexedDB — script cannot read it out. Only the public key was published. The shared AES-GCM key is derived with HKDF.

Send a text message, then show what the server actually stored:

```bash
python -c "import sqlite3;print([dict(r) for r in sqlite3.connect('app/data/reveal_x.db').execute('select content, extra from messages')])"
```

The `content` column is base64 ciphertext. The server cannot read it.

### 2. Send a voice note

Click the 🎤 button, speak, then click ⏹ to send. The recipient gets a playable audio message with a padlock.

Say:

> The recording was encrypted in the browser with the same derived key before it left the machine. The server holds base64 ciphertext and an IV — it never has the audio. The recipient's browser decrypts it back to a Blob and plays it locally.

Show the stored row again: the `content` column has no `data:audio/` header, just ciphertext.

### 3. Send an image and show automatic verification

Send an image from alice to bob. Bob's message bubble immediately shows:

```text
✅ Share 1 verified intact
HMAC-SHA256: match (bit-for-bit identical)
ML: Clean Share - 96.3% confidence
RandomForest VC share tamper detector - checked in 29 ms
```

Say:

> The server recorded an HMAC of Share 1 when it was written to disk. The moment the share reached the recipient, their client asked the server to re-verify it and to run the Random Forest detector over it. Both checks pass, and this happened automatically — no button was pressed.

### 4. Attack the share and show detection

The chat ships no button that causes tampering — it only detects it. So play the attacker from a second terminal, editing the stored share directly:

```bash
python -c "import cv2,glob,sys; sys.path.insert(0,'.'); from app.ai_security.attacks import tamper_share; p=sorted(glob.glob('app/shares/*.png'))[-1]; cv2.imwrite(p, tamper_share(cv2.imread(p,0), attack='block', strength=0.4)); print('tampered:', p)"
```

Back in bob's chat, press **Re-check integrity**. The verdict flips:

```text
⚠️ Tampering detected in Share 1
HMAC-SHA256: MISMATCH (share was modified)
ML: Tampered Share - 100.0% confidence
```

Expand **Suspicious-region heatmap and reasoning** to show the heatmap and the model's explanation.

Say:

> Nothing in the app did that. An attacker with write access to the stored share replaced a block of it, exactly as could happen on a compromised server. The HMAC no longer matches, which is exact proof of modification. The ML detector independently classified it as tampered, and the heatmap shows *where* — which a hash alone cannot tell you.

### 5. Reconstruct — and show the browser checking for itself

Click **Reconstruct**. The panel repeats the warning before opening the image, and the reconstructed result is visibly damaged.

Point at the third row in the verdict, which appears only at this moment:

```text
⚠️ Tampering detected in Share 1
HMAC-SHA256: MISMATCH (share was modified)
Server ML (share as stored): Tampered Share - 100.0% confidence
In-browser ML (bytes you received): Tampered Share - 100.0% confidence (30 ms, ONNX/wasm)
Suspicious-region heatmap and reasoning (computed in your browser)
```

Say:

> The first two rows come from the server. The third does not. That is the same Random Forest, exported to ONNX and executed inside this browser by WebAssembly, scoring the bytes that actually arrived. The heatmap was drawn here too — none of this round-tripped.

Then make the point that justifies it existing at all:

> Share 1 has one-time access. The moment I fetched it, the server will not look at it again — a re-check now returns "already opened". From this point on the browser is the *only* thing that can verify what I received. And asking the server whether the file it stored is intact is asking the party that stored it. This row does not depend on trusting them.

If the two ML rows ever disagree, the UI says so explicitly and tells the user to treat the share as tampered — the two are the same forest and agree to about 1e-7 on identical input, so a split verdict means they were shown different bytes.

### 6. Show one-time access and per-side deletion

Click **Reconstruct** again on the same share. It fails: Share 1 has one-time access, and it was consumed by the first download. The integrity check does not consume it, which is why the warning could be shown *before* opening.

Then show the two delete scopes. On a message bob **received**, the × reads *Delete for me*: it disappears for bob and **stays** in alice's window, and the row survives in the database with bob recorded in `deleted_by`. On a message bob **sent**, the × reads *Delete for everyone*: it vanishes from both windows and the row is destroyed outright.

```bash
python -c "import sqlite3;c=sqlite3.connect('app/data/reveal_x.db');c.row_factory=sqlite3.Row;[print(dict(r)) for r in c.execute('select id, deleted_by from messages')]"
```

### 7. Show the settings actually work

Open ⚙️ Settings. Rename the account, set a bio and set status to **Do Not Disturb** — the other window updates live (name, coloured pip, bio under the name).

Turn **Send read receipts** off, then open the other window's message: the sender keeps seeing "Sent" rather than "Read". Turn on **Lock this device with a passcode** and reload to show the lock screen.

Say:

> The passcode is stored only as a PBKDF2-SHA256 hash with a random salt and never leaves the browser. It locks the screen — it is not a second factor, and the E2EE key in IndexedDB is what actually protects message content.

---

## Demo 2 — `/lab` evaluation dashboard

Open `http://127.0.0.1:5000/lab`.

1. Upload an image.
2. Choose **None / clean share**, click **Run Reveal-X Analysis**.
   - Both shares look like random noise.
   - HMAC verification passed, ML classified the share as clean.
   - XOR reconstruction recovered the image exactly: SSIM 1.0, PSNR 99 dB.
3. Change the attack to **Block replacement** and run again.
   - SHA-256/HMAC detect the exact tampering.
   - ML detects the suspicious pattern and shows the heatmap.
   - PSNR/SSIM drop because the reconstruction is damaged.
4. Point at the enhancement row. When enhancement would *lower* SSIM, the dashboard says so explicitly and reports the unenhanced result instead of quietly substituting it.
5. Point at the **In-browser ML (ONNX)** card. It reports the same verdict, the inference time (typically **under a millisecond**), and `Δp` — how far the browser's probability sits from the server's. Expect `Δp` around `1e-8`.

Worth running the **noise** attack here as a pair with Demo 3: HMAC says tampered, and *both* ML engines say clean. The browser inherits the model's real blind spot rather than papering over it, which is the honest outcome — see the HMAC-vs-ML answer below.

---

## Demo 3 — `/lab/parity`: proving the browser model is the real one

Thirty seconds, and it pre-empts the obvious challenge — *"how do you know the
browser model behaves like the one you trained?"*

Open **`/lab/parity`** and press **Run parity check**. It generates shares,
computes all 13 features in Python **and** in JavaScript, and diffs them.

Expect **10 / 10**, with:

- **max relative feature difference: ~1e-13**
- **max probability difference vs server scikit-learn: ~1e-8**

Say:

> The model was fitted on statistics computed by NumPy and OpenCV. If the browser computed them even slightly differently, the tree splits would be meaningless and it would still return a confident-looking answer. So I test it rather than assume it — including the cases most likely to break: dimensions not divisible by the 16-pixel block grid, single-row and single-column images, zero-variance input, all-black.

Then scroll to the **last** case, which is the interesting one:

```text
64×64 colour image (must be REFUSED)
CORRECTLY REFUSED — deferred to the server
```

> OpenCV decodes colour PNGs through libpng, which lands ±1 away from the standard BT.601 result on a fraction of a percent of pixels. So there is no formula I can write in the browser that is guaranteed to reproduce the server's exact pixels. Rather than score *nearly* the right data and return a verdict that looks just as confident, the detector refuses and defers to the server. Nothing real is lost — visual cryptography shares are always single-channel. I would rather it decline than be quietly wrong.

Worth adding if pushed on the ONNX conversion itself:

> The export script will not write the model unless the ONNX output matches scikit-learn on real shares across all seven attack types, and never flips a verdict. Exporting also removed a fragility: the `.joblib` warns that it was pickled under scikit-learn 1.8 and loaded under 1.9. ONNX has no version coupling.

---

## The two strongest viva answers

### Why both HMAC and ML?

Because there is a concrete attack the ML provably cannot detect. Share 1 is uniform random data, so overwriting random pixels with other random pixels changes nothing measurable — entropy, histogram and correlation all stay put.

| Attack | ML detects | HMAC detects |
| --- | --- | --- |
| Block replacement | yes | yes |
| Blur region | yes | yes |
| Brightness shift | yes | yes |
| Crop + resize | yes | yes |
| Scratch/line | yes | yes |
| JPEG damage | yes | yes |
| **Random noise injection** | **no** | **yes** |

HMAC catches all seven. ML adds a confidence score and localises the change on a heatmap. Neither alone is sufficient. This is proved by a test:

```bash
pytest tests/test_tamper_detector.py::test_noise_is_invisible_to_ml_but_caught_by_hmac -v
```

### Why is this not just WhatsApp?

Reveal-X is a cybersecurity prototype for sensitive image sharing. Its contribution is the combination:

```text
Visual Cryptography + E2EE + HMAC Integrity + ML Tamper Detection + Reconstruction Quality Evaluation
```

---

## If asked to prove it works

```bash
pytest
```

170 tests: VC round-trips and share secrecy, tamper detection and its fallback scorer, metrics, HMAC integrity across all seven attacks, one-time share access, deletion, retention, SQL portability, the HTTP endpoints, the Socket.IO handlers covering voice, E2EE payloads, key registration and account settings, Firebase token verification, the TOTP second factor, and the ONNX export.

The ONNX tests check that the exported model matches scikit-learn on clean shares and all seven attacks, never flips a verdict, still reproduces the model's real `noise` blind spot, and that every browser asset is actually shipped and served — a missing file would silently disable in-browser detection without breaking anything visible.

```bash
python scripts/validate_final_product.py
```

## Screenshots worth capturing

- The key manager showing exchanged fingerprints.
- A clean share bubble (green, ✅ verified intact).
- A tampered share bubble (red, HMAC mismatch + ML confidence).
- The expanded heatmap and reasoning.
- The `/lab` PSNR/SSIM table for clean vs tampered runs.
- A voice message bubble with its padlock, next to the ciphertext row in the database.
