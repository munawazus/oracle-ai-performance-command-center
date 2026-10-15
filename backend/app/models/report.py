from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow

REPORT_TYPE_DAILY_DBA = "DAILY_DBA"
REPORT_TYPE_INCIDENT = "INCIDENT"
REPORT_TYPE_PERFORMANCE = "PERFORMANCE"
REPORT_TYPE_SQL_REGRESSION = "SQL_REGRESSION"
REPORT_TYPE_CAPACITY = "CAPACITY"
REPORT_TYPE_HEALTH = "HEALTH"

REPORT_TYPES = (
    REPORT_TYPE_DAILY_DBA,
    REPORT_TYPE_INCIDENT,
    REPORT_TYPE_PERFORMANCE,
    REPORT_TYPE_SQL_REGRESSION,
    REPORT_TYPE_CAPACITY,
    REPORT_TYPE_HEALTH,
)


class Report(UUIDPkMixin, Base):
    """A generated report - persisted so Generate/View/Download are distinct real actions
    (master plan Sec 26 "Reports: generate, view, download, filter") rather than
    recomputed-on-every-view. database_id/incident_id are denormalized alongside their
    human-readable name/title snapshots so a report stays meaningful even if the source
    row is later renamed or removed (ondelete=SET NULL, never CASCADE - a report is an
    audit artifact, not a view over live state)."""

    __tablename__ = "reports"

    report_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    requested_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_email: Mapped[str] = mapped_column(String(320), nullable=False)

    monitored_database_id: Mapped[str | None] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    database_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)

    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    incident_title_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)

    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    data_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    data_completeness_note: Mapped[str] = mapped_column(Text, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
