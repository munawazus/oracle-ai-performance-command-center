from redis.asyncio import Redis


class RateLimitService:
    """Fixed-window rate limiting backed by Redis. Complements per-account lockout with a
    per-IP ceiling so credential stuffing across many accounts from one source is also capped."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Increment the counter for `key`. Returns (allowed, current_count)."""
        redis_key = f"ratelimit:{key}"
        count = await self.redis.incr(redis_key)
        if count == 1:
            await self.redis.expire(redis_key, window_seconds)
        return count <= limit, count
