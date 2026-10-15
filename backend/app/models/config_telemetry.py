from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class ConfigParameterSample(UUIDPkMixin, Base):
    """Raw non-default V$SYSTEM_PARAMETER facts per collection round (master plan Phase 14,
    Enterprise Readiness Plan D.4) - same "store facts, not derived metrics" convention as
    TablespaceSample: which parameters changed, and how, is computed by
    app/analytics/config_metrics.py at read time, never stored here.

    A full snapshot of the non-default parameter set each round (like TablespaceSample), not
    an incremental sync (like StatspackSnapshot) - V$SYSTEM_PARAMETER has no natural
    high-water-mark column to sync from, so each round simply re-lists whatever is currently
    non-default. Reversion to default is detected as "present in the previous round, absent
    from this one," not as a stored fact of its own.

    parameter_value is Text, not a bounded String, since some values (e.g. control_files,
    service_names) can be long."""

    __tablename__ = "config_parameter_samples"
    __table_args__ = (
        Index("ix_config_parameter_samples_db_sampled", "monitored_database_id", "sampled_at"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    parameter_name: Mapped[str] = mapped_column(String(200), nullable=False)
    parameter_value: Mapped[str | None] = mapped_column(Text, nullable=True)
