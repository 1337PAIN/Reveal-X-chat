"""Retention runs on a clock, not only when somebody happens to use the app.

Expired messages and their Share 1 files were removed inside the request path
-- on a send, on opening a conversation. A server nobody touched kept every
expired ciphertext and every Share 1 on disk indefinitely, which is not what
"messages expire after an hour" says. A background sweeper now runs every 60
seconds.
"""

import os
import time
from datetime import timedelta

from app import sweeper
from app.timeutil import utcnow


def expire_everything(store):
    past = (utcnow() - timedelta(hours=3)).isoformat()
    with store._connect() as db:
        db.execute("UPDATE messages SET created_at = ?, expires_at = ?", (past, past))
        db.execute("UPDATE resume_tokens SET expires_at = ?", (past,))


def make_share(store, users):
    alice, bob = users
    name = 'share1_sweeper_test.png'
    path = os.path.join(store.shares_folder, name)
    with open(path, 'wb') as handle:
        handle.write(b'\x89PNG not really')
    store.add_message(alice['id'], bob['id'], 'share', 'x',
                      {'share1_filename': name, 'share1_token': 't', 'share1_hmac': 'h'})
    return path


def test_one_sweep_removes_expired_rows_files_and_tokens(store, users):
    alice, bob = users
    path = make_share(store, users)
    store.issue_resume_token(alice['id'])
    expire_everything(store)

    result = sweeper.sweep_once(store)

    assert result['messages'] == 1 and result['resume_tokens'] == 1
    assert not os.path.exists(path)
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM messages").fetchone()['n'] == 0


def test_the_sweeper_runs_without_any_request(store, users):
    path = make_share(store, users)
    expire_everything(store)
    worker = sweeper.Sweeper(store, interval=0.05)
    worker.start()
    try:
        deadline = time.time() + 3
        while os.path.exists(path) and time.time() < deadline:
            time.sleep(0.02)
    finally:
        worker.stop()
    assert not os.path.exists(path), 'nothing swept while the app sat idle'


def test_a_failing_sweep_does_not_kill_the_sweeper(store, monkeypatch):
    calls = []

    def flaky(room):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('database briefly unavailable')
        return {'messages': 0, 'resume_tokens': 0}

    monkeypatch.setattr(sweeper, 'sweep_once', flaky)
    worker = sweeper.Sweeper(store, interval=0.02)
    worker.start()
    deadline = time.time() + 3
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    worker.stop()
    assert len(calls) >= 3


def test_only_one_sweeper_is_started_per_process(store, monkeypatch):
    monkeypatch.setenv('REVEALX_SWEEPER', 'on')
    monkeypatch.setattr(sweeper, '_started', None)
    started = []
    monkeypatch.setattr(sweeper.Sweeper, 'start', lambda self: started.append(self))
    for _ in range(5):
        sweeper.ensure_started(store)
    assert len(started) == 1


def test_it_can_be_turned_off(store, monkeypatch):
    monkeypatch.setenv('REVEALX_SWEEPER', 'off')
    monkeypatch.setattr(sweeper, '_started', None)
    assert sweeper.ensure_started(store) is None


def test_the_interval_is_a_minute():
    assert sweeper.SWEEP_INTERVAL_SECONDS == 60
