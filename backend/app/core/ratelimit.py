"""Redis fixed-window rate limiting (AD-19, docs/SECURITY.md §13).

A fixed window, not a sliding one: two `INCR`s and an `EXPIRE`, which is cheap
enough to sit in front of every request. The known cost is burst tolerance at a
window boundary — up to 2x the budget across two adjacent windows. For the
budgets in docs/SECURITY.md §13 that is acceptable; the limits exist to stop
brute force and restart storms, not to meter billing.

The asymmetry in :func:`enforce` is deliberate and specified: **privileged
classes fail closed** when Redis is unavailable, read classes fail open with a
warning. An authentication endpoint that stops rate-limiting itself because the
counter store blinked is a brute-force window.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import status
from redis.exceptions import RedisError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_KEY_PREFIX = "dcp:ratelimit:"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Retry later."

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds

    def response_headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after_seconds)}


@dataclass(frozen=True, slots=True)
class Verdict:
    allowed: bool
    remaining: int
    reset_seconds: int


async def check(bucket: str, identity: str, *, limit: int, window_seconds: int) -> Verdict:
    """Consume one unit from ``(bucket, identity)``.

    Raises ``RedisError`` if the counter store is unreachable — the caller
    decides whether that means deny or allow, because the answer differs by
    endpoint class.
    """
    client = get_redis()
    key = f"{_KEY_PREFIX}{bucket}:{identity}"
    async with client.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        # NX so a long-running window is not extended by every request in it.
        pipe.expire(key, window_seconds, nx=True)
        pipe.ttl(key)
        count, _, ttl = await pipe.execute()
    reset = int(ttl) if int(ttl) > 0 else window_seconds
    return Verdict(
        allowed=int(count) <= limit,
        remaining=max(0, limit - int(count)),
        reset_seconds=reset,
    )


async def enforce(
    bucket: str,
    identity: str,
    *,
    limit: int,
    window_seconds: int,
    fail_closed: bool,
) -> Verdict:
    """Apply a limit, raising :class:`RateLimitedError` when it is exceeded."""
    try:
        verdict = await check(bucket, identity, limit=limit, window_seconds=window_seconds)
    except RedisError as error:
        # Never the identity: for /auth/* it is a client IP, and this line is
        # emitted on a path that is otherwise unauthenticated.
        logger.warning(
            "rate_limit_backend_unavailable",
            bucket=bucket,
            fail_closed=fail_closed,
            error=str(error),
        )
        if fail_closed:
            raise RateLimitedError(retry_after_seconds=window_seconds) from error
        return Verdict(allowed=True, remaining=-1, reset_seconds=window_seconds)
    if not verdict.allowed:
        logger.info("rate_limit_exceeded", bucket=bucket)
        raise RateLimitedError(retry_after_seconds=verdict.reset_seconds)
    return verdict
