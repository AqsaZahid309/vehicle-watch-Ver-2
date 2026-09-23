import time
import uuid as _uuid_mod

import redis.asyncio as aioredis

from app.config import get_settings
from app.core.exceptions import RateLimitError

settings = get_settings()

# Sliding window log algorithm using a unique member per request.
#
# Previous bug: ZADD used the timestamp as both score AND member.
# Two requests in the same millisecond would produce the same member,
# causing ZADD to *update* the existing entry instead of adding a new one —
# meaning the counter undercounted and allowed bursts beyond the limit.
#
# Fix: each request generates a UUID4 as the unique member, while the
# timestamp is still the score (used for range-based pruning). This
# guarantees every request occupies its own slot in the sorted set.
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

-- Remove timestamps outside the sliding window
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window * 1000)

local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end

-- Use unique member so same-millisecond requests each occupy their own slot
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window * 1000)
return 1
"""


async def _check(key: str, redis: aioredis.Redis, limit: int, window: int) -> None:
    now_ms = int(time.time() * 1000)
    member = str(_uuid_mod.uuid4())  # unique per request — fixes ZADD collision bug
    result = await redis.eval(RATE_LIMIT_SCRIPT, 1, key, now_ms, window, limit, member)
    if result == 0:
        raise RateLimitError(retry_after=window)


async def check_device_rate_limit(device_id: str, redis: aioredis.Redis) -> None:
    """
    Raises RateLimitError if the device has exceeded its request quota.
    Atomic Lua script prevents race conditions without MULTI/EXEC overhead.
    A batch upload counts once per request, not per reading — offline buffers
    must be able to drain in one go.
    """
    await _check(
        f"ratelimit:device:{device_id}",
        redis,
        settings.rate_limit_requests,
        settings.rate_limit_window_seconds,
    )


async def check_login_rate_limit(client_key: str, redis: aioredis.Redis) -> None:
    """Throttle credential guessing per client IP + email."""
    await _check(
        f"ratelimit:login:{client_key}",
        redis,
        settings.login_rate_limit_requests,
        settings.login_rate_limit_window_seconds,
    )
