from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.exceptions import AuthenticationError, AuthorizationError, RateLimitedError
from app.logging_config import get_logger
from app.models.user import User
from app.redis_client import get_redis
from app.repositories.audit_repo import AuditRepository
from app.repositories.rbac_repo import RbacRepository
from app.repositories.user_repo import UserRepository
from app.security.jwt import DecodedTokenError, decode_token
from app.services.audit_service import AuditService
from app.services.rate_limit_service import RateLimitService

_bearer_scheme = HTTPBearer(auto_error=False)
logger = get_logger(__name__)


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise AuthenticationError("Not authenticated.")
    try:
        payload = decode_token(credentials.credentials, "access")
    except DecodedTokenError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    user = await UserRepository(db).get_by_id(payload.sub)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid or expired token.")
    return user


def require_permission(permission_code: str):
    async def dependency(
        request: Request,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        codes = await RbacRepository(db).get_user_permission_codes(user.id)
        if permission_code not in codes:
            audit = AuditService(AuditRepository(db))
            await audit.record(
                action="authz.denied",
                success=False,
                actor_user_id=user.id,
                actor_email=user.email,
                ip_address=get_client_ip(request),
                metadata={"permission": permission_code, "path": request.url.path},
            )
            raise AuthorizationError("You do not have permission to perform this action.")
        return user

    return dependency


def require_rate_limit(key_prefix: str, *, limit: int, window_seconds: int):
    """A stricter, per-authenticated-user cap for one specific expensive route (AI inference,
    report generation, bulk onboarding, integration test-delivery) - on top of the blanket
    per-IP floor every /api request already gets from RateLimitMiddleware. Keyed by user id
    rather than IP so one user can't exhaust a budget shared with everyone else behind the
    same NAT/corporate proxy, and so the limit tracks the actual actor even if their IP
    changes mid-session."""

    async def dependency(
        request: Request,
        user: User = Depends(get_current_user),
        redis: Redis = Depends(get_redis),
    ) -> None:
        # Fails open on any Redis error, same as RateLimitMiddleware - found by actually
        # running this dependency against an unreachable Redis (Master Plan Phase 29 live
        # smoke test): it had no error handling at all, so a Redis outage turned every
        # AI/report/bulk/integration-test request into a 500 instead of just skipping the
        # rate-limit check. A rate limiter must never become a full outage of the route it
        # was only meant to throttle.
        try:
            limiter = RateLimitService(redis)
            allowed, _count = await limiter.hit(f"{key_prefix}:{user.id}", limit=limit, window_seconds=window_seconds)
        except Exception:
            logger.error("rate_limit_check_failed_failing_open", path=request.url.path)
            allowed = True
        if not allowed:
            raise RateLimitedError(
                "Rate limit exceeded for this action. Try again shortly.", retry_after_seconds=window_seconds
            )

    return dependency
