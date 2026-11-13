"""Request and Socket.IO handlers, one module per concern.

Importing this package registers every route and event. common holds what
the others share; the order below only matters in that common comes first.
"""

from app.handlers import common  # noqa: F401
from app.handlers import http, auth, admin, settings, messaging, shares, groups  # noqa: F401,E402
