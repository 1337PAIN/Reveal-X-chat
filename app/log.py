"""Logging for Reveal-X: an app log for diagnostics and a separate audit log.

Everything used to be print(). That gave no levels (a failed sign-in and a
socket connecting looked the same), timestamps on only some lines, and no way
to turn the per-connection chatter down in production or to send the audit
trail somewhere of its own.

* ``log`` (``revealx``): human-readable, to stderr, level from REVEALX_LOG_LEVEL
  (default INFO).
* ``audit()`` (``revealx.audit``): one JSON object per line, to stdout, for
  administrative actions. JSON rather than "key=value" text because a value
  containing a space or a newline could make one text entry read as two; json
  escapes both, so an entry cannot be forged from inside a field.

Both write to whatever sys.stdout / sys.stderr are *at the time of writing*,
not when the handler was made, so a process (or a test) that replaces them
still gets the output.
"""

import json
import logging
import os
import sys

from app.timeutil import utcnow


class _LiveStream(logging.StreamHandler):
    """A StreamHandler bound to the current sys.stdout or sys.stderr."""

    def __init__(self, name):
        super().__init__()
        self._name = name

    @property
    def stream(self):
        return getattr(sys, self._name)

    @stream.setter
    def stream(self, _value):
        pass


def _build_app_logger():
    logger = logging.getLogger('revealx')
    if not getattr(logger, '_revealx_configured', False):
        handler = _LiveStream('stderr')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        logger.addHandler(handler)
        level = os.environ.get('REVEALX_LOG_LEVEL', 'INFO').upper()
        logger.setLevel(getattr(logging, level, logging.INFO))
        # Its own handler, so a host that also configures the root logger
        # (gunicorn does) does not print every line twice.
        logger.propagate = False
        logger._revealx_configured = True
    return logger


def _build_audit_logger():
    logger = logging.getLogger('revealx.audit')
    if not getattr(logger, '_revealx_configured', False):
        handler = _LiveStream('stdout')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._revealx_configured = True
    return logger


log = _build_app_logger()
_audit_log = _build_audit_logger()


def audit(actor, action, target='', detail=''):
    """Record an administrative action as one JSON line. Never pass secrets."""
    entry = {
        'type': 'audit',
        'ts': utcnow().isoformat(timespec='seconds') + 'Z',
        'actor': actor,
        'action': action,
    }
    if target:
        entry['target'] = target
    if detail:
        entry['detail'] = detail
    _audit_log.info(json.dumps(entry, sort_keys=True, ensure_ascii=True))
