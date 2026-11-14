"""The E2EE v3 fields every encrypted test payload needs.

sent_at is the sender's clock in integer milliseconds, and the server refuses
one more than five minutes from its own, so it is read fresh for each payload
rather than fixed at import.
"""

import time


def now_ms():
    return int(time.time() * 1000)
