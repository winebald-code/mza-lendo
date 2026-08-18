"""
Gunicorn configuration for Mzalendo.

The port is read here, in Python, rather than interpolated into a start command
as $PORT. That is deliberate: a platform may run the start command directly
rather than through a shell, in which case "--bind 0.0.0.0:$PORT" reaches
gunicorn as the literal four characters $PORT and it refuses to start with
"'$PORT' is not a valid port number". Reading os.environ removes the shell from
the path entirely, so the same command works under Docker, Nixpacks, a Procfile
or a bare terminal.

    gunicorn app:app -c gunicorn.conf.py
"""
import multiprocessing
import os


def _int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Railway, Heroku, Fly and Cloud Run all inject PORT. 8000 is the local default.
bind = f"0.0.0.0:{_int('PORT', 8000)}"

# Two workers is a sane default for a small container. Note that Flask-Limiter
# counts per process, so raising this multiplies the effective rate limits —
# see the operational notes in README.md before increasing it.
workers = _int("WEB_CONCURRENCY", 2)
threads = _int("WEB_THREADS", 4)
worker_class = "gthread"

timeout = _int("WEB_TIMEOUT", 60)
graceful_timeout = 30
keepalive = 5

# Log to the container's stdout/stderr so the platform collects them.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")

# Railway terminates TLS at the edge and forwards over the private network.
forwarded_allow_ips = "*"
proxy_allow_ips = "*"

# Do not advertise the server or its version. The application sets its own
# Server header, but Gunicorn writes one at the protocol layer afterwards and
# wins, so it has to be suppressed here rather than in an after_request hook.
from gunicorn.http import wsgi as _gunicorn_wsgi  # noqa: E402


class _QuietResponse(_gunicorn_wsgi.Response):
    def default_headers(self, *args, **kwargs):
        return [
            h for h in super().default_headers(*args, **kwargs)
            if not h.lower().startswith("server:")
        ]


_gunicorn_wsgi.Response = _QuietResponse


max_requests = _int("MAX_REQUESTS", 0)
max_requests_jitter = 50 if max_requests else 0


def on_starting(server):
    server.log.info(
        "Mzalendo starting on %s with %d worker(s), %d thread(s)",
        bind, workers, threads,
    )
