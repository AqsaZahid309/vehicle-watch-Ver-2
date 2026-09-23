# ── Stage 1: build the web app ────────────────────────────────────────────────
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite.config.ts writes to ../app/static/app
RUN npm run build

# ── Stage 2: API + worker image ───────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
COPY --from=web /app/static/app ./app/static/app

RUN useradd --create-home --uid 1000 vehiclewatch && chown -R vehiclewatch /app
USER vehiclewatch

EXPOSE 8000

# One process can serve the API and run the background worker (RUN_WORKER_IN_API=true,
# the default — right for single-container hosts like Railway). To scale the API, set
# RUN_WORKER_IN_API=false, raise --workers, and run `python -m app.workers.runner`
# separately (see docker-compose.yml). A Redis lock keeps worker cycles single-flight.
CMD ["/bin/sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers"]
