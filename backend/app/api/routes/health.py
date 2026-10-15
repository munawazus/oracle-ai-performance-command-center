from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis

from app.db import db_is_ready
from app.redis_client import get_redis, redis_is_ready

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response, redis: Redis = Depends(get_redis)) -> dict:
    """Readiness: dependencies the API actually needs are reachable."""
    postgres_ok = await db_is_ready()
    redis_ok = await redis_is_ready(redis)
    all_ok = postgres_ok and redis_ok
    response.status_code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if all_ok else "not_ready",
        "dependencies": {
            "postgres": "ok" if postgres_ok else "unreachable",
            "redis": "ok" if redis_ok else "unreachable",
        },
    }
