"""
Standalone worker process:

    python -m app.workers.runner

Use this (with RUN_WORKER_IN_API=false on the API) to scale the API to many
uvicorn workers/replicas while background processing runs separately. Several
runners may be started for availability — the Redis cycle lock ensures only
one executes a cycle at a time.
"""

import asyncio
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger("vehiclewatch.worker")


async def main() -> None:
    from app.database import engine
    from app.redis import close_redis, init_redis
    from app.workers.anomaly_worker import start_anomaly_worker

    await init_redis()
    task = asyncio.create_task(start_anomaly_worker())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    try:
        await stop.wait()
    finally:
        logger.info("Worker shutting down")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await close_redis()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
