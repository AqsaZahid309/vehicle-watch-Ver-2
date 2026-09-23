FROM python:3.11-slim

# Prevents Python from writing pyc files and enables stdout/stderr flushing
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps required by asyncpg, cryptography builds, and healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Single worker required — the anomaly detection background task (asyncio.create_task)
# runs inside the same process. Multiple uvicorn workers each spawn their own event
# loop and would race against each other, causing duplicate alerts and silent crashes.
# ${PORT:-8000} lets Railway inject its dynamic port while keeping 8000 as the local default.
CMD ["/bin/sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
