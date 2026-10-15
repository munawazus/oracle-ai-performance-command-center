from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class MetricBaseline(UUIDPkMixin, Base):
    """Rolling per-hour-of-day mean/stddev per (database, metric) - Welford's algorithm
    state, updated incrementally by AnalyticsService. See app/analytics/baseline_engine.py."""

    __tablename__ = "metric_baselines"
    __table_args__ = (
        UniqueConstraint("monitored_database_id", "metric_key", "hour_of_day", name="uq_metric_baseline_bucket"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    hour_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mean: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    m2: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Anomaly(UUIDPkMixin, Base):
    """A single observation that deviated from its metric's rolling baseline by enough to
    flag. Correlated into an incident (evidence) by IncidentService - incident_id is null
    only in the brief window before correlation runs, never left permanently unset."""

    __tablename__ = "anomalies"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_mean: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_stddev: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
