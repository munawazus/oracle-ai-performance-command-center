from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPkMixin, utcnow


class SessionSample(UUIDPkMixin, Base):
    """Our own point-in-time session snapshot history - built from V$SESSION sampling,
    not a reproduction of Oracle ASH (master plan Sec 7)."""

    __tablename__ = "session_samples"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    sid: Mapped[int] = mapped_column(Integer, nullable=False)
    serial_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sql_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    event: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wait_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    blocking_session: Mapped[int | None] = mapped_column(Integer, nullable=True)
    machine: Mapped[str | None] = mapped_column(String(200), nullable=True)
    program: Mapped[str | None] = mapped_column(String(200), nullable=True)
    module: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_call_et: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WaitSnapshot(UUIDPkMixin, Base):
    """Raw cumulative V$SYSTEM_EVENT counters per sample. Deltas/rates are computed by the
    analytics layer (a later phase), not here - this table stores facts, not derived metrics."""

    __tablename__ = "wait_snapshots"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(200), nullable=False)
    wait_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_waits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    time_waited_centisecs: Mapped[int] = mapped_column(BigInteger, nullable=False)


class InstanceHealthSnapshot(UUIDPkMixin, Base):
    __tablename__ = "instance_health_snapshots"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    # "up"/"unreachable"/"authentication_failed"/"unknown_error" - reachability, not the
    # instance's own OPEN/MOUNTED status (see `instance_status` below for that).
    reachability: Mapped[str] = mapped_column(String(30), nullable=False)
    instance_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    active_session_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Raw cumulative V$SYSSTAT "CPU used by this session" counter (centiseconds) - system-wide
    # despite the name. A rate (Finding 7, docs/QA_LIVE_FINDINGS.md) is computed by the
    # analytics layer from two rounds, same as WaitSnapshot.time_waited_centisecs.
    cpu_centisecs_used: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class CollectorStatus(UUIDPkMixin, Base):
    """One row per monitored database - self-observability per master plan Sec 23
    (collector status, latency, failures)."""

    __tablename__ = "collector_status"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_run_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The SQL collector (Master Plan Phase 9) runs as its own job on its own interval - see
    # app/services/collector_service.py's run_sql_once_for_database - so it gets its own
    # self-observability fields rather than sharing/overloading the ones above.
    last_sql_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sql_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sql_error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # The capacity collector (Master Plan Phase 5, Enterprise Readiness Plan D.2) is also its
    # own job on its own interval - see app/services/collector_service.py's
    # run_capacity_once_for_database - own self-observability fields, same reasoning as above.
    last_capacity_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_capacity_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_capacity_error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # The Statspack sync job (Master Plan Phase 11, Enterprise Readiness Plan D.3) is also its
    # own job on its own interval - see app/services/collector_service.py's
    # run_statspack_once_for_database - own self-observability fields, same reasoning as above.
    last_statspack_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_statspack_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_statspack_error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Change Intelligence (Master Plan Phase 14, Enterprise Readiness Plan D.4) is also its own
    # job on its own interval - see app/services/collector_service.py's
    # run_config_once_for_database - own self-observability fields, same reasoning as above.
    last_config_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_config_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_config_error_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
