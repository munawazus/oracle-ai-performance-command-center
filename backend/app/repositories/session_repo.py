from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import ensure_aware, utcnow
from app.models.session import UserSession


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        jti: str,
        user_id: str,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> UserSession:
        session = UserSession(
            id=jti,
            user_id=user_id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get(self, jti: str) -> UserSession | None:
        return await self.db.get(UserSession, jti)

    async def revoke(self, jti: str) -> None:
        session = await self.get(jti)
        if session and session.revoked_at is None:
            session.revoked_at = utcnow()
            await self.db.flush()

    def is_valid(self, session: UserSession | None) -> bool:
        if session is None:
            return False
        if session.revoked_at is not None:
            return False
        return ensure_aware(session.expires_at) > utcnow()
