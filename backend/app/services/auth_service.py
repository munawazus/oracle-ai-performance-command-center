from datetime import timedelta

from app.config import get_settings
from app.exceptions import AccountLockedError, AuthenticationError
from app.models.base import utcnow
from app.models.user import User
from app.repositories.session_repo import SessionRepository
from app.repositories.user_repo import UserRepository
from app.security.jwt import DecodedTokenError, create_access_token, create_refresh_token, decode_token
from app.security.passwords import verify_password
from app.services.audit_service import AuditService

_GENERIC_LOGIN_ERROR = "Invalid email or password."


class TokenPair:
    def __init__(self, access_token: str, refresh_token: str, access_expires_at, refresh_expires_at):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.access_expires_at = access_expires_at
        self.refresh_expires_at = refresh_expires_at


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        session_repo: SessionRepository,
        audit: AuditService,
    ):
        self.user_repo = user_repo
        self.session_repo = session_repo
        self.audit = audit
        self.settings = get_settings()

    async def _issue_tokens(self, user: User, *, user_agent: str | None, ip_address: str | None) -> TokenPair:
        access_token, access_expires = create_access_token(user.id)
        refresh_token, jti, refresh_expires = create_refresh_token(user.id)
        await self.session_repo.create(
            jti=jti,
            user_id=user.id,
            expires_at=refresh_expires,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        return TokenPair(access_token, refresh_token, access_expires, refresh_expires)

    async def authenticate(
        self, *, email: str, password: str, ip_address: str | None, user_agent: str | None
    ) -> tuple[TokenPair, User]:
        user = await self.user_repo.get_by_email(email)

        if user is None:
            await self.audit.record(
                action="auth.login.failure",
                success=False,
                actor_email=email,
                ip_address=ip_address,
                metadata={"reason": "unknown_email"},
            )
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        if self.user_repo.is_locked(user):
            await self.audit.record(
                action="auth.login.failure",
                success=False,
                actor_user_id=user.id,
                actor_email=user.email,
                ip_address=ip_address,
                metadata={"reason": "account_locked"},
            )
            raise AccountLockedError(
                "Account temporarily locked after repeated failed sign-in attempts. Try again later."
            )

        if not user.is_active:
            await self.audit.record(
                action="auth.login.failure",
                success=False,
                actor_user_id=user.id,
                actor_email=user.email,
                ip_address=ip_address,
                metadata={"reason": "inactive_account"},
            )
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        if not verify_password(password, user.password_hash):
            await self.user_repo.record_login_failure(
                user,
                max_attempts=self.settings.login_max_attempts,
                lock_until=utcnow() + timedelta(minutes=self.settings.login_lockout_minutes),
            )
            await self.audit.record(
                action="auth.login.failure",
                success=False,
                actor_user_id=user.id,
                actor_email=user.email,
                ip_address=ip_address,
                metadata={"reason": "bad_password"},
            )
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        await self.user_repo.record_login_success(user)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        await self.audit.record(
            action="auth.login.success",
            success=True,
            actor_user_id=user.id,
            actor_email=user.email,
            ip_address=ip_address,
        )
        return tokens, user

    async def refresh(
        self, refresh_token: str, *, ip_address: str | None, user_agent: str | None
    ) -> tuple[TokenPair, User]:
        try:
            payload = decode_token(refresh_token, "refresh")
        except DecodedTokenError as exc:
            raise AuthenticationError("Invalid or expired session.") from exc

        session = await self.session_repo.get(payload.jti)
        if not self.session_repo.is_valid(session):
            raise AuthenticationError("Invalid or expired session.")

        user = await self.user_repo.get_by_id(payload.sub)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid or expired session.")

        await self.session_repo.revoke(payload.jti)
        tokens = await self._issue_tokens(user, user_agent=user_agent, ip_address=ip_address)
        return tokens, user

    async def logout(self, refresh_token: str, *, actor_user_id: str | None, ip_address: str | None) -> None:
        try:
            payload = decode_token(refresh_token, "refresh")
            await self.session_repo.revoke(payload.jti)
        except DecodedTokenError:
            pass
        await self.audit.record(
            action="auth.logout",
            success=True,
            actor_user_id=actor_user_id,
            ip_address=ip_address,
        )
