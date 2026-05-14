# syntax=docker/dockerfile:1
# ── CityMapFrames API ───────────────────────────────────────────────────────
# Runs the FastAPI/uvicorn server (scripts/server.py).
# The frontend (web/) is built separately and served by nginx as static files.
#
# Build:   docker build -t citymapframes-api .
# Run:     docker compose up -d          (see docker-compose.yml)
# ────────────────────────────────────────────────────────────────────────────

FROM python:3.14-slim

# --- system deps ------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast pip replacement — avoids heavy build-step inside venv)
RUN pip install --no-cache-dir uv

# --- Python deps ------------------------------------------------------------
WORKDIR /app

# Copy the dependency manifest first so Docker can cache this layer.
COPY scripts/pyproject.toml ./pyproject.toml

# Install exactly what pyproject.toml declares, system-wide (no venv needed).
RUN uv pip install --system --no-cache-dir \
    "fastapi>=0.136.1" \
    "orjson>=3.11.9" \
    "requests>=2.33.1" \
    "uvicorn[standard]>=0.46.0"

# --- application source -----------------------------------------------------
# Only the files actually imported by the server chain.
# discover.py / ref_bbox.py are CLI-only tools; leave them out.
COPY scripts/server.py \
     scripts/db.py \
     scripts/presets_db.py \
     scripts/fetch.py \
     scripts/geocode.py \
     scripts/layers.json \
     ./

# --- runtime config ---------------------------------------------------------
# SQLite databases live on a named volume so they survive container restarts
# and image upgrades.  Override these in docker-compose.yml or with -e flags.
ENV CITYMAPFRAMES_CACHE_DB=/data/cache.db
ENV CITYMAPFRAMES_PRESETS_DB=/data/presets.db

# CORS: set to your frontend origin in production, e.g.
#   CORS_ORIGINS=https://citymapframes.example.com
# Leave blank (or *) for open CORS during development.
ENV CORS_ORIGINS=*

VOLUME /data
EXPOSE 8000

# Single worker — SQLite WAL handles concurrent reads fine; multiple uvicorn
# workers would each have their own in-memory coalescing tables, which is
# wasteful and slightly incorrect. Scale horizontally (multiple containers +
# a shared SQLite via NFS or migrate to Postgres) if you ever need it.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
