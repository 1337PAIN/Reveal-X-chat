"""Time-based one-time passwords (RFC 6238) for second-factor sign-in.

This runs entirely in the app, so it works on Firebase's free tier and on a
laptop with no internet. It sits *after* whichever first factor was used -
Firebase Google, Firebase email/password, or the built-in password login - so
knowing the password (or holding a Google session) is not sufficient on its own.

Codes are 6 digits on a 30-second step, matching Google Authenticator, Authy
and 1Password.
"""

from __future__ import annotations

import base64
import io
import threading
import time

import pyotp
import qrcode

TOTP_ISSUER = 'Reveal-X'
TOTP_DIGITS = 6
TOTP_INTERVAL = 30
# One step either side, to tolerate clock drift between phone and server.
TOTP_VALID_WINDOW = 1

# A correct code must not be replayable inside its own window: without this,
# anyone who observes a code (shoulder-surfing, a logged request) could reuse
# it for up to 90 seconds. Keyed by (user_id, code).
_used_codes: dict[tuple[str, str], float] = {}
_used_lock = threading.Lock()


def generate_totp_secret() -> str:
    """A fresh base32 secret for one account."""
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account_label: str) -> str:
    """The otpauth:// URI an authenticator app scans."""
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL).provisioning_uri(
        name=account_label or 'account',
        issuer_name=TOTP_ISSUER,
    )


def totp_qr_data_url(secret: str, account_label: str) -> str:
    """Render the provisioning URI as a PNG data URL.

    Generated server-side so the page needs no QR library and no external
    request - the secret never leaves this origin.
    """
    uri = totp_provisioning_uri(secret, account_label)
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(uri)
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def _prune_used(now: float) -> None:
    horizon = TOTP_INTERVAL * (TOTP_VALID_WINDOW + 1)
    for key, seen_at in list(_used_codes.items()):
        if now - seen_at > horizon:
            _used_codes.pop(key, None)


def verify_totp_code(secret: str, code: str, user_id: str = '') -> bool:
    """Check a 6-digit code, rejecting replays of one already accepted."""
    if not secret or not code:
        return False

    code = str(code).strip().replace(' ', '')
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False

    totp = pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    if not totp.verify(code, valid_window=TOTP_VALID_WINDOW):
        return False

    now = time.time()
    key = (user_id or secret, code)
    with _used_lock:
        _prune_used(now)
        if key in _used_codes:
            return False
        _used_codes[key] = now

    return True


def reset_replay_cache() -> None:
    """Test hook: forget which codes have been used."""
    with _used_lock:
        _used_codes.clear()
