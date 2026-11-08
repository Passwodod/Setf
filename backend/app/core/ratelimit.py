from fastapi import HTTPException, Request, status

from app.core.redis import get_redis


async def enforce_rate_limit(bucket: str, key: str, limit: int, window_seconds: int = 60) -> None:
    """Fixed-window counter in Redis. Fails closed if Redis is unreachable."""
    redis_key = f"rl:{bucket}:{key}"
    try:
        r = get_redis()
        count = await r.incr(redis_key)
        if count == 1:
            await r.expire(redis_key, window_seconds)
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "rate limiter unavailable") from exc
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many requests")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
