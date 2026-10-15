from fastapi import APIRouter, Cookie, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.exceptions import AuthenticationError, RateLimitedError
from app.logging_config import get_logger
from app.redis_client import get_redis
from app.repositories.audit_repo import AuditRepository
from app.repositories.rbac_repo import RbacRepository
from app.repositories.session_repo import SessionRepository
from app.repositories.user_repo import UserRepository
from app.schemas.auth import AccessTokenResponse, CurrentUserResponse, LoginRequest
from app.security.deps import get_client_ip, get_current_user
from app.security.jwt import DecodedTokenError, decode_token
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService
from app.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

REFRESH_COOKIE_NAME = "refresh_token"


def _build_auth_service(db: AsyncSession) -> AuthService:
    return AuthService(UserRepository(db), SessionRepository(db), AuditService(AuditRepository(db)))


def _set_refresh_cookie(response: Response, token: str, expires_at) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        expires=expires_at,
        path="/",
    )


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> AccessTokenResponse:
    ip = get_client_ip(request)
    # Fails open on any Redis error, same as RateLimitMiddleware (app/security/middleware.py)
    # - found by actually running this route against an unreachable Redis (Master Plan
    # Phase 29 live smoke test): this call had no error handling at all, so a Redis outage
    # turned every login attempt into a 500 instead of just skipping the rate-limit check.
    # A rate limiter must never become a full login outage.
    try:
        limiter = RateLimitService(redis)
        allowed, _count = await limiter.hit(f"login:{ip}", limit=30, window_seconds=300)
    except Exception:
        logger.error("rate_limit_check_failed_failing_open", path="/api/auth/login")
        allowed = True
    if not allowed:
        # 429, not 401 - "too many attempts" is a distinct, more accurate signal than "bad
        # credentials," and carries a Retry-After header a well-behaved client can act on.
        raise RateLimitedError("Too many login attempts. Try again shortly.", retry_after_seconds=300)

    auth_service = _build_auth_service(db)
    tokens, _user = await auth_service.authenticate(
        email=payload.email,
        password=payload.password,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    _set_refresh_cookie(response, tokens.refresh_token, tokens.refresh_expires_at)
    return AccessTokenResponse(access_token=tokens.access_token, expires_at=tokens.access_expires_at)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> AccessTokenResponse:
    if refresh_token is None:
        raise AuthenticationError("No active session.")

    auth_service = _build_auth_service(db)
    tokens, _user = await auth_service.refresh(
        refresh_token, ip_address=get_client_ip(request), user_agent=request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, tokens.refresh_token, tokens.refresh_expires_at)
    return AccessTokenResponse(access_token=tokens.access_token, expires_at=tokens.access_expires_at)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> dict:
    if refresh_token:
        auth_service = _build_auth_service(db)
        actor_user_id = None
        try:
            actor_user_id = decode_token(refresh_token, "refresh").sub
        except DecodedTokenError:
            pass
        await auth_service.logout(refresh_token, actor_user_id=actor_user_id, ip_address=get_client_ip(request))
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me", response_model=CurrentUserResponse)
async def me(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> CurrentUserResponse:
    rbac_repo = RbacRepository(db)
    roles = await rbac_repo.get_user_role_names(user.id)
    permissions = await rbac_repo.get_user_permission_codes(user.id)
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        roles=roles,
        permissions=sorted(permissions),
    )
