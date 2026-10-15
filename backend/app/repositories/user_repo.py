from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import ensure_aware, utcnow
from app.models.user import User


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: str) -> User | None:
        return await self.db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 200, offset: int = 0) -> list[User]:
        result = await self.db.execute(select(User).order_by(User.created_at).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def create(self, *, email: str, password_hash: str, display_name: str) -> User:
        user = User(email=email.lower(), password_hash=password_hash, display_name=display_name)
        self.db.add(user)
        await self.db.flush()
        return user

    async def set_active(self, user: User, is_active: bool) -> None:
        user.is_active = is_active
        await self.db.flush()

    async def record_login_success(self, user: User) -> None:
        user.failed_login_count = 0
        user.locked_until = None
        await self.db.flush()

    async def record_login_failure(self, user: User, *, max_attempts: int, lock_until: datetime | None) -> None:
        user.failed_login_count += 1
        if user.failed_login_count >= max_attempts and lock_until is not None:
            user.locked_until = lock_until
        await self.db.flush()

    def is_locked(self, user: User) -> bool:
        return user.locked_until is not None and ensure_aware(user.locked_until) > utcnow()
