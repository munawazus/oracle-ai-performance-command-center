from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


class AuditRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        action: str,
        success: bool,
        actor_user_id: str | None,
        actor_email: str | None,
        target_type: str | None = None,
        target_id: str | None = None,
        ip_address: str | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            action=action,
            success=success,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            event_metadata=metadata,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_events(
        self, limit: int = 100, offset: int = 0, action_prefix: str | None = None
    ) -> list[AuditEvent]:
        query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit).offset(offset)
        if action_prefix:
            query = query.where(AuditEvent.action.like(f"{action_prefix}%"))
        result = await self.db.execute(query)
        return list(result.scalars().all())
