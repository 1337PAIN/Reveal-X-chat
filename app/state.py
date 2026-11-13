"""Process-wide state that is not in the database, behind one interface.

Rate-limit windows, send buckets, the TOTP replay cache and the table of
which socket belongs to which signed-in user used to be bare dicts scattered
through routes.py, models.py and auth/totp.py, some with a lock and some
without. They all live here now, every operation under one lock.

Two implementations:

* ``MemoryStore`` (default): in this process. Correct for the deployment as
  documented -- one worker with many threads.
* ``RedisStore``: selected by ``REVEALX_REDIS_URL``. Shared by every worker,
  which is what running more than one worker requires: otherwise each worker
  has its own login throttle (so N workers allow N times the attempts), its
  own replay cache (so a TOTP code can be used once per worker) and its own
  idea of who is online. Multi-step updates use WATCH/MULTI, so they stay
  atomic across workers without server-side scripting.

What stays per process on purpose: half-finished TOTP sign-ins and
enrolments, which are keyed by socket id. A socket lives on one worker (the
load balancer must be sticky, see docs/DEPLOYMENT.md), so nothing else could
ever look them up.
"""

import json
import os
import threading
import time
import uuid


class MemoryStore:
    kind = 'memory'

    def __init__(self):
        self.lock = threading.RLock()
        self.windows = {}        # namespace -> {key: [timestamps]}
        self.buckets = {}        # key -> (tokens, last refill)
        self.claims = {}         # key -> expiry time
        self.sessions = {}       # sid -> user dict
        self.user_sessions = {}  # user id -> set(sid)

    # -------------------------------------------------- sliding windows

    def window(self, namespace):
        """The raw {key: [timestamps]} dict for a namespace (memory only)."""
        with self.lock:
            return self.windows.setdefault(namespace, {})

    def _hits(self, namespace, key, span, now):
        table = self.window(namespace)
        hits = [t for t in table.get(key, []) if now - t < span]
        table[key] = hits
        return hits

    def window_wait(self, namespace, keys_limits, span, now):
        """Seconds until every key is under its limit again, or 0."""
        with self.lock:
            for key, limit in keys_limits:
                hits = self._hits(namespace, key, span, now)
                if len(hits) >= limit:
                    return int(span - (now - hits[0])) + 1
            return 0

    def window_reserve(self, namespace, keys_limits, span, now):
        """Check every key and, if all are under their limits, count this
        attempt against all of them -- as one step. Returns the wait, 0 if
        reserved."""
        with self.lock:
            wait = self.window_wait(namespace, keys_limits, span, now)
            if wait:
                return wait
            for key, _limit in keys_limits:
                self.window(namespace).setdefault(key, []).append(now)
            self._sweep(namespace, span, now)
            return 0

    def window_remove(self, namespace, keys, stamp):
        with self.lock:
            table = self.window(namespace)
            for key in keys:
                hits = table.get(key)
                if hits and stamp in hits:
                    hits.remove(stamp)

    def window_clear(self, namespace, keys=None):
        with self.lock:
            table = self.window(namespace)
            if keys is None:
                table.clear()
            for key in keys or ():
                table.pop(key, None)

    def _sweep(self, namespace, span, now):
        # The tables are only appended to, so drop keys that have aged out
        # rather than let them grow for ever.
        table = self.window(namespace)
        if len(table) > 2048:
            for key in list(table):
                table[key] = [t for t in table[key] if now - t < span]
                if not table[key]:
                    del table[key]

    # ---------------------------------------------------- token buckets

    def take_token(self, key, capacity, rate, now):
        with self.lock:
            tokens, last = self.buckets.get(key, (float(capacity), now))
            tokens = min(float(capacity), tokens + (now - last) * rate)
            if tokens < 1:
                self.buckets[key] = (tokens, now)
                return False
            self.buckets[key] = (tokens - 1, now)
            if len(self.buckets) > 4096:
                for stale in [k for k, (n, t) in self.buckets.items() if now - t > 3600]:
                    del self.buckets[stale]
            return True

    # ----------------------------------------------------- one-time use

    def claim_once(self, key, ttl, now=None):
        """True the first time `key` is claimed within `ttl` seconds."""
        now = time.time() if now is None else now
        with self.lock:
            for stale in [k for k, expiry in self.claims.items() if expiry <= now]:
                del self.claims[stale]
            if key in self.claims:
                return False
            self.claims[key] = now + ttl
            return True

    def clear_claims(self):
        with self.lock:
            self.claims.clear()

    # --------------------------------------------------------- sessions

    def session_add(self, sid, user):
        with self.lock:
            self.session_remove(sid)
            self.sessions[sid] = user
            self.user_sessions.setdefault(user['id'], set()).add(sid)

    def session_remove(self, sid):
        """Drop a socket. Returns (user, was_last_session_for_user)."""
        with self.lock:
            user = self.sessions.pop(sid, None)
            if not user:
                return None, False
            sids = self.user_sessions.get(user['id'])
            last = False
            if sids is not None:
                sids.discard(sid)
                if not sids:
                    del self.user_sessions[user['id']]
                    last = True
            return user, last

    def session_get(self, sid):
        with self.lock:
            return self.sessions.get(sid)

    def user_sids(self, user_id):
        with self.lock:
            return list(self.user_sessions.get(user_id, ()))

    def all_sessions(self):
        with self.lock:
            return list(self.sessions.items())

    def online_user_ids(self):
        with self.lock:
            return set(self.user_sessions)

    def session_update_user(self, user_id, **fields):
        with self.lock:
            for sid in self.user_sessions.get(user_id, ()):
                self.sessions[sid].update(fields)

    def sessions_reset(self):
        with self.lock:
            self.sessions = {}
            self.user_sessions = {}


class RedisStore:
    """The same operations, shared between workers through Redis."""

    kind = 'redis'
    PREFIX = 'revealx:'

    def __init__(self, client):
        self.r = client

    def _k(self, *parts):
        return self.PREFIX + ':'.join(str(p) for p in parts)

    def _wkey(self, namespace, key):
        return self._k('w', namespace, json.dumps(key, separators=(',', ':')))

    def _transaction(self, watch, body):
        """Run body(pipe) under WATCH, retrying when another worker interferes."""
        from redis.exceptions import WatchError
        while True:
            with self.r.pipeline() as pipe:
                try:
                    pipe.watch(*watch)
                    return body(pipe)
                except WatchError:
                    continue

    # -------------------------------------------------- sliding windows

    def _wait_in(self, conn, namespace, keys_limits, span, now):
        # Read-only on purpose: under WATCH, writing a watched key before
        # MULTI would abort this connection's own transaction. Old entries
        # are pruned inside the transaction instead.
        for key, limit in keys_limits:
            name = self._wkey(namespace, key)
            if conn.zcount(name, now - span, '+inf') >= limit:
                oldest = conn.zrangebyscore(name, now - span, '+inf', start=0, num=1,
                                            withscores=True)[0][1]
                return int(span - (now - oldest)) + 1
        return 0

    def window_wait(self, namespace, keys_limits, span, now):
        return self._wait_in(self.r, namespace, keys_limits, span, now)

    def window_reserve(self, namespace, keys_limits, span, now):
        names = [self._wkey(namespace, key) for key, _ in keys_limits]

        def body(pipe):
            wait = self._wait_in(pipe, namespace, keys_limits, span, now)
            if wait:
                pipe.unwatch()
                return wait
            pipe.multi()
            for name in names:
                pipe.zremrangebyscore(name, '-inf', now - span)
                pipe.zadd(name, {f'{now!r}|{uuid.uuid4().hex}': now})
                pipe.expire(name, int(span) + 60)
            pipe.execute()
            return 0
        return self._transaction(names, body)

    def window_remove(self, namespace, keys, stamp):
        for key in keys:
            name = self._wkey(namespace, key)
            members = self.r.zrangebyscore(name, stamp, stamp)
            if members:
                self.r.zrem(name, members[0])

    def window_clear(self, namespace, keys=None):
        if keys is None:
            names = list(self.r.scan_iter(self._k('w', namespace, '*')))
        else:
            names = [self._wkey(namespace, key) for key in keys]
        if names:
            self.r.delete(*names)

    # ---------------------------------------------------- token buckets

    def take_token(self, key, capacity, rate, now):
        name = self._k('b', json.dumps(key, separators=(',', ':')))

        def body(pipe):
            stored = pipe.hgetall(name)
            tokens = float(stored.get(b'tokens', capacity)) if stored else float(capacity)
            last = float(stored.get(b'last', now)) if stored else now
            tokens = min(float(capacity), tokens + (now - last) * rate)
            allowed = tokens >= 1
            pipe.multi()
            pipe.hset(name, mapping={'tokens': tokens - 1 if allowed else tokens, 'last': now})
            pipe.expire(name, 3600)
            pipe.execute()
            return allowed
        return self._transaction([name], body)

    # ----------------------------------------------------- one-time use

    def claim_once(self, key, ttl, now=None):
        name = self._k('c', json.dumps(key, separators=(',', ':')))
        return bool(self.r.set(name, 1, nx=True, ex=max(int(ttl), 1)))

    def clear_claims(self):
        names = list(self.r.scan_iter(self._k('c', '*')))
        if names:
            self.r.delete(*names)

    # --------------------------------------------------------- sessions

    def session_add(self, sid, user):
        self.session_remove(sid)
        pipe = self.r.pipeline()
        pipe.hset(self._k('sessions'), sid, json.dumps(user))
        pipe.sadd(self._k('user', user['id']), sid)
        pipe.execute()

    def session_remove(self, sid):
        raw = self.r.hget(self._k('sessions'), sid)
        if not raw:
            return None, False
        user = json.loads(raw)
        pipe = self.r.pipeline()
        pipe.hdel(self._k('sessions'), sid)
        pipe.srem(self._k('user', user['id']), sid)
        pipe.scard(self._k('user', user['id']))
        remaining = pipe.execute()[-1]
        return user, remaining == 0

    def session_get(self, sid):
        raw = self.r.hget(self._k('sessions'), sid)
        return json.loads(raw) if raw else None

    def user_sids(self, user_id):
        return [s.decode() if isinstance(s, bytes) else s
                for s in self.r.smembers(self._k('user', user_id))]

    def all_sessions(self):
        return [(k.decode() if isinstance(k, bytes) else k, json.loads(v))
                for k, v in self.r.hgetall(self._k('sessions')).items()]

    def online_user_ids(self):
        return {user['id'] for _sid, user in self.all_sessions()}

    def session_update_user(self, user_id, **fields):
        for sid in self.user_sids(user_id):
            user = self.session_get(sid)
            if user:
                user.update(fields)
                self.r.hset(self._k('sessions'), sid, json.dumps(user))

    def sessions_reset(self):
        for name in list(self.r.scan_iter(self._k('user', '*'))):
            self.r.delete(name)
        self.r.delete(self._k('sessions'))


_store = None
_store_lock = threading.Lock()


def redis_url():
    return os.environ.get('REVEALX_REDIS_URL', '').strip()


def store():
    """The process's store, chosen from REVEALX_REDIS_URL on first use."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                url = redis_url()
                if url:
                    import redis
                    _store = RedisStore(redis.Redis.from_url(url))
                else:
                    _store = MemoryStore()
    return _store


def use_store(new_store):
    """Replace the store (tests)."""
    global _store
    _store = new_store
    return new_store
