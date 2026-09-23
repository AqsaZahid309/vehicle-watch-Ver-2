"""
Pre-flight startup check — run this before uvicorn to surface exactly which
component (config, database, redis) is preventing the app from starting.

Usage (Railway startCommand):
    alembic upgrade head && python scripts/check_startup.py && uvicorn ...

Exit codes:
    0 — all checks passed, safe to start uvicorn
    1 — a check failed; details are printed to stdout for Railway to capture
"""

import asyncio
import sys
from pathlib import Path

# Ensure the project root (parent of this script's directory) is on sys.path
# so `from app.xxx import ...` works whether invoked as:
#   python scripts/check_startup.py   (Railway startCommand)
#   python -m scripts.check_startup   (local dev)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


async def main() -> None:
    # ── 1. Config ─────────────────────────────────────────────────────────────
    print("[check] Loading application config...", flush=True)
    try:
        from app.config import get_settings
        settings = get_settings()
        # Truncate URL to avoid leaking credentials in logs
        db_prefix = str(settings.database_url)[:40]
        redis_prefix = str(settings.redis_url)[:30]
        print(f"[check] Config OK — db={db_prefix}... redis={redis_prefix}...", flush=True)
    except Exception as exc:
        print(f"[check] FAIL — config error: {exc}", flush=True)
        sys.exit(1)

    # ── 2. Database ───────────────────────────────────────────────────────────
    print("[check] Testing database connectivity...", flush=True)
    try:
        from sqlalchemy import text
        from app.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("[check] Database OK", flush=True)
    except Exception as exc:
        print(f"[check] FAIL — database error: {exc}", flush=True)
        sys.exit(1)

    # ── 3. Redis ──────────────────────────────────────────────────────────────
    print("[check] Testing Redis connectivity...", flush=True)
    try:
        from app.redis import init_redis, get_redis_pool, close_redis
        await init_redis()
        r = get_redis_pool()
        await r.ping()
        await close_redis()
        print("[check] Redis OK", flush=True)
    except Exception as exc:
        print(f"[check] FAIL — redis error: {exc}", flush=True)
        sys.exit(1)

    # ── 4. Static files ───────────────────────────────────────────────────────
    print("[check] Verifying static files directory...", flush=True)
    try:
        from pathlib import Path
        static_dir = Path(__file__).parent.parent / "app" / "static"
        if not static_dir.exists():
            raise FileNotFoundError(f"Static directory not found: {static_dir}")
        dashboard = static_dir / "dashboard.html"
        if not dashboard.exists():
            raise FileNotFoundError(f"dashboard.html not found in {static_dir}")
        print(f"[check] Static files OK — {static_dir}", flush=True)
    except Exception as exc:
        print(f"[check] FAIL — static files error: {exc}", flush=True)
        sys.exit(1)

    print("[check] All pre-flight checks passed — starting uvicorn", flush=True)


asyncio.run(main())
