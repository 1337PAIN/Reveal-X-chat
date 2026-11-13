"""How the store opens database connections.

SQLite: a fresh connection per operation is fine, but the default rollback
journal makes readers and the writer block each other, and the server runs
100 threads. WAL lets reads proceed during a write; busy_timeout makes a
writer wait for the lock instead of failing at once with "database is locked".

PostgreSQL: every operation opened a new TCP connection and authenticated,
then closed it. A pool reuses them. psycopg2's ThreadedConnectionPool raises
immediately when it is empty rather than waiting, so a semaphore in front of
it makes threads queue for a connection instead of erroring under load.
"""

import threading
import time

import pytest

from app import models
from app.models import ChatRoom


def test_sqlite_connections_use_wal_and_wait_for_locks(store):
    with store._connect() as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
        assert db.execute('PRAGMA busy_timeout').fetchone()[0] >= 5000


class FakeConn:
    def __init__(self):
        self.closed = 0
        self.commits = self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def cursor(self):
        return self

    def close(self):
        self.closed = 1


class FakePool:
    instances = []

    def __init__(self, minconn, maxconn, dsn, **kwargs):
        self.maxconn = maxconn
        self.out = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.returned = []
        FakePool.instances.append(self)

    def getconn(self):
        with self.lock:
            if self.out >= self.maxconn:
                raise RuntimeError('pool exhausted')  # what psycopg2 does
            self.out += 1
            self.peak = max(self.peak, self.out)
        return FakeConn()

    def putconn(self, conn, close=False):
        with self.lock:
            self.out -= 1
            self.returned.append((conn, close))


@pytest.fixture
def pg_room(monkeypatch):
    FakePool.instances.clear()
    monkeypatch.setattr(models, 'ThreadedConnectionPool', FakePool, raising=False)
    monkeypatch.setenv('REVEALX_PG_POOL_MAX', '4')
    room = object.__new__(ChatRoom)
    room.use_postgres = True
    room.database_url = 'postgresql://example/test'
    room._setup_connection_pool()
    return room


def test_postgres_connections_are_returned_to_the_pool_not_closed(pg_room):
    with pg_room._connect():
        pass
    pool = FakePool.instances[0]
    conn, close = pool.returned[0]
    assert conn.commits == 1 and not close and not conn.closed


def test_a_failed_operation_rolls_back_and_still_returns_the_connection(pg_room):
    with pytest.raises(ValueError):
        with pg_room._connect():
            raise ValueError('boom')
    conn, close = FakePool.instances[0].returned[0]
    assert conn.rollbacks == 1 and not close


def test_threads_queue_for_a_connection_instead_of_failing(pg_room):
    errors, barrier = [], threading.Barrier(12)

    def work():
        barrier.wait()
        try:
            with pg_room._connect():
                time.sleep(0.02)
        except Exception as exc:  # the pool raising would land here
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pool = FakePool.instances[0]
    assert not errors, errors
    assert len(FakePool.instances) == 1, 'one pool per process'
    assert pool.peak <= 4 and len(pool.returned) == 12
