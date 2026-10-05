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

To genuinely scale past one process you need a message queue so the workers
share state:

```bash
pip install redis
# app/__init__.py
socketio = SocketIO(app, message_queue='redis://localhost:6379/0', ...)
```

That is a real change with real failure modes, and it is not needed for an FYP
demo or a class-sized deployment. It is written down here so the single-worker
constraint reads as a decision rather than an oversight.

## Transports

`async_mode` is `threading`. WebSocket still works: `python-engineio` requires
`simple-websocket`, which detects `gunicorn.socket` in the WSGI environ and
upgrades the connection. Long-polling is the fallback, and it is the transport
CI asserts on, because the chat has to work even where an upgrade is blocked.

## Environment

| Variable | Purpose |
| --- | --- |
| `PORT` | Listen port (default 5000) |
| `REVEAL_X_SECRET_KEY` | Flask session signing key. **Set this.** Without it a fresh random key is generated per start, so sessions do not survive a restart |
| `REVEAL_X_COOKIE_SECURE` | `true` to mark session cookies Secure. Set it whenever you serve over HTTPS |
| `REVEAL_X_ALLOWED_ORIGINS` | Comma-separated Socket.IO origins. Unset means same-origin only, which is what you want unless the front end is served from elsewhere |
| `REVEALX_TRUST_PROXY` | `true` only when behind a reverse proxy you control, so `X-Forwarded-For` is believed for login throttling |
| `REVEAL_X_FORCE_HTTPS` | `true` to 301-redirect plain HTTP to HTTPS. HSTS is sent whenever `X-Forwarded-Proto: https` arrives, independently of this |
| `REVEALX_ADMIN_USER` / `REVEALX_ADMIN_PASSWORD` | Bootstraps the superadmin on first start |
| `DATABASE_URL` | PostgreSQL DSN. Unset uses SQLite |
| `FIREBASE_*` | Google sign-in — see [FIREBASE_SETUP.md](FIREBASE_SETUP.md) |

### `REVEALX_TRUST_PROXY` and login throttling

Login throttling buckets by client address. Behind a proxy every request appears
to come from the proxy, so one person failing a password would throttle
everyone. `REVEALX_TRUST_PROXY=true` makes the app read `X-Forwarded-For`
instead — but only set it when a proxy you control is in front, because
otherwise a client can spoof that header and bypass its own throttle.

## HTTPS

Terminate TLS at a reverse proxy (nginx, Caddy, your host's load balancer) and
forward to gunicorn. Google sign-in requires `https` or `localhost` — a popup on
a plain-`http` LAN address opens and closes with no result.

Set `REVEAL_X_FORCE_HTTPS=true` to redirect plain HTTP. HSTS is sent whenever the
proxy passes `X-Forwarded-Proto: https`, so make sure it does.

`REVEAL_X_ADHOC_SSL=true` makes `run.py` generate a throwaway certificate. That
is for testing the HTTPS path locally; browsers will warn, and it is not a
deployment option.

## Checklist

- [ ] `REVEAL_X_SECRET_KEY` set to something persistent
- [ ] `REVEAL_X_COOKIE_SECURE=true` if serving HTTPS
- [ ] TLS terminated in front of gunicorn
- [ ] `REVEALX_ADMIN_PASSWORD` set, then rotated after first sign-in
- [ ] Firebase service-account JSON outside the repo
- [ ] `--workers 1` unchanged, unless a message queue was added
