from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.integration import STATUS_FAILED, STATUS_PENDING, STATUS_RETRYING, STATUS_SENT, Integration, IntegrationDelivery


class IntegrationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Integration]:
        result = await self.db.execute(select(Integration).order_by(Integration.name))
        return list(result.scalars().all())

    async def list_enabled(self) -> list[Integration]:
        result = await self.db.execute(
            select(Integration).where(Integration.enabled.is_(True)).order_by(Integration.name)
        )
        return list(result.scalars().all())

    async def get(self, integration_id: str) -> Integration | None:
        return await self.db.get(Integration, integration_id)

    async def create(
        self,
        *,
        integration_type: str,
        name: str,
        target_url: str,
        secret_ref: str | None,
        enabled: bool,
        trigger_severities: list[str],
        created_by_user_id: str,
    ) -> Integration:
        integration = Integration(
            type=integration_type,
            name=name,
            target_url=target_url,
            secret_ref=secret_ref,
            enabled=enabled,
            trigger_severities=trigger_severities,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(integration)
        await self.db.flush()
        return integration

    async def update(self, integration: Integration, **fields) -> Integration:
        for key, value in fields.items():
            if value is not None:
                setattr(integration, key, value)
        await self.db.flush()
        return integration

    async def delete(self, integration: Integration) -> None:
        await self.db.delete(integration)
        await self.db.flush()


class IntegrationDeliveryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def exists_for_dedupe_key(self, dedupe_key: str) -> bool:
        result = await self.db.execute(
            select(IntegrationDelivery.id).where(IntegrationDelivery.dedupe_key == dedupe_key)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self, *, integration_id: str, incident_id: str, event_type: str, dedupe_key: str, payload: dict
    ) -> IntegrationDelivery:
        delivery = IntegrationDelivery(
            integration_id=integration_id,
            incident_id=incident_id,
            event_type=event_type,
            dedupe_key=dedupe_key,
            payload=payload,
        )
        self.db.add(delivery)
        await self.db.flush()
        return delivery

    async def list_pending_or_retrying(self, limit: int = 50) -> list[IntegrationDelivery]:
        result = await self.db.execute(
            select(IntegrationDelivery)
            .where(IntegrationDelivery.status.in_([STATUS_PENDING, STATUS_RETRYING]))
            .order_by(IntegrationDelivery.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_sent(self, delivery_id: str, *, response_status_code: int, response_snippet: str) -> None:
        delivery = await self.db.get(IntegrationDelivery, delivery_id)
        if delivery is None:
            return
        delivery.status = STATUS_SENT
        delivery.sent_at = utcnow()
        delivery.last_attempt_at = utcnow()
        delivery.attempt_count += 1
        delivery.last_error_category = None
        delivery.response_status_code = response_status_code
        delivery.response_snippet = response_snippet
        await self.db.flush()

    async def mark_failed(self, delivery_id: str, *, category: str, exhausted: bool) -> None:
        delivery = await self.db.get(IntegrationDelivery, delivery_id)
        if delivery is None:
            return
        delivery.attempt_count += 1
        delivery.last_attempt_at = utcnow()
        delivery.last_error_category = category
        delivery.status = STATUS_FAILED if exhausted else STATUS_RETRYING
        await self.db.flush()

    async def list_for_incident(self, incident_id: str) -> list[IntegrationDelivery]:
        result = await self.db.execute(
            select(IntegrationDelivery)
            .where(IntegrationDelivery.incident_id == incident_id)
            .order_by(IntegrationDelivery.created_at)
        )
        return list(result.scalars().all())

    async def list_for_integration(self, integration_id: str, limit: int = 100) -> list[IntegrationDelivery]:
        result = await self.db.execute(
            select(IntegrationDelivery)
            .where(IntegrationDelivery.integration_id == integration_id)
            .order_by(IntegrationDelivery.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent(self, limit: int = 100) -> list[IntegrationDelivery]:
        result = await self.db.execute(
            select(IntegrationDelivery).order_by(IntegrationDelivery.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
