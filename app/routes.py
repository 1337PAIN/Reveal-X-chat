"""Compatibility module: the handlers now live in app/handlers/.

routes.py was a single 2,400-line file. It is split by concern -- http, auth,
admin, settings, messaging, shares, groups, and the common helpers they
share. Importing this module still registers every handler, and every name
the old module had is re-exported here, so `from app import routes` and
`routes.<name>` keep working. To change behaviour, or to monkeypatch a name a
handler looks up, go to the module that defines it.
"""

from app.handlers import common, http, auth, admin, settings, messaging, shares, groups  # noqa: F401
from app.handlers.common import *  # noqa: F401,F403
from app.handlers.http import *  # noqa: F401,F403
from app.handlers.auth import *  # noqa: F401,F403
from app.handlers.admin import *  # noqa: F401,F403
from app.handlers.settings import *  # noqa: F401,F403
from app.handlers.messaging import *  # noqa: F401,F403
from app.handlers.shares import *  # noqa: F401,F403
from app.handlers.groups import *  # noqa: F401,F403
