import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

# ── Logging MUST be configured before any other import that could fail.
# Railway captures stdout only; without force=True a previously configured
# handler (e.g. from a library) would swallow these startup logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)
logger.info("VehicleWatch: module import phase starting")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger.info("VehicleWatch: FastAPI imported OK")

from app.config import get_settings

logger.info("VehicleWatch: config imported OK")

from app.core.exceptions import register_exception_handlers
from app.database import engine
from app.redis import init_redis, close_redis, get_redis_pool
from app.routers import auth, devices, telemetry, alerts, analytics
from app.workers.anomaly_worker import start_anomaly_worker

logger.info("VehicleWatch: all internal imports OK")

STATIC_DIR = Path(__file__).parent / "static"
settings = get_settings()
logger.info(
    "VehicleWatch: settings loaded [env=%s, db_url_prefix=%s]",
    settings.app_env,
    str(settings.database_url)[:30],
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("=== Lifespan startup BEGIN [env=%s] ===", settings.app_env)

    # ── Step 1: Redis ─────────────────────────────────────────────────────────
    logger.info("Lifespan step 1/2 — initialising Redis connection pool...")
    try:
        await init_redis()
        logger.info("Lifespan step 1/2 — Redis OK")
    except Exception as exc:
        logger.error(
            "Lifespan step 1/2 — Redis FAILED: %s", exc, exc_info=True
        )
        raise

    # ── Step 2: Anomaly worker ────────────────────────────────────────────────
    logger.info("Lifespan step 2/2 — starting anomaly detection worker task...")
    try:
        worker_task = asyncio.create_task(start_anomaly_worker())
        logger.info("Lifespan step 2/2 — anomaly worker task created OK")
    except Exception as exc:
        logger.error(
            "Lifespan step 2/2 — anomaly worker FAILED: %s", exc, exc_info=True
        )
        raise

    logger.info("=== Lifespan startup COMPLETE — app is accepting requests ===")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("=== Lifespan shutdown BEGIN ===")
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await close_redis()
    await engine.dispose()
    logger.info("=== Lifespan shutdown COMPLETE ===")


app = FastAPI(
    title="VehicleWatch",
    description="Real-Time Fleet Telemetry Ingestion and ML Anomaly Detection API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── /health registered FIRST — must return 200 regardless of DB/Redis state.
# Railway healthcheck polls this immediately on container start; if it has any
# dependency (DB connect, Redis ping) it will fail during lifespan startup and
# keep the container in a crash loop before the services are even ready.
@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


# ── CORS ─────────────────────────────────────────────────────────────────────
# allow_credentials=True is incompatible with allow_origins=["*"].
# In development we allow localhost origins explicitly.
# In production, set ALLOWED_ORIGINS env var to your frontend domain.
_dev_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins if not settings.is_production else settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

API_PREFIX = "/api/v1"
app.include_router(auth.router,      prefix=API_PREFIX)
app.include_router(devices.router,   prefix=API_PREFIX)
app.include_router(telemetry.router, prefix=API_PREFIX)
app.include_router(alerts.router,    prefix=API_PREFIX)
app.include_router(analytics.router, prefix=API_PREFIX)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "dashboard.html")


# ── Deep health check (separate from the lightweight /health above) ───────────
@app.get("/health/deep", tags=["Health"])
async def health_deep() -> dict:
    """
    Verifies DB and Redis are reachable.
    Use this for manual diagnostics — NOT as the Railway healthcheck path.
    """
    from sqlalchemy import text

    result: dict = {"status": "ok", "version": "1.0.0", "services": {}}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        result["services"]["postgres"] = "ok"
    except Exception as exc:
        result["services"]["postgres"] = f"error: {exc}"
        result["status"] = "degraded"

    try:
        redis = get_redis_pool()
        await redis.ping()
        result["services"]["redis"] = "ok"
    except Exception as exc:
        result["services"]["redis"] = f"error: {exc}"
        result["status"] = "degraded"

    return result
