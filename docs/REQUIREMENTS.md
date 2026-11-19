# Requirements

The functional and non-functional requirements of Reveal-X, as stated in
Chapter 3 of the FYP report (Tables 3.1 and 3.2). It is generated from the
report's own tables, so the two always agree: a requirement changes in the
report first, then here.

**27 functional requirements** and **14 non-functional
requirements.** Every functional requirement names the test module that holds
it in place (under `tests/`; `tests/js` is the browser suite), and every
non-functional requirement records its measured outcome. Section references
(§) are to the report.

## Functional requirements

Priority is Essential, Expected or Desirable.

| ID | Requirement | Priority | Verified by |
| --- | --- | --- | --- |
| FR-1 | A visitor shall be able to request an account; no account is usable until an administrator approves it | Essential | test_admin.py |
| FR-2 | A user shall authenticate with a username and password hashed with Argon2id | Essential | test_auth.py |
| FR-3 | A user shall optionally authenticate through Google, verified server-side by Firebase ID token | Desirable | test_auth.py |
| FR-4 | A user shall optionally enrol a TOTP second factor; no session is issued until a valid code is supplied | Expected | test_auth.py |
| FR-5 | The system shall throttle failed sign-in attempts per account and per network address | Essential | test_rate_limit.py |
| FR-6 | A user shall exchange one-to-one text messages in real time | Essential | test_routes.py |
| FR-7 | Text, voice and the image envelope shall be end-to-end encrypted with a key the server never holds, bound to sender, recipient and message | Essential | tests/js, test_e2ee_v4.py |
| FR-8 | A user shall record and send a voice note, encrypted as raw bytes | Expected | test_voice.py |
| FR-9 | A user shall share an image, which the sender’s browser splits into two visual cryptography shares | Essential | test_client_split.py |
| FR-10 | Share 2 shall never reach the server in readable form under any code path | Essential | test_client_split.py, test_models.py |
| FR-11 | Share 1 shall be retrievable exactly once, through an unguessable capability token | Essential | test_models.py |
| FR-12 | The system shall verify a stored share against an HMAC-SHA256 tag before release | Essential | test_integrity.py |
| FR-13 | The system shall classify a share as clean or tampered and localise suspected damage | Essential | test_tamper_detector.py |
| FR-14 | The recipient’s browser shall run the same classifier independently and agree with the server | Expected | test_onnx_export.py |
| FR-15 | Integrity analysis shall not consume the one-time share access | Expected | test_models.py, test_routes.py |
| FR-16 | A sender shall revoke a message, destroying Share 1 on disk; a recipient’s delete shall hide only their own copy | Expected | test_message_lifecycle.py |
| FR-17 | Messages and shares shall expire automatically after one hour | Expected | test_message_lifecycle.py |
| FR-18 | An administrator shall approve, suspend, reinstate and delete accounts strictly below their own rank | Essential | test_admin.py |
| FR-19 | Every administrative action shall be recorded in an audit trail the panel cannot edit | Expected | test_admin.py |
| FR-20 | A user shall change profile, presence and preference settings, and delete their own account after re-authenticating | Expected | test_settings.py |
| FR-21 | The application shall be installable as a Progressive Web App with offline asset caching | Desirable | test_pwa.py |
| FR-22 | An evaluation dashboard shall reproduce the reported measurements without requiring an account | Desirable | test_routes.py |
| FR-23 | A user shall see and message only contacts: people added by exact username who accepted the request; the server shall refuse every send, typing notice and history request between anyone else | Essential | test_contacts.py |
| FR-24 | A username shall belong to one account regardless of case, and account creation shall say whether a name is taken as it is typed | Expected | test_username_unique.py |
| FR-25 | A user shall pin, archive, mute and clear a conversation from a menu beside the contact’s name, kept per account on the server | Expected | test_chat_menu.py |
| FR-26 | A user shall lock a conversation behind a chat-lock PIN; the server shall not serve its history until the PIN is entered, and shall limit wrong PINs | Expected | test_chat_lock_and_block.py |
| FR-27 | A user shall block an account, ending the contact and refusing its messages and requests | Expected | test_chat_lock_and_block.py |

## Non-functional requirements

Each is a measurable target; the measurements are reported in Chapter 5 of
the report.

| ID | Category | Requirement and acceptance criterion | Measured outcome |
| --- | --- | --- | --- |
| NFR-1 | Correctness | Reconstruction shall be lossless: MSE = 0 and SSIM = 1.0000 on every test image | Met — 6 of 6 images bit-exact |
| NFR-2 | Confidentiality | A single share shall be statistically indistinguishable from uniform random data (entropy ≥ 7.99 bits/px, \|r\| < 0.01) | Met — 7.997 bits/px, r = 0.0035 |
| NFR-3 | Integrity | Any modification of a stored share shall be detected by at least one layer | Met — 420 of 420 attacks detected |
| NFR-4 | Integrity | False-positive rate of the classifier on clean shares shall be below 2% | Met — 0.71% at threshold 0.50 |
| NFR-5 | Performance | A complete integrity check shall take under 150 ms at the image size cap | Met — 109 ms at the 1,200-px cap (484 ms before the block statistics were vectorised) |
| NFR-6 | Performance | Share generation and reconstruction shall be negligible against detection cost | Met — 0.013 ms and 0.003 ms at 256x256 |
| NFR-7 | Consistency | Server and browser classifiers shall never reach opposite verdicts | Met — 0 disagreements over 200 samples |
| NFR-8 | Security | Passwords shall be hashed with a memory-hard function | Met — Argon2id, 64 MiB, 55 ms |
| NFR-9 | Security | The share integrity key shall be independent of the session signing key | Met — separate key, one-way derived |
| NFR-10 | Accessibility | Text contrast shall meet WCAG 2.1 Level AAA in both themes | Met — 0 below AAA across 229 elements; axe-core AAA gate in CI |
| NFR-11 | Usability | The interface shall be usable at phone widths and honour reduced-motion and reduced-transparency preferences | Met — audited, §5.7 |
| NFR-12 | Portability | The system shall run on SQLite in development and PostgreSQL in production from one codebase | Met — asserted in CI |
| NFR-13 | Maintainability | Automated test code shall be comparable in size to application code | Met — 16,585 vs 17,707 lines; 1,459 tests |
| NFR-14 | Reliability | The full suite shall pass on every change, on two Python versions | Met — CI green |

## Running the checks

```bash
pytest                              # server tests
node --test "tests/js/*.test.mjs"   # browser tests
pytest -m e2e                       # end-to-end, in Chromium
```
