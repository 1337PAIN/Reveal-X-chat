"""The E2EE v4 fields every encrypted test payload needs.

sent_at is the sender's clock in integer milliseconds, and the server refuses
one more than five minutes from its own, so it is read fresh for each payload
rather than fixed at import. epk is the message's ephemeral public key: a real
P-256 key, base64 SPKI, because the server checks it is a point on the curve.
"""

import base64
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def now_ms():
    return int(time.time() * 1000)


def epk():
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    der = public.public_bytes(serialization.Encoding.DER,
                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode('ascii')
