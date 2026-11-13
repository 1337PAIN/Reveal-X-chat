"""The one place app code reads the clock.

datetime.utcnow() is deprecated (Python 3.12) because it returns a naive
datetime that only means UTC by convention. This reads an aware UTC time and
then drops the zone, on purpose: every timestamp already stored is a naive
ISO 8601 string, and expiry checks compare those strings directly, so the
format must not gain a "+00:00" suffix. The value is the same; only the call
is no longer deprecated.
"""

from datetime import datetime, timezone


def utcnow():
    """Current UTC time as a naive datetime, matching the stored format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
