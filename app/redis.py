from collections.abc import AsyncGenerator

import redis.asyncio as aioredis

from app.config import get_settings

settings = get_settings()

# Two separate connection pools:
#
# _redis_text_pool  — decode_responses=True
#   Used for everything that stores plain text: rate-limit sorted sets,
#   device-status JSON cache, health checks. Responses are auto-decoded to str.
#
# _redis_binary_pool — decode_responses=False
#   Used exclusively by AnomalyService to pickle/unpickle sklearn model bundles.
#   With decode_responses=True the Redis client decodes every response as UTF-8,
#   which corrupts arbitrary binary data and causes unpickling errors.

_redis_text_pool:   aioredis.Redis | None = None
_redis_binary_pool: aioredis.Redis | None = None


async def init_redis() -> None:
    global _redis_text_pool, _redis_binary_pool
    _redis_text_pool = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )
    _redis_binary_pool = aioredis.from_url(
        settings.redis_url,
        decode_responses=False,
        max_connections=10,
    )


async def close_redis() -> None:
    global _redis_text_pool, _redis_binary_pool
    if _redis_text_pool:
        await _redis_text_pool.aclose()
        _redis_text_pool = None
    if _redis_binary_pool:
        await _redis_binary_pool.aclose()
        _redis_binary_pool = None


def get_redis_pool() -> aioredis.Redis:
    """Text-decoded pool — for rate limiting, device/telemetry caches, health checks."""
    if _redis_text_pool is None:
        raise RuntimeError("Redis pool not initialized. Call init_redis() at startup.")
    return _redis_text_pool


def get_redis_binary_pool() -> aioredis.Redis:
    """Binary pool — for pickle-serialized sklearn model bundles in AnomalyService."""
    if _redis_binary_pool is None:
        raise RuntimeError("Redis pool not initialized. Call init_redis() at startup.")
    return _redis_binary_pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency — yields the text-decoded pool."""
    yield get_redis_pool()
