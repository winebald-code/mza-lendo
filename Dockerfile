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
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Mzalendo" \
      org.opencontainers.image.description="Manufacturing operations and supply intelligence for African SMEs, reachable by web dashboard or basic phone over USSD and SMS." \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

# curl is here only for the container healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-postgres.txt ./
RUN pip install --upgrade pip && pip install -r requirements-postgres.txt

COPY app.py ./
COPY templates ./templates
COPY static ./static
COPY --from=assets /build/static/css/app.css ./static/css/app.css

# Run unprivileged. /data is a volume so the SQLite file survives a redeploy.
RUN useradd --create-home --uid 10001 mzalendo \
 && mkdir -p /data \
 && chown -R mzalendo:mzalendo /app /data
USER mzalendo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 60 --access-logfile - --error-logfile -"]
