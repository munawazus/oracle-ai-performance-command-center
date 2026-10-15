from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow

STATUS_OPEN = "OPEN"
STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"
STATUS_RESOLVED = "RESOLVED"


class Incident(UUIDPkMixin, Base):
    """Deduplicated/correlated - see app/services/incident_service.py for the correlation
    rule. Never one row per anomaly; multiple anomalies for the same database within the
    correlation window attach as evidence to one incident instead (master plan Sec 7)."""

    __tablename__ = "incidents"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=STATUS_OPEN, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    last_evidence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    assigned_to_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class IncidentEvent(UUIDPkMixin, Base):
    """The incident's timeline - both system-generated (evidence attached, severity
    escalated) and human actions (acknowledged, assigned, resolved)."""

    __tablename__ = "incident_events"

    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
