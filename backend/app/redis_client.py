from redis.asyncio import Redis, from_url

from app.config import get_settings

_settings = get_settings()
_redis: Redis | None = None

# Every caller of a Redis-backed check in this platform (RateLimitMiddleware,
# require_rate_limit, login's own limiter, /api/ready) is designed to fail open/report
# unreachable rather than block, but that design only pays off if "unreachable" is detected
# quickly - the OS-level default TCP connect timeout can be tens of seconds, which showed up
# live as a multi-second "Checking your session..." stall on every request while Redis was
# down (docs/QA_LIVE_FINDINGS.md, Finding 05). A short, explicit timeout here makes every
# fail-open path actually fail open fast.
_CONNECT_TIMEOUT_SECONDS = 2
_SOCKET_TIMEOUT_SECONDS = 2


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )
    return _redis


async def redis_is_ready(redis: Redis | None = None) -> bool:
    try:
        return bool(await (redis or get_redis()).ping())
    except Exception:
        return False
