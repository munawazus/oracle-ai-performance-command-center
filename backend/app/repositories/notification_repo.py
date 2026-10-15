from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.notification import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RETRYING,
    STATUS_SENT,
    NotificationEvent,
    NotificationSettings,
)


class NotificationSettingsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create(self) -> NotificationSettings:
        result = await self.db.execute(select(NotificationSettings).limit(1))
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = NotificationSettings()
            self.db.add(settings)
            await self.db.flush()
        return settings

    async def update(self, **fields) -> NotificationSettings:
        settings = await self.get_or_create()
        for key, value in fields.items():
            if value is not None:
                setattr(settings, key, value)
        await self.db.flush()
        return settings


class NotificationEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def exists_for_dedupe_key(self, dedupe_key: str) -> bool:
        result = await self.db.execute(select(NotificationEvent.id).where(NotificationEvent.dedupe_key == dedupe_key))
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        *,
        incident_id: str,
        dedupe_key: str,
        severity: str,
        recipients: list[str],
        subject: str,
        body_text: str,
    ) -> NotificationEvent:
        event = NotificationEvent(
            incident_id=incident_id,
            dedupe_key=dedupe_key,
            severity=severity,
            recipients=recipients,
            subject=subject,
            body_text=body_text,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_pending_or_retrying(self, limit: int = 50) -> list[NotificationEvent]:
        result = await self.db.execute(
            select(NotificationEvent)
            .where(NotificationEvent.status.in_([STATUS_PENDING, STATUS_RETRYING]))
            .order_by(NotificationEvent.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_sent(self, event_id: str) -> None:
        event = await self.db.get(NotificationEvent, event_id)
        if event is None:
            return
        event.status = STATUS_SENT
        event.sent_at = utcnow()
        event.last_attempt_at = utcnow()
        event.attempt_count += 1
        event.last_error_category = None
        await self.db.flush()

    async def mark_failed(self, event_id: str, *, category: str, exhausted: bool) -> None:
        event = await self.db.get(NotificationEvent, event_id)
        if event is None:
            return
        event.attempt_count += 1
        event.last_attempt_at = utcnow()
        event.last_error_category = category
        event.status = STATUS_FAILED if exhausted else STATUS_RETRYING
        await self.db.flush()

    async def list_for_incident(self, incident_id: str) -> list[NotificationEvent]:
        result = await self.db.execute(
            select(NotificationEvent)
            .where(NotificationEvent.incident_id == incident_id)
            .order_by(NotificationEvent.created_at)
        )
        return list(result.scalars().all())

    async def list_recent(self, limit: int = 100) -> list[NotificationEvent]:
        result = await self.db.execute(select(NotificationEvent).order_by(NotificationEvent.created_at.desc()).limit(limit))
        return list(result.scalars().all())
