"""Build a send_share payload the way vc-client.js does, for tests that need
an image share to exist but are not about the split itself."""

import base64
import os
import uuid

import cv2
import numpy as np
from e2ee_payload import now_ms


def share1_png_b64(height=48, width=40, seed=11):
    """Share 1 is uniform random bytes, as a single-channel PNG."""
    pixels = np.random.default_rng(seed).integers(0, 256, (height, width), dtype=np.uint8)
    ok, buf = cv2.imencode('.png', pixels)
    assert ok
    return base64.b64encode(buf.tobytes()).decode('ascii')


def share_payload(recipient_id, seed=11):
    return {
        'recipient_id': recipient_id,
        'share1_png': share1_png_b64(seed=seed),
        # Stands in for the AES-GCM ciphertext of the envelope, which the
        # server cannot read and does not inspect.
        'envelope': base64.b64encode(os.urandom(256)).decode('ascii'),
        'iv': base64.b64encode(os.urandom(12)).decode('ascii'),
        'encrypted': True,
        'v': 3, 'sent_at': now_ms(),
        'msg_uuid': str(uuid.uuid4()),
    }
