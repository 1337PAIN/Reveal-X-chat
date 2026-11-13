"""The shared-state store, in memory and in Redis, held to one contract.

Every case runs against both implementations: MemoryStore (one worker, the
default) and RedisStore over fakeredis (what more than one worker needs). A
behaviour that only one of them had would be a throttle or a replay check
that changed meaning the day someone added a worker.
"""

import threading

import fakeredis
import pytest

from app import state


@pytest.fixture(params=['memory', 'redis'])
def store(request):
    if request.param == 'memory':
        return state.MemoryStore()
    return state.RedisStore(fakeredis.FakeRedis())


def test_a_window_counts_and_refuses_past_its_limit(store):
    keys = [(('account', 'alice'), 3)]
    for i in range(3):
        assert store.window_reserve('login', keys, 900, 1000.0 + i) == 0
    wait = store.window_reserve('login', keys, 900, 1003.0)
    assert 0 < wait <= 900
    assert store.window_wait('login', keys, 900, 1003.0) == wait


def test_a_window_forgets_entries_older_than_its_span(store):
    keys = [(('account', 'alice'), 1)]
    assert store.window_reserve('login', keys, 60, 1000.0) == 0
    assert store.window_reserve('login', keys, 60, 1030.0) > 0
    assert store.window_reserve('login', keys, 60, 1061.0) == 0


def test_a_reservation_counts_against_every_key_or_none(store):
    both = [(('account', 'alice'), 5), (('address', '1.2.3.4'), 1)]
    assert store.window_reserve('login', both, 900, 1000.0) == 0
    # The address is full, so the account must not be charged either.
    assert store.window_reserve('login', both, 900, 1001.0) > 0
    assert store.window_wait('login', [(('account', 'alice'), 2)], 900, 1001.0) == 0


def test_a_reservation_can_be_handed_back(store):
    keys = [(('account', 'alice'), 1)]
    assert store.window_reserve('login', keys, 900, 1000.0) == 0
    store.window_remove('login', [k for k, _ in keys], 1000.0)
    assert store.window_reserve('login', keys, 900, 1001.0) == 0


def test_clearing_a_window(store):
    keys = [(('account', 'alice'), 1)]
    store.window_reserve('login', keys, 900, 1000.0)
    store.window_clear('login', [('account', 'alice')])
    assert store.window_wait('login', keys, 900, 1000.0) == 0


def test_parallel_reservations_never_exceed_the_limit(store):
    keys = [(('account', 'victim'), 8)]
    barrier, results = threading.Barrier(30), []

    def go():
        barrier.wait()
        results.append(store.window_reserve('login', keys, 900, 1000.0))

    threads = [threading.Thread(target=go) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(0) == 8


def test_token_buckets_burst_then_refill(store):
    for _ in range(3):
        assert store.take_token(('u', 'message'), 3, 1.0, 1000.0)
    assert not store.take_token(('u', 'message'), 3, 1.0, 1000.0)
    assert store.take_token(('u', 'message'), 3, 1.0, 1001.5)
    assert store.take_token(('other', 'message'), 3, 1.0, 1000.0), 'per key'


def test_a_claim_succeeds_once(store):
    assert store.claim_once(('alice', '123456'), 90)
    assert not store.claim_once(('alice', '123456'), 90)
    assert store.claim_once(('bob', '123456'), 90)
    store.clear_claims()
    assert store.claim_once(('alice', '123456'), 90)


def test_the_session_registry(store):
    alice = {'id': 'a', 'username': 'alice', 'status': 'online'}
    store.session_add('s1', alice)
    store.session_add('s2', dict(alice))
    assert store.session_get('s1')['username'] == 'alice'
    assert sorted(store.user_sids('a')) == ['s1', 's2']
    assert store.online_user_ids() == {'a'}

    store.session_update_user('a', status='away')
    assert store.session_get('s2')['status'] == 'away'

    user, last = store.session_remove('s1')
    assert user['id'] == 'a' and last is False
    user, last = store.session_remove('s2')
    assert last is True
    assert store.online_user_ids() == set()
    assert store.session_remove('nope') == (None, False)


def test_the_store_is_chosen_from_the_environment(monkeypatch):
    monkeypatch.setattr(state, '_store', None)
    monkeypatch.delenv('REVEALX_REDIS_URL', raising=False)
    assert state.store().kind == 'memory'
    monkeypatch.setattr(state, '_store', None)
    monkeypatch.setenv('REVEALX_REDIS_URL', 'redis://localhost:6379/0')
    assert state.store().kind == 'redis'
    monkeypatch.setattr(state, '_store', None)


def test_redis_also_turns_on_the_socketio_message_queue():
    """Shared state without a shared queue would still lose emits to sockets
    on other workers; the two are switched on by the same setting."""
    import subprocess
    import sys
    code = ("import os; os.environ['REVEALX_REDIS_URL']='redis://localhost:6379/0';"
            "import app; print(app.socketio_options.get('message_queue'))")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                         cwd=str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    assert out.stdout.strip().endswith('redis://localhost:6379/0'), out.stderr[-500:]
