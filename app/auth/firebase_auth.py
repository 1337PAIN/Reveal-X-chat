"""Firebase ID token verification.

The browser signs in with Firebase (Google or email/password) and sends the
resulting ID token here. That token is a JWT signed by Google; this module
verifies the signature, issuer, audience and expiry with the Firebase Admin
SDK before any account is trusted.

Nothing the client claims about its own identity is believed. A UID is only
accepted when it comes out of `verify_id_token()`.

Firebase is optional: with no credentials configured the app falls back to the
built-in username/password sign-in and these helpers report "not enabled".
"""

from __future__ import annotations

import json
import os
import threading

try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth_sdk
    from firebase_admin import credentials
    FIREBASE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    firebase_admin = None
    firebase_auth_sdk = None
    credentials = None
    FIREBASE_SDK_AVAILABLE = False


_init_lock = threading.Lock()
_app = None
_init_failed = False


class FirebaseAuthError(Exception):
    """Raised when an ID token cannot be trusted."""


def _credential_source():
    """Locate service-account credentials, by file path or inline JSON."""
    path = os.environ.get('FIREBASE_CREDENTIALS_FILE', '').strip()
    if path and os.path.isfile(path):
        return credentials.Certificate(path)

    raw = os.environ.get('FIREBASE_CREDENTIALS_JSON', '').strip()
    if raw:
        return credentials.Certificate(json.loads(raw))

    return None


def _get_app():
    """Initialise the Admin SDK once, or return None when unconfigured."""
    global _app, _init_failed

    if _app is not None or _init_failed or not FIREBASE_SDK_AVAILABLE:
        return _app

    with _init_lock:
        if _app is not None or _init_failed:
            return _app
        try:
            source = _credential_source()
            if source is None:
                _init_failed = True
                return None
            _app = firebase_admin.initialize_app(source, name='reveal-x')
        except ValueError:
            # Already initialised elsewhere in this process.
            try:
                _app = firebase_admin.get_app('reveal-x')
            except Exception:
                _init_failed = True
        except Exception as exc:  # pragma: no cover - depends on local config
            print(f'Firebase Admin init failed: {exc}')
            _init_failed = True

    return _app


def firebase_enabled() -> bool:
    """True when the server can actually verify Firebase tokens."""
    return _get_app() is not None


def firebase_client_config() -> dict:
    """Public web-app config handed to the browser.

    These values are not secrets - they identify the project to Google and are
    visible in any Firebase web app. The service-account key stays server-side.
    """
    config = {
        'apiKey': os.environ.get('FIREBASE_API_KEY', '').strip(),
        'authDomain': os.environ.get('FIREBASE_AUTH_DOMAIN', '').strip(),
        'projectId': os.environ.get('FIREBASE_PROJECT_ID', '').strip(),
        'appId': os.environ.get('FIREBASE_APP_ID', '').strip(),
    }
    sender_id = os.environ.get('FIREBASE_MESSAGING_SENDER_ID', '').strip()
    if sender_id:
        config['messagingSenderId'] = sender_id

    # The browser needs all four core values to sign in at all.
    if not all(config[key] for key in ('apiKey', 'authDomain', 'projectId', 'appId')):
        return {}
    return config


def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return its claims.

    Raises FirebaseAuthError when the token is missing, malformed, expired,
    revoked, or signed for a different project.
    """
    if not id_token or not isinstance(id_token, str):
        raise FirebaseAuthError('No sign-in token was supplied')

    app = _get_app()
    if app is None:
        raise FirebaseAuthError('Firebase sign-in is not configured on this server')

    try:
        # check_revoked catches tokens issued before a forced sign-out.
        claims = firebase_auth_sdk.verify_id_token(id_token, app=app, check_revoked=True)
    except Exception as exc:
        raise FirebaseAuthError('Sign-in token was rejected') from exc

    if not claims.get('uid') and not claims.get('sub'):
        raise FirebaseAuthError('Sign-in token carried no account id')

    return claims


def claims_to_identity(claims: dict) -> dict:
    """Pull the fields we store from verified token claims."""
    provider = 'firebase'
    firebase_section = claims.get('firebase') or {}
    if isinstance(firebase_section, dict):
        provider = firebase_section.get('sign_in_provider') or provider

    return {
        'firebase_uid': claims.get('uid') or claims.get('sub'),
        'email': (claims.get('email') or '').strip().lower(),
        'email_verified': bool(claims.get('email_verified')),
        'display_name': (claims.get('name') or '').strip(),
        'provider': provider,
    }
