import asyncio
import logging
import sys
import uuid
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

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.exceptions import register_exception_handlers  # noqa: E402
from app.core.metrics import MetricsMiddleware  # noqa: E402
from app.database import engine  # noqa: E402
from app.redis import init_redis, close_redis, get_redis_pool  # noqa: E402
from app.routers import (  # noqa: E402
    alerts, analytics, auth, devices, fleet, maintenance, ml, notifications, reports, stream,
    telemetry, users,
)
from app.workers.anomaly_worker import start_anomaly_worker  # noqa: E402

VERSION = "2.0.0"
STATIC_DIR = Path(__file__).parent / "static"
SPA_DIR = STATIC_DIR / "app"          # built frontend (frontend/ → npm run build)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("=== VehicleWatch %s starting [env=%s] ===", VERSION, settings.app_env)
    await init_redis()
    logger.info("Redis pools initialised")

    worker_task: asyncio.Task | None = None
    if settings.run_worker_in_api:
        worker_task = asyncio.create_task(start_anomaly_worker())
        logger.info("Background worker running in-process (RUN_WORKER_IN_API=true)")
    else:
        logger.info("Background worker disabled here — run `python -m app.workers.runner`")

    yield

    logger.info("=== Shutdown ===")
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="VehicleWatch",
    description=(
        "Fleet telemetry, predictive maintenance and operations platform: ML anomaly detection, "
        "failure forecasting, work orders, trips & driver scoring, geofencing, fuel analytics, "
        "notifications and reporting."
    ),
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# /health registered FIRST and dependency-free — the platform healthcheck polls it
# during startup, before DB/Redis may be reachable.
@app.get("/health", tags=["Health"])
async def health() -> dict:
    return {"status": "ok", "version": VERSION}


@app.get("/health/deep", tags=["Health"])
async def health_deep() -> dict:
    """Verifies DB and Redis are reachable. For diagnostics, not the platform healthcheck."""
    from sqlalchemy import text

    result: dict = {"status": "ok", "version": VERSION, "services": {}}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        result["services"]["postgres"] = "ok"
    except Exception as exc:
        result["services"]["postgres"] = f"error: {type(exc).__name__}"
        result["status"] = "degraded"
    try:
        await get_redis_pool().ping()
        result["services"]["redis"] = "ok"
    except Exception as exc:
        result["services"]["redis"] = f"error: {type(exc).__name__}"
        result["status"] = "degraded"
    return result


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    if settings.metrics_token:
        if request.headers.get("authorization") != f"Bearer {settings.metrics_token}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Middleware ───────────────────────────────────────────────────────────────
_dev_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins if settings.is_production else _dev_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MetricsMiddleware)


@app.middleware("http")
async def request_id_and_security_headers(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


register_exception_handlers(app)

API_PREFIX = "/api/v1"
for r in (auth, users, devices, telemetry, alerts, analytics, maintenance, fleet,
          notifications, ml, reports, stream):
    app.include_router(r.router, prefix=API_PREFIX)


# ── Frontend (single-page app) ───────────────────────────────────────────────
if (SPA_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=SPA_DIR / "assets"), name="assets")


@app.get("/dashboard", include_in_schema=False)
async def legacy_dashboard() -> RedirectResponse:
    return RedirectResponse("/", status_code=308)


@app.get("/{full_path:path}", include_in_schema=False)
async def spa(full_path: str) -> Response:
    """Serve the SPA for every non-API route so client-side routing and deep links work."""
    if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    candidate = (SPA_DIR / full_path).resolve()
    if full_path and candidate.is_file() and SPA_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = SPA_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return JSONResponse(
        {"detail": "Frontend not built. Run `npm ci && npm run build` in frontend/, or use the API at /docs."},
        status_code=404,
    )
