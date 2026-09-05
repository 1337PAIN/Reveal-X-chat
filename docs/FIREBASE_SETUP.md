# Firebase sign-in setup

Reveal-X supports three ways in:

| Method | Needs Firebase? | Works offline? |
| --- | --- | --- |
| Username + password (built in) | No | Yes |
| Google | Yes | No |
| Email + password via Firebase | Yes | No |
| **Two-factor (TOTP)** on top of any of the above | No | Yes |

Firebase is **optional**. With nothing configured the app runs exactly as before: the Google/email block stays hidden, the Firebase SDK is never even downloaded, and the built-in login is used. Two-factor works either way.

---

## 1. Create the Firebase project

1. Go to <https://console.firebase.google.com> and **Add project**. Analytics is not needed.
2. Open **Authentication**, then **Get started**.

   The console no longer has a "Build" section — that navigation was reorganised.
   Authentication is pinned under **Project shortcuts** in the left sidebar, or go
   straight to `https://console.firebase.google.com/project/<project-id>/authentication/providers`.

   **Get started is not optional.** Registering a web app gives you a config
   snippet, which makes it look like Firebase is ready, but it does not create
   the Auth configuration. Until you click it, sign-in fails with
   `auth/configuration-not-found` and this probe returns `CONFIGURATION_NOT_FOUND`:

   ```bash
   curl -s -X POST "https://identitytoolkit.googleapis.com/v1/accounts:createAuthUri?key=$FIREBASE_API_KEY"         -H "Content-Type: application/json"         -d '{"identifier":"probe@example.com","continueUri":"http://localhost:5000"}'
   ```

3. On the **Sign-in method** tab, enable:
   - **Google** (pick a support email)
   - **Email/Password**
4. On the **Settings → Authorized domains** tab, add the host you will serve from. `localhost` is there by default; add your LAN IP or deployed domain if you use one.

## 2. Get the web app config

1. **Project settings → General → Your apps → Web (`</>`)**.
2. Register the app; Firebase shows a `firebaseConfig` object.
3. Copy `apiKey`, `authDomain`, `projectId`, `appId`.

These four are **not secrets** — they identify the project to Google and are visible in any Firebase web app. Access is controlled by the authorized-domains list and your security rules, not by hiding these.

## 3. Get the service-account key

This one **is** a secret. It lets the server verify ID tokens.

1. **Project settings → Service accounts → Generate new private key**.
2. Save the downloaded JSON somewhere outside the repo.

> The repo's `.gitignore` covers `.env`, but **not** an arbitrary JSON key path. Keep the key outside the project folder, or add its filename to `.gitignore` yourself.

## 4. Set the environment variables

```bash
FIREBASE_API_KEY=AIza...
FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
FIREBASE_PROJECT_ID=your-project
FIREBASE_APP_ID=1:1234567890:web:abcdef
FIREBASE_CREDENTIALS_FILE=/absolute/path/to/serviceAccountKey.json
```

`FIREBASE_CREDENTIALS_JSON` accepts the JSON inline instead, if a file path is awkward (e.g. on a PaaS).

`FIREBASE_CLOCK_SKEW_SECONDS` (optional, default **30**, capped at 60) is the tolerance
for clock drift when checking token timestamps. You should not need to touch it —
see below for why it is not zero.

### Why token checking tolerates clock drift

Google stamps an ID token's `iat` from *its* clock. The Admin SDK defaults to
`clock_skew_seconds=0`, so a server running even **one second** behind Google
rejects every token:

```
InvalidIdTokenError: Token used too early, 1788637184 < 1788637185.
```

This deployment was 1s out, which is well inside normal NTP drift and invisible
to anyone looking at their clock — and it failed **100%** of Google sign-ins. It
is not something a user can be asked to fix, so the tolerance defaults to 30
seconds.

That widens `iat`/`nbf` and `exp` by the same amount. Against a one-hour token
30 seconds is immaterial: a replayed token still has to be used inside its own
lifetime, which is what `exp` enforces. `check_revoked` stays on.

Windows PowerShell, for one session:

```powershell
$env:FIREBASE_API_KEY="AIza..."; $env:FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"; $env:FIREBASE_PROJECT_ID="your-project"; $env:FIREBASE_APP_ID="1:123:web:abc"; $env:FIREBASE_CREDENTIALS_FILE="C:\keys\serviceAccountKey.json"; python run.py
```

## 5. Install the Admin SDK

```bash
pip install "firebase-admin>=6.5.0"
```

It is in `requirements.txt`, but the app treats it as optional: without it
`firebase_enabled()` reports False and the Google button simply never appears —
no error, no warning. If the button is missing and your config looks right,
check this first.

## 6. Check it

```bash
curl http://127.0.0.1:5000/api/auth/config
```

- `{"firebase": null, ...}` → not configured. Either a variable is missing or the service-account key could not be loaded; the server prints the reason on startup.
- `{"firebase": {...}, ...}` → working. The Google button appears on the login screen.

The endpoint deliberately reports `null` unless **both** the web config is present **and** the server can verify tokens, so the button never appears if it would lead to a dead end.

---

## How it fits together

```text
Browser                              Server
  |  sign in with Google/email          |
  |------------------------------------>|  (Firebase, not us)
  |  <-- Firebase ID token (JWT)        |
  |                                     |
  |  socket: firebase_login {id_token}  |
  |------------------------------------>|
  |                                     |  verify_id_token()  <- Admin SDK
  |                                     |  checks signature, issuer,
  |                                     |  audience, expiry, revocation
  |                                     |
  |                                     |  map verified uid -> local account
  |  <-- totp_required (if 2FA on)      |
  |  socket: verify_totp {code}         |
  |------------------------------------>|
  |  <-- auth_success                   |
```

**Nothing the client claims about its own identity is trusted.** The UID comes only out of verified token claims. A forged or expired token is rejected before any account is touched — there is a test for exactly this (`test_an_unverifiable_token_grants_nothing`).

### Account mapping

Firebase accounts are keyed on the **Firebase UID only**, never on email address. This is deliberate: matching on email would let someone who signs up with an unverified address matching an existing account take it over. `test_firebase_account_never_matched_on_email_alone` pins that behaviour.

A username is derived from the email local-part (or Google display name) and de-duplicated with a numeric suffix. Users can rename themselves afterwards in Settings.

### Linking an existing local account to Google

Not supported. A Google sign-in always resolves to its own account row. If you want an existing local account to become a Google account, that is a linking flow with its own verification requirements — worth saying out loud in a viva rather than pretending it works.

---

## Two-factor (TOTP)

Independent of Firebase, so it works on the free Spark plan and with no internet.

- **Enable:** Settings → Security → *Two-factor authentication*. Scan the QR with Google Authenticator, Authy or 1Password, then enter a code to confirm.
- The secret is only written to the account **after** a valid code proves the authenticator has it, so a half-finished setup cannot lock anyone out.
- **Disable** requires a current code, so someone on an already-open session cannot quietly switch it off.

Implementation notes worth knowing:

- RFC 6238, 6 digits, 30-second step, ±1 step tolerance for clock drift.
- A correct code is **single-use**: accepting it again within its window is refused, so an observed code cannot be replayed.
- Five wrong codes abandon the sign-in entirely.
- The pending sign-in lives in server memory only and dies with the socket — the first factor grants **no session at all** until the code checks out.

### Why not Firebase's own MFA?

Firebase multi-factor requires upgrading the project to **Identity Platform** on the Blaze pay-as-you-go plan, which needs a billing card. Doing TOTP in-app avoids that, keeps the demo working offline, and is more substantial to show as your own work.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Google button never appears | `/api/auth/config` returns `firebase: null` — a variable is missing, `firebase-admin` is not installed, or the service-account key failed to load |
| `auth/configuration-not-found` | Authentication was never switched on. Registering a web app gives you a config snippet without creating any Auth config, so this looks like a working setup right up until someone tries to sign in. Open Authentication → **Get started** |
| `auth/unauthorized-domain` | Add the host to Firebase → Authentication → Settings → Authorized domains |
| `auth/operation-not-allowed` | The provider is not enabled on the Sign-in method tab |
| `auth/popup-blocked` | The browser blocked the popup; allow popups for this origin |
| "Sign-in token was rejected" | **The server log now says why** — it is printed there and deliberately not sent to the browser. Most often clock drift (see below) or a service account belonging to a different project than the web config |
| Popup opens then closes with no result | Serving over plain `http://` on a non-localhost host; Google requires `https` or `localhost` |
