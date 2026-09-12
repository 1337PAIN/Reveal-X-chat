# Socket.IO keeps per-client session state in the worker process, so this has to
# stay at --workers 1: with more, a client's polling requests land on different
# processes and the session is lost. Scaling out needs a Redis message queue --
# see docs/DEPLOYMENT.md. Concurrency comes from --threads instead.
web: gunicorn --worker-class gthread --workers 1 --threads 100 --timeout 120 --bind 0.0.0.0:${PORT:-5000} app:app
