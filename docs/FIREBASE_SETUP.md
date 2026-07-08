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
2. In the project, open **Build → Authentication → Get started**.
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

Windows PowerShell, for one session:

```powershell
$env:FIREBASE_API_KEY="AIza..."; $env:FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"; $env:FIREBASE_PROJECT_ID="your-project"; $env:FIREBASE_APP_ID="1:123:web:abc"; $env:FIREBASE_CREDENTIALS_FILE="C:\keys\serviceAccountKey.json"; python run.py
```

## 5. Check it

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
| Google button never appears | `/api/auth/config` returns `firebase: null` — a variable is missing or the service-account key failed to load |
| `auth/unauthorized-domain` | Add the host to Firebase → Authentication → Settings → Authorized domains |
| `auth/operation-not-allowed` | The provider is not enabled on the Sign-in method tab |
| `auth/popup-blocked` | The browser blocked the popup; allow popups for this origin |
| "Sign-in token was rejected" | The server's service account belongs to a different project than the web config |
| Popup opens then closes with no result | Serving over plain `http://` on a non-localhost host; Google requires `https` or `localhost` |
