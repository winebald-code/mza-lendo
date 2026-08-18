# ═══════════════════════════════════════════════════════════════════════════
#  Mzalendo — Manufacturing Operations & Supply Intelligence Platform
#
#  Multi-stage build. Stage one compiles the stylesheet with Node; stage two
#  is a slim Python runtime that carries no Node at all. The result is a
#  single self-contained image suitable for per-customer isolated instances.
#
#    docker build -t mzalendo:1.0.0 .
#    docker run -p 8000:8000 -e SECRET_KEY=... -v mzalendo-data:/data mzalendo:1.0.0
# ═══════════════════════════════════════════════════════════════════════════

# ── Stage 1: build the stylesheet ──────────────────────────────────────────
FROM node:20-alpine AS assets

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

# Tailwind scans these for class names, so they must be present at build time.
COPY tailwind.config.js ./
COPY static/css/input.css ./static/css/input.css
COPY templates ./templates
COPY static/js ./static/js
COPY app.py ./app.py

RUN npx tailwindcss -i static/css/input.css -o static/css/app.css --minify


# ── Stage 2: runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Mzalendo" \
      org.opencontainers.image.description="Manufacturing operations and supply intelligence for African SMEs, reachable by web dashboard or basic phone over USSD and SMS." \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    DATA_DIR=/data

WORKDIR /app

# curl is here only for the container healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-postgres.txt ./
RUN pip install --upgrade pip && pip install -r requirements-postgres.txt

COPY app.py gunicorn.conf.py docker-entrypoint.py ./
COPY templates ./templates
COPY static ./static
COPY --from=assets /build/static/css/app.css ./static/css/app.css

# The application user exists, but privileges are dropped at *runtime* by the
# entrypoint rather than with a USER directive here. Platform volumes are
# mounted owned by root, so an image that has already dropped privileges cannot
# write to its own database directory.
#
# There is deliberately no VOLUME instruction: Railway rejects it, and it buys
# nothing — persistence comes from the platform volume (or `docker run -v`)
# mounted at /data.
RUN useradd --create-home --uid 10001 mzalendo \
 && mkdir -p /data \
 && chown -R mzalendo:mzalendo /app /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/healthz" || exit 1

# No $PORT in the command: gunicorn.conf.py reads it from the environment, so
# this works whether or not the platform runs it through a shell.
ENTRYPOINT ["python3", "/app/docker-entrypoint.py"]
CMD ["gunicorn", "app:app", "-c", "/app/gunicorn.conf.py"]
