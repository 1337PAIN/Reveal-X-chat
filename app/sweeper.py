"""Background retention: expire messages, Share 1 files and resume tokens.

Retention used to run only inside requests -- sending a message or opening a
conversation called cleanup_old_messages(). A server nobody touched kept every
expired ciphertext and Share 1 file on disk for as long as it stayed idle,
which is not what "messages expire after an hour" promises. This sweeps every
SWEEP_INTERVAL_SECONDS regardless; the in-request cleanup stays as well, so a
message is never served in the gap between two sweeps.

Started lazily, from the first request or socket connection, rather than at
import: scripts and tools import the app too, and should not leave a thread
behind. REVEALX_SWEEPER=off disables it (the test suite does, and starts its
own when it wants one). One per process; with more than one worker each runs
its own, which is harmless because a sweep is idempotent.
"""

import os
import threading

from app.log import log

SWEEP_INTERVAL_SECONDS = 60

_started = None
_start_lock = threading.Lock()


def sweep_once(room):
    """One pass. Returns how many messages and resume tokens were removed."""
    return {
        'messages': room.cleanup_old_messages(),
        'resume_tokens': room.purge_expired_resume_tokens(),
    }


class Sweeper:
    def __init__(self, room, interval=SWEEP_INTERVAL_SECONDS):
        self.room = room
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name='revealx-sweeper', daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                removed = sweep_once(self.room)
                if any(removed.values()):
                    log.info('Retention sweep removed %(messages)s message(s) and '
                             '%(resume_tokens)s resume token(s)', removed)
            except Exception:
                # A database blip must not end retention for the life of the
                # process; try again next interval.
                log.exception('Retention sweep failed')


def ensure_started(room):
    """Start this process's sweeper once. Returns it, or None when disabled."""
    global _started
    if os.environ.get('REVEALX_SWEEPER', 'on').lower() in ('off', 'false', '0'):
        return None
    if _started is None:
        with _start_lock:
            if _started is None:
                worker = Sweeper(room)
                worker.start()
                _started = worker
    return _started
