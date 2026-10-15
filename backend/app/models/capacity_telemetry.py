from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class TablespaceSample(UUIDPkMixin, Base):
    """Raw per-tablespace space facts per collection round (master plan Phase 5, Enterprise
    Readiness Plan D.2) - same "store facts, not derived metrics" convention as WaitSnapshot/
    SqlStatSample: pct_used, growth rate, and time-to-full projection are all computed by
    app/analytics/capacity_metrics.py at read time, never stored here.

    One unified table across all three `contents` values (PERMANENT, UNDO, TEMPORARY) rather
    than one table per type - unlike SqlStatSample vs SqlPlanSnapshot (genuinely different
    grains), every tablespace type shares the same grain (one row per tablespace per round)
    and the same access patterns below, so splitting would only triplicate the schema.

    used_bytes is still a stored fact, not a derived metric, despite being computed from other
    Oracle facts (allocated - free, or V$TEMP_SPACE_HEADER.bytes_used directly): it's a single
    arithmetic pass over one snapshot's own rows, not a temporal derivation across samples -
    the same distinction SqlStatDelta draws between "a fact" and "a rate needing two rounds."

    max_bytes is NULL when no known ceiling exists (autoextend enabled with MAXBYTES at or
    above the "unlimited" threshold - see app/oracle/tablespace_probe.py) - never a fabricated
    huge number standing in for "no meaningful limit."

    Composite indexes mirror SqlStatSample's justification: both real access patterns are
    two-column - "the latest round for this database" (current-state overview) and "this
    tablespace's series in a window" (growth-trend computation)."""

    __tablename__ = "tablespace_samples"
    __table_args__ = (
        Index("ix_tablespace_samples_db_sampled", "monitored_database_id", "sampled_at"),
        Index(
            "ix_tablespace_samples_db_name_sampled",
            "monitored_database_id",
            "tablespace_name",
            "sampled_at",
        ),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    tablespace_name: Mapped[str] = mapped_column(String(128), nullable=False)
    contents: Mapped[str] = mapped_column(String(20), nullable=False)  # PERMANENT | UNDO | TEMPORARY
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    allocated_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)


class CapacityAlertEvent(UUIDPkMixin, Base):
    """A deterministic finding from app/analytics/capacity_alerts.py - structural mirror of
    SqlRegressionEvent's OPEN/RESOLVED, escalate-only-severity, auto-resolve-when-undetected
    lifecycle. detection_note is a fixed, evidence-derived sentence built by the comparator -
    never LLM-generated, same rule stated in app/reports/builders.py for report narration.

    Unlike SqlRegressionEvent's PLAN_CHANGE suppressing ELAPSED_PER_EXEC (same root cause),
    PCT_USED and PROJECTED_FULL are independent, non-suppressing finding kinds here - a
    tablespace can be nearly full but flat, or well under threshold but growing fast, and
    both are separately worth surfacing."""

    __tablename__ = "capacity_alert_events"
    __table_args__ = (
        Index(
            "ix_capacity_alert_events_open",
            "monitored_database_id",
            "tablespace_name",
            "alert_kind",
            "status",
        ),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tablespace_name: Mapped[str] = mapped_column(String(128), nullable=False)
    alert_kind: Mapped[str] = mapped_column(String(30), nullable=False)  # PCT_USED | PROJECTED_FULL
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)  # OPEN | RESOLVED
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pct_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Which denominator pct_used is measured against ("max size" | "allocated") - only set for
    # PCT_USED findings; null for PROJECTED_FULL (never ambiguous - always max-size-relative)
    # and for any alert persisted before this column existed. Never inferred after the fact.
    pct_basis: Mapped[str | None] = mapped_column(String(20), nullable=True)
    projected_days_to_full: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_used_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_allocated_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_max_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detection_note: Mapped[str] = mapped_column(String(500), nullable=False)
