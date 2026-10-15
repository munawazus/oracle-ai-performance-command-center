from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import Anomaly
from app.models.base import utcnow
from app.models.incident import STATUS_RESOLVED, Incident, IncidentEvent


class IncidentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, *, monitored_database_id: str, severity: str, title: str, opened_at: datetime, last_evidence_at: datetime
    ) -> Incident:
        incident = Incident(
            monitored_database_id=monitored_database_id,
            severity=severity,
            title=title,
            opened_at=opened_at,
            last_evidence_at=last_evidence_at,
        )
        self.db.add(incident)
        await self.db.flush()
        return incident

    async def get(self, incident_id: str) -> Incident | None:
        return await self.db.get(Incident, incident_id)

    async def find_open_within_window(
        self, monitored_database_id: str, window: timedelta, now: datetime
    ) -> Incident | None:
        """The v1 correlation rule: the most recent non-resolved incident for this database,
        as long as it received evidence within `window`. Once RESOLVED, a new anomaly opens
        a fresh incident even inside the window - resolution is a deliberate human signal
        that shouldn't get silently reopened by a coincidentally-timed later anomaly."""
        result = await self.db.execute(
            select(Incident)
            .where(
                Incident.monitored_database_id == monitored_database_id,
                Incident.status != STATUS_RESOLVED,
                Incident.last_evidence_at >= now - window,
            )
            .order_by(Incident.last_evidence_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def update_evidence(self, incident_id: str, *, last_evidence_at: datetime, new_severity: str | None) -> None:
        incident = await self.get(incident_id)
        if incident is None:
            return
        incident.last_evidence_at = last_evidence_at
        if new_severity is not None:
            incident.severity = new_severity
        await self.db.flush()

    async def link_anomaly(self, anomaly_id: str, incident_id: str) -> None:
        anomaly = await self.db.get(Anomaly, anomaly_id)
        if anomaly is not None:
            anomaly.incident_id = incident_id
            await self.db.flush()

    async def acknowledge(self, incident_id: str, actor_user_id: str) -> Incident | None:
        incident = await self.get(incident_id)
        if incident is None:
            return None
        incident.status = "ACKNOWLEDGED"
        incident.acknowledged_at = utcnow()
        incident.acknowledged_by_user_id = actor_user_id
        await self.db.flush()
        return incident

    async def assign(self, incident_id: str, assignee_user_id: str) -> Incident | None:
        incident = await self.get(incident_id)
        if incident is None:
            return None
        incident.assigned_to_user_id = assignee_user_id
        await self.db.flush()
        return incident

    async def resolve(self, incident_id: str, actor_user_id: str, note: str | None) -> Incident | None:
        incident = await self.get(incident_id)
        if incident is None:
            return None
        incident.status = STATUS_RESOLVED
        incident.resolved_at = utcnow()
        incident.resolved_by_user_id = actor_user_id
        incident.resolution_note = note
        await self.db.flush()
        return incident

    async def add_event(
        self, incident_id: str, event_type: str, *, actor_user_id: str | None = None, detail: dict | None = None
    ) -> IncidentEvent:
        event = IncidentEvent(incident_id=incident_id, event_type=event_type, actor_user_id=actor_user_id, detail=detail)
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_events(self, incident_id: str) -> list[IncidentEvent]:
        result = await self.db.execute(
            select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.occurred_at)
        )
        return list(result.scalars().all())

    async def list_for_database(self, monitored_database_id: str, limit: int = 100) -> list[Incident]:
        result = await self.db.execute(
            select(Incident)
            .where(Incident.monitored_database_id == monitored_database_id)
            .order_by(Incident.opened_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_all(
        self, status: str | None = None, limit: int = 200, monitored_database_ids: list[str] | None = None
    ) -> list[Incident]:
        """`monitored_database_ids`, when given, restricts to that set - the fleet
        group/tag filter on the global incident view (Master Plan Phase 22). `None` means
        unfiltered; an empty list is a legitimate "matched no database" result."""
        query = select(Incident).order_by(Incident.opened_at.desc()).limit(limit)
        if status:
            query = query.where(Incident.status == status)
        if monitored_database_ids is not None:
            query = query.where(Incident.monitored_database_id.in_(monitored_database_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())
