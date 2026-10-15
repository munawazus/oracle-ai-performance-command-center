from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class StatspackSnapshot(UUIDPkMixin, Base):
    """One row per Statspack snapshot this platform has synced (master plan Phase 11,
    Enterprise Readiness Plan D.3) - identity only (snap_id, snap_time), mirroring
    SqlPlanSnapshot's capture-on-change shape: a Statspack snap_id is immutable once taken by
    the DBA's own statspack.snap job, so it's synced once, never re-fetched.

    Statspack's own snapshots are the "rounds" here, taken on the DBA's independent, irregular
    schedule (often hourly, sometimes not at all) - NOT this platform's own fixed collection
    interval, unlike every other telemetry table in this codebase."""

    __tablename__ = "statspack_snapshots"
    __table_args__ = (
        UniqueConstraint("monitored_database_id", "snap_id", name="uq_statspack_snapshot"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snap_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StatspackWaitEventSample(UUIDPkMixin, Base):
    """Raw cumulative-since-instance-startup wait-event counters as of one Statspack
    snapshot, from STATS$SYSTEM_EVENT - same "store facts, not derived metrics" convention as
    WaitSnapshot; a rate is computed at read time between two synced snapshots, reusing
    app/analytics/metrics.py's existing aggregate_by_wait_class/compute_wait_class_rates
    unmodified (these rows are shaped to satisfy that module's row Protocol)."""

    __tablename__ = "statspack_wait_event_samples"
    __table_args__ = (
        Index("ix_statspack_wait_event_samples_db_snap", "monitored_database_id", "snap_id"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_name: Mapped[str] = mapped_column(String(200), nullable=False)
    wait_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_waits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_waited_centisecs: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StatspackSqlSample(UUIDPkMixin, Base):
    """Raw cumulative-since-cursor-load SQL execution counters as of one Statspack snapshot,
    from STATS$SQL_SUMMARY. Field names/units deliberately match SqlStatSample/SqlStatRowLike
    exactly (elapsed_time_us/cpu_time_us in microseconds, not centiseconds) so
    app/analytics/sql_metrics.py's compute_sql_deltas/rank_sql_deltas are reusable unmodified
    against these rows - no new delta logic needed for Statspack's own top-SQL summary.

    sql_text is deliberately never stored here (never fetched from the CLOB-assembly
    STATS$SQLTEXT table, same LOB-avoidance reasoning as app/oracle/sql_probe.py) - the API
    layer cross-references sql_id against this platform's own D.1 SqlStatement table instead,
    when the two collectors happen to overlap on the same statement."""

    __tablename__ = "statspack_sql_samples"
    __table_args__ = (
        Index("ix_statspack_sql_samples_db_snap", "monitored_database_id", "snap_id"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snap_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sql_id: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_hash_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    executions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    elapsed_time_us: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cpu_time_us: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buffer_gets: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disk_reads: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rows_processed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parse_calls: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StatspackSyncState(UUIDPkMixin, Base):
    """The sync high-water-mark cursor - the newest snap_id this platform has already pulled
    for this database, so the next sync run only asks Statspack's own tables for snap_id
    greater than this. Kept as its own table, separate from CollectorStatus, because it's
    domain data the sync logic reads/writes every run, not self-observability metadata."""

    __tablename__ = "statspack_sync_state"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    last_synced_snap_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_synced_snap_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
