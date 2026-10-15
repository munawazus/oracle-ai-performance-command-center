from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class SqlStatement(UUIDPkMixin, Base):
    """One row per (database, sql_id) - the SQL text/identity dimension, kept separate from
    the per-round fact table below so text isn't repeated on every sample (master plan
    Phase 9). Text comes from V$SQL.SQL_TEXT (VARCHAR2(1000)) - never V$SQL.SQL_FULLTEXT
    (a CLOB the deliberately-minimal OracleCursor protocol doesn't handle), so
    sql_text_truncated records when the real statement ran longer than what's stored."""

    __tablename__ = "sql_statements"
    __table_args__ = (
        UniqueConstraint("monitored_database_id", "sql_id", name="uq_sql_statement_per_database"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sql_id: Mapped[str] = mapped_column(String(20), nullable=False)
    sql_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    sql_text_truncated: Mapped[bool] = mapped_column(default=False, nullable=False)
    parsing_schema_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class SqlStatSample(UUIDPkMixin, Base):
    """Raw cumulative V$SQL counters per (sql_id, plan_hash_value) per collection round -
    same convention as WaitSnapshot: this table stores facts, not derived metrics. Deltas/
    rates are computed by app/analytics/sql_metrics.py, not here. Counters are cumulative
    per child cursor exactly like V$SYSTEM_EVENT, so a delta always needs two rounds.

    elapsed_time_us/cpu_time_us are MICROSECONDS (V$SQL's native unit) - deliberately not
    centiseconds like the wait tables, to avoid a silent unit-mixing bug between the two.

    Composite indexes (this schema's first - every other telemetry table uses only a single
    column index) because both real access patterns here are always two-column: "the latest
    two rounds for this database" (top-SQL) and "this statement's history in a window"
    (drill-down + regression detection). A single-column monitored_database_id index would
    degenerate into a full-partition scan on this table, which will have far more rows than
    session_samples/wait_snapshots at the same retention (multiple rows per SQL per round).
    """

    __tablename__ = "sql_stat_samples"
    __table_args__ = (
        Index("ix_sql_stat_samples_db_sampled", "monitored_database_id", "sampled_at"),
        Index("ix_sql_stat_samples_db_sqlid_sampled", "monitored_database_id", "sql_id", "sampled_at"),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sql_id: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_hash_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    child_cursor_count: Mapped[int] = mapped_column(Integer, nullable=False)
    executions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    elapsed_time_us: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cpu_time_us: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buffer_gets: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disk_reads: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rows_processed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parse_calls: Mapped[int] = mapped_column(BigInteger, nullable=False)


class SqlPlanSnapshot(UUIDPkMixin, Base):
    """Capture-on-change: one row per (database, sql_id, plan_hash_value), from V$SQL_PLAN -
    a base dynamic performance view, not a Diagnostics/Tuning Pack view (unlike
    DBA_HIST_SQL_PLAN or SQL Plan Baselines, neither of which this platform reads). A plan
    hash is by definition a hash of the plan shape, so the plan body for one hash is
    immutable - snapshotting it every collection round would be pure waste. first_seen_at/
    last_seen_at instead mark the observation window for that plan.

    plan_lines is deliberately JSON, not a child table: a plan is always read whole, never
    queried per-line, and is small (a handful to a few dozen rows) - same reasoning already
    applied to Report.content. Each element: {id, parent_id, depth, position, operation,
    options, object_owner, object_name, object_type, cost, cardinality, bytes}.

    ACCESS_PREDICATES/FILTER_PREDICATES are deliberately not collected or stored anywhere -
    they can embed literal predicate values (e.g. "EMPNO=12345"), the same PII/data-
    exfiltration class as V$SQL_BIND_CAPTURE (never allowlisted at all)."""

    __tablename__ = "sql_plan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "monitored_database_id", "sql_id", "plan_hash_value", name="uq_sql_plan_snapshot"
        ),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sql_id: Mapped[str] = mapped_column(String(20), nullable=False)
    plan_hash_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    plan_line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_lines: Mapped[list] = mapped_column(JSON, nullable=False)


class SqlRegressionEvent(UUIDPkMixin, Base):
    """A deterministic finding from app/analytics/sql_regression.py - never a statistical
    outlier from the general per-metric-key baseline engine (see that module's docstring for
    why). detection_note is a fixed, evidence-derived sentence built by the comparator -
    never LLM-generated, same rule already stated in app/reports/builders.py for report
    narration."""

    __tablename__ = "sql_regression_events"
    __table_args__ = (
        Index(
            "ix_sql_regression_events_open",
            "monitored_database_id",
            "sql_id",
            "regression_kind",
            "status",
        ),
    )

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sql_id: Mapped[str] = mapped_column(String(20), nullable=False)
    regression_kind: Mapped[str] = mapped_column(String(30), nullable=False)  # PLAN_CHANGE | ELAPSED_PER_EXEC
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)  # OPEN | RESOLVED
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_elapsed_per_exec_us: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_elapsed_per_exec_us: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_executions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_executions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    baseline_plan_hash_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_plan_hash_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detection_note: Mapped[str] = mapped_column(String(500), nullable=False)
