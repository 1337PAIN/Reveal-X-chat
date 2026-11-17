# Deployment

## The short version

```bash
pip install -r requirements.txt
gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 120 \
         --bind 0.0.0.0:5000 app:app
```

That command lives in the `Procfile`, and CI runs it verbatim — it is read out of
that file with `sed`, booted, and asked for a page, a Socket.IO handshake and
`/api/auth/config` on every push. If the deployment recipe breaks, the build goes
red rather than the discovery waiting for a demo.

## Hosting it

Two supported ways in, both running the same gunicorn command:

**Render (one click).** Push to GitHub, then Render → New → Blueprint → pick
this repo. `render.yaml` creates the web service and a Postgres database,
generates `REVEAL_X_SECRET_KEY`, and sets the proxy and HTTPS flags. Nothing in
that file is a secret: the admin and Firebase variables are `sync: false`, so
you fill them in the dashboard after the first deploy.

**Docker (anywhere else).**

```bash
docker build -t reveal-x .
docker run -p 5000:5000 -e REVEAL_X_SECRET_KEY="$(openssl rand -hex 32)" reveal-x
```

CI builds that image on every push, starts it, and asserts it serves the app
shell, completes a Socket.IO handshake, imports the ML stack, and is not running
as root. The Dockerfile's `CMD` and the `Procfile` web command are asserted
identical by `tests/test_deployment.py`, so a platform cannot end up running a
command nobody tested.

### Two things the container does not keep

The filesystem is wiped on every deploy.

- **The database.** On SQLite that means every account disappears when you push.
  The Render blueprint provisions Postgres and wires `DATABASE_URL` for this
  reason. Anywhere else, set `DATABASE_URL` or mount a volume at
  `/app/app/data`.
- **Stored Share 1 images** under `/app/app/shares`. These expire after an hour
  by design, so losing them on a deploy costs only in-flight shares — but a
  reconstruction in progress across a restart will fail. Mount a volume there if
  that matters for your demo.

### Why not Vercel

Vercel's functions are request-scoped and stateless. Flask-SocketIO keeps each
client's session, room membership and pending packets in the worker process, so
a handshake on one invocation and a poll on the next is an unknown session —
the chat would connect and then silently drop messages. This needs a long-lived
process. The same rules out any serverless platform without a hosted Socket.IO
service in front.

The repo did carry a `vercel.json` and a `pyproject.toml` for a while. They were
removed, and not only for the reason above: the `vercel.json` was invalid JSON
(a stray character on the last line, which failed the build before anything else
ran), and the `pyproject.toml` declared one dependency against the fifteen in
`requirements.txt`. Nothing in this project read that file, but build tools and
buildpacks prefer `pyproject.toml` when it exists, so it was a live trap --
install Flask alone, die on the first import, with `requirements.txt` sitting
right there looking correct. `tests/test_deployment.py` now fails on a
`pyproject.toml` that under-declares, so a future one has to be complete or
carry no dependency list at all.

## Running it from a Windows PC, exposed over a tunnel

This is the path to use for a live demo off your own machine. It is not the
same as the Linux deployment above, for two reasons worth knowing before you
start.

**Gunicorn cannot run on Windows.** It imports `fcntl`, which does not exist
there. The `Procfile` and `Dockerfile` are for Linux hosts and CI.

**Waitress works, but costs you WebSocket.** `waitress-serve` is a real
production WSGI server and does run on Windows — the app serves, and Socket.IO
falls back to HTTP long-polling, so the chat works. But `simple-websocket`
cannot get the raw socket out of a waitress WSGI environment, so every client's
upgrade attempt raises:

```
RuntimeError: Cannot obtain socket from WSGI environment.
```

That is logged per attempt and is noise, not breakage.

So for a demo, `python run.py` is the better choice on Windows: WebSocket works,
and the warning in the next section is about scale and robustness, not about a
handful of demo users.

### Do not port-forward. Use a tunnel.

Opening a router port at your PC gives you a plain-HTTP URL on your home IP, and
that breaks something specific: **Google sign-in requires `https` or
`localhost`.** Over `http://<your-ip>:5000` the Firebase popup opens and closes
with no result. Passwords and share tokens would also cross the network in the
clear.

A tunnel solves all of it, costs nothing, needs no router changes, and works
behind CGNAT:

```bash
# Cloudflare, no account needed for a quick demo
cloudflared tunnel --url http://localhost:5000

# or
ngrok http 5000
```

Both hand you an `https://...` URL with a real certificate.

### Starting it

The short way is `scripts/run_windows_server.ps1`. It generates the two keys
that must survive restarts into a git-ignored `.server-secrets.ps1` on its
first run (back that file up off the PC), sets everything below, loads an
optional `firebase.local.ps1` of `$env:FIREBASE_*` lines, and starts
`run.py`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_windows_server.ps1
```

It also sets `REVEALX_BIND_HOST=127.0.0.1`, so the server listens on this PC
only and the tunnel is the one way in. Without it `run.py` listens on every
interface, and with proxy trust on, anyone on the same network could connect
directly and forge the forwarded client address. Done by hand:

```powershell
$env:REVEAL_X_SECRET_KEY   = "<a long random string, kept between runs>"
$env:REVEAL_X_COOKIE_SECURE = "true"   # the tunnel is HTTPS
$env:REVEALX_TRUST_PROXY    = "true"   # see below
$env:REVEALX_BIND_HOST      = "127.0.0.1"   # only the tunnel can connect
$env:REVEALX_CLIENT_IP_HEADER = "CF-Connecting-IP"   # with cloudflared
$env:REVEALX_ADMIN_USER     = "admin"
$env:REVEALX_ADMIN_PASSWORD = "<set this, then change it after first sign-in>"
python run.py
```

`REVEALX_TRUST_PROXY` matters more here than anywhere else. A tunnel is a
reverse proxy, so without it every request arrives from one address — and the
login throttle would lock out every visitor the moment one of them fumbled a
password.

### Before you share the URL

- [ ] Add the tunnel hostname to Firebase → Authentication → Settings →
      **Authorized domains**, or Google sign-in fails with
      `auth/unauthorized-domain`
- [ ] `REVEAL_X_SECRET_KEY` set and unchanged between restarts — otherwise
      shares tagged before a restart verify as tampered, which looks exactly
      like a real detection on the headline feature
- [ ] Admin password set, and changed after the first sign-in
- [ ] Remember the quick-tunnel URL changes each run — the Firebase
      authorized domain has to be updated with it (a named tunnel, below,
      keeps one address)
- [ ] Stop the tunnel when the demo is over. This is your own machine, and the
      app has an upload path and a database on it

### A fixed address on your own domain

A quick tunnel's URL changes every run. A **named tunnel** keeps one hostname,
such as `revealx.qzz.io`, still free, still with no router changes:

1. In the Cloudflare dashboard, **add the domain** (free plan). Cloudflare
   gives two nameservers; set them at the registrar (for a DigitalPlat free
   domain, in its dashboard's nameserver settings) and wait until Cloudflare
   says the domain is active.
2. **Create the tunnel** in the dashboard (Zero Trust → Networks → Tunnels →
   Create a tunnel → Cloudflared). Give it a **public hostname**: the domain,
   service `HTTP`, URL `127.0.0.1:5000`.
3. Copy the tunnel's **token** (the long string after `--token` in the install
   command it shows) into a file named `.tunnel-token` in the project root.
   It is git-ignored; treat it like a password, since anyone holding it can
   serve traffic as this hostname.
4. Start the app with `scripts\run_windows_server.ps1`, then, in a second
   window:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_tunnel.ps1
```

`run_tunnel.ps1` runs the named tunnel when `.tunnel-token` exists and a
quick tunnel when it does not. It passes the token in the environment, not on
the command line, and gives cloudflared this project's own
`scripts/cloudflared-revealx.yml` in both modes, so a `~/.cloudflared/config.yml` belonging
to another tunnel on the same PC is never read or changed. Add the hostname to
Firebase's authorized domains once; it does not change again.

## Why not `python run.py`

`run.py` starts Werkzeug's development server. It needs
`allow_unsafe_werkzeug=True` to start at all under Flask-SocketIO, which is the
library saying, plainly, that this is not a production server. The `Procfile`
used to run it anyway, while `gunicorn` sat in `requirements.txt` referenced by
nothing.

`run.py` remains the right thing for development — it reloads templates, prints
the LAN URL, and guards the port. It is not what should face a network.

## Why exactly one worker

Socket.IO session state lives in the process that accepted the connection.
`python-socketio` keeps the client's session, room membership and pending
packets in memory, so with two workers a client that handshakes against worker A
and then polls against worker B is an unknown session — the symptom is a chat
that connects and then silently drops messages, intermittently, under load only.

Concurrency therefore comes from threads, not processes. `--threads 100` on one
worker handles far more concurrent chat clients than this project will see.

### Running more than one worker

One worker is still the default and still enough for an FYP demo or a
class-sized deployment. To run more, three things have to hold at once:

1. **Shared state.** Set `REVEALX_REDIS_URL` (for example
   `redis://localhost:6379/0`). The login, registration, lab and send limits,
   the TOTP replay cache, and the table of which socket belongs to which user
   then live in Redis (`app/state.py`, `RedisStore`). Without it each worker
   keeps its own: N workers allow N times the login attempts, a TOTP code can
   be used once per worker, and a user is "online" only on the worker their
   socket landed on.
2. **A shared message queue.** The same setting passes `message_queue` to
   Flask-SocketIO, so an emit to a socket held by another worker reaches it.
3. **Sticky sessions.** Socket.IO's polling transport sends a client's
   requests one after another, and every one must reach the worker that holds
   that socket. Configure the load balancer for sticky sessions (nginx
   `ip_hash`, or a cookie), or restrict clients to the WebSocket transport.

Then raise `--workers` in the Procfile command. Two limits remain, stated
plainly:
- Half-finished TOTP sign-ins and enrolments stay per process. With sticky
  sessions that is correct, since only that worker can see the socket.
- If a worker dies, its sessions stay listed in Redis until those users sign
  out or reconnect, so they can appear online for a while.

`tests/test_state.py` holds the Redis store to the same contract as the
in-memory one (against fakeredis), including 30 parallel login attempts
against a limit of 8.

## Transports

`async_mode` is `threading`. WebSocket still works: `python-engineio` requires
`simple-websocket`, which detects `gunicorn.socket` in the WSGI environ and
upgrades the connection. Long-polling is the fallback, and it is the transport
CI asserts on, because the chat has to work even where an upgrade is blocked.

Every dependency is pinned exactly, and `requirements.lock` records the
full resolved tree. Install the lock, not `requirements.txt`, to reproduce
the environment the suite was run in:

```bash
pip install -r requirements.lock
```

## Environment

| Variable | Purpose |
| --- | --- |
| `PORT` | Listen port (default 5000) |
| `REVEAL_X_SECRET_KEY` | Flask session signing key, and the fallback the share-integrity key is derived from. **Set this.** Without it a fresh random key is generated per start, so sessions do not survive a restart — and neither do share HMAC tags, which makes every share stored before the restart report as *tampered* |
| `REVEALX_REDIS_URL` | Unset (default): limits, the TOTP replay cache and the session table are kept in memory, which is right for one worker. Set it to a Redis URL to share them between workers and turn on the Socket.IO message queue; see *Running more than one worker* |
| `REVEALX_SWEEPER` | `off` disables the background retention sweep (every 60 s). Leave it on: expiry otherwise happens only when someone uses the app |
| `REVEALX_LOG_LEVEL` | App log level on stderr (`DEBUG`, `INFO`, `WARNING`, ...). Default `INFO`; `DEBUG` adds a line per socket connect and disconnect. The audit log (stdout, one JSON object per line) is not affected |
| `REVEALX_DATA_KEY` | 32 random bytes, base64. Encrypts TOTP secrets at rest; existing ones are migrated on the next start. **Keep it**: once set, starting without it (or with a different one) is refused, because the stored secrets could no longer be read. Generate with `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"` |
| `REVEALX_SHARE_HMAC_KEY` | Key for the per-share integrity tag. Optional: derived one-way from `REVEAL_X_SECRET_KEY` when unset. Set it explicitly in production so the two secrets can be rotated independently |
| `REVEAL_X_COOKIE_SECURE` | `true` to mark session cookies Secure. Set it whenever you serve over HTTPS |
| `REVEAL_X_ALLOWED_ORIGINS` | Comma-separated Socket.IO origins. Unset means same-origin only, which is what you want unless the front end is served from elsewhere |
| `REVEALX_TRUST_PROXY` | `true` only when behind a reverse proxy you control, so forwarded headers are believed for throttling |
| `REVEALX_PROXY_HOPS` | With trust on: how many trusted proxies append to `X-Forwarded-For`. The address used is that many entries **from the right**. Default `1` |
| `REVEALX_CLIENT_IP_HEADER` | With trust on: a single-value header your edge *overwrites* with the client address (e.g. `True-Client-IP`, `CF-Connecting-IP`). Used in preference to `X-Forwarded-For` when present |
| `REVEAL_X_FORCE_HTTPS` | `true` to redirect plain HTTP to HTTPS (301 for GET/HEAD, 308 otherwise), before the request is handled. Behind a proxy this needs `REVEALX_TRUST_PROXY=true`, or every request looks like plain HTTP and is redirected for ever |
| `REVEALX_ADMIN_USER` / `REVEALX_ADMIN_PASSWORD` | Bootstraps the superadmin on first start |
| `DATABASE_URL` | PostgreSQL DSN. Unset uses SQLite, in WAL mode with a 5-second busy timeout (so expect `reveal_x.db-wal` and `-shm` beside the database; back up all three, or stop the app first) |
| `REVEALX_BIND_HOST` | `run.py` only: the address to listen on. Unset listens on every interface, as development on a LAN expects. Set `127.0.0.1` behind a tunnel on the same machine, so nothing else can reach the port and forge forwarded headers |
| `REVEALX_PG_POOL_MAX` | PostgreSQL only: connections kept in the pool (default 20). Threads beyond that wait up to 30 s for one rather than failing. Keep it under the server's connection limit |
| `REVEALX_GROUPS` | `true` turns on the server-side group conversation events. **Off by default**: there is no client for groups, so a deployment that leaves it unset refuses every `group_*` event |
| `FIREBASE_*` | Google sign-in — see [FIREBASE_SETUP.md](FIREBASE_SETUP.md) |

### `REVEALX_TRUST_PROXY` and login throttling

Login throttling buckets by client address. Behind a proxy every request appears
to come from the proxy, so one person failing a password would throttle
everyone. `REVEALX_TRUST_PROXY=true` makes the app read forwarded headers
instead — but only set it when a proxy you control is in front, because
otherwise a client can spoof them and bypass its own throttle.

Proxies **append** to `X-Forwarded-For`; they do not replace it. A client that
sends `X-Forwarded-For: 1.2.3.4` through one proxy arrives as
`1.2.3.4, <real address>`, so the left-most entry is attacker-chosen. The app
used to read that entry, which let anyone reset every limit by changing one
header. It now counts `REVEALX_PROXY_HOPS` entries in from the right:

| In front of the app | Setting |
| --- | --- |
| One nginx/Caddy you run | `REVEALX_PROXY_HOPS=1` (default) |
| Cloudflare → your nginx | `REVEALX_CLIENT_IP_HEADER=CF-Connecting-IP`, or `REVEALX_PROXY_HOPS=2` |
| Render | `REVEALX_CLIENT_IP_HEADER=True-Client-IP` (set in `render.yaml`) |

If the header has fewer entries than the hop count, the request did not come
through the proxies you described, and `remote_addr` is used instead.

## Upgrading the database

The schema is versioned (`app/migrations.py`). On start the app applies every
numbered migration the database has not yet run, each in its own transaction,
and records it in `schema_version` with its name and time. A database made
before versioning has no `schema_version` table; it replays every step, and
each step finds its work already done and skips it, so an upgrade from any
earlier Reveal-X keeps its accounts and messages
(`tests/test_migrations.py` builds both kinds and upgrades them).
Those upgrade tests are SQLite files; on PostgreSQL, CI applies every
migration to a fresh PostgreSQL 16 database and runs the full suite on it
(the `postgres` job in `.github/workflows/ci.yml`).

Start one process first after an upgrade. Two processes migrating the same
database at the same moment is not guarded against.

## Before a demo or viva

Three things fail quietly on the free tiers, and each looks like a broken app
rather than an expired plan. Check them a few days before, not on the morning.

- [ ] **The database's creation date.** A free Render Postgres database expires
      **30 days** after it is created and is **deleted 14 days** after that,
      along with every account in it. Open Render → the `reveal-x-db` database
      and read its creation date. If the demo falls past day 30, either upgrade
      it to a paid instance, recreate it close to the date (back up first, see
      below), or move `DATABASE_URL` to a host whose free tier does not expire.
- [ ] **The service is awake.** A free Render web service sleeps after a spell
      with no traffic, and the first request then waits while it starts — long
      enough for the Socket.IO connection banner to show. Open the site a few
      minutes before you present.
- [ ] **`REVEAL_X_SECRET_KEY` and `REVEALX_DATA_KEY` are set explicitly.**
      Without the first, each restart mints a new key: sessions end, and every
      share stored before the restart fails its HMAC and reports as *tampered*,
      which looks exactly like a real detection. Without the second, TOTP
      secrets sit unencrypted; and once it is set, changing or removing it makes
      the app refuse to start rather than lose them. `render.yaml` generates the
      first; set the second in the dashboard (the generator command is in
      [Environment](#environment)) and keep a copy somewhere other than Render.

### Backing up and restoring

```bash
DATABASE_URL='postgres://…external URL from Render…' ./scripts/backup_postgres.sh
```

`scripts/backup_postgres.sh` runs `pg_dump -Fc` against `$DATABASE_URL` and
writes `revealx-<UTC date and time>.dump`. Use the database's *external* URL
from a laptop; the internal one resolves only inside Render. To load a dump into
a new database:

```bash
pg_restore --clean --if-exists --no-owner --no-acl            --dbname "$NEW_DATABASE_URL" revealx-20261115-090000Z.dump
```

`--no-owner --no-acl` because the new database's role names differ from the
old one's. Then point `DATABASE_URL` at the new database and keep
`REVEAL_X_SECRET_KEY` and `REVEALX_DATA_KEY` unchanged: the encrypted TOTP
secrets and the share tags in the dump were made with them. Messages need no
care, since they expire within the hour anyway.

## HTTPS

Terminate TLS at a reverse proxy (nginx, Caddy, your host's load balancer) and
forward to gunicorn. Google sign-in requires `https` or `localhost` — a popup on
a plain-`http` LAN address opens and closes with no result.

Set `REVEAL_X_FORCE_HTTPS=true` to redirect plain HTTP. HSTS is sent on HTTPS
requests only. Behind a proxy the app learns that from `X-Forwarded-Proto`, which
it believes **only** with `REVEALX_TRUST_PROXY=true`: otherwise any client could
claim HTTPS. So behind a proxy, set both, and make sure the proxy sends
`X-Forwarded-Proto` (nginx: `proxy_set_header X-Forwarded-Proto $scheme;`).

`REVEAL_X_ADHOC_SSL=true` makes `run.py` generate a throwaway certificate. That
is for testing the HTTPS path locally; browsers will warn, and it is not a
deployment option.

## Checklist

- [ ] `REVEAL_X_SECRET_KEY` set to something persistent
- [ ] `REVEALX_DATA_KEY` set, and a copy kept outside the host
- [ ] On a free database: [Before a demo or viva](#before-a-demo-or-viva)
- [ ] `REVEAL_X_COOKIE_SECURE=true` if serving HTTPS
- [ ] TLS terminated in front of gunicorn
- [ ] `REVEALX_ADMIN_PASSWORD` set, then rotated after first sign-in
- [ ] Firebase service-account JSON outside the repo
- [ ] `--workers 1` unchanged, unless a message queue was added
