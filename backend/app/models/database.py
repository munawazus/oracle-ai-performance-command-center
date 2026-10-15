from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin


class MonitoredDatabase(UUIDPkMixin, TimestampMixin, Base):
    """A registered Oracle target. No connection is made from this table alone -
    connectivity/capability probing is implemented in the collector phase, not here."""

    __tablename__ = "monitored_databases"

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # DBA-entered hint only, not yet verified against the live instance.
    edition_hint: Mapped[str] = mapped_column(String(20), default="UNKNOWN", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Freeform, normalized (lowercase/trimmed/deduped) at the schema layer - cross-cutting
    # fleet labels distinct from DatabaseGroup, which is a named organizational container.
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Per-database overrides of app/config.py's global capacity-alert thresholds - "how full is
    # too full" varies by environment/disk-provisioning policy far more than one fleet-wide
    # number can express. Null means "use the global default," the same convention every
    # nullable per-database override in this app already follows.
    capacity_warn_pct_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_critical_pct_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_warn_days_to_full: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_critical_days_to_full: Mapped[float | None] = mapped_column(Float, nullable=True)


class DatabaseCredential(UUIDPkMixin, TimestampMixin, Base):
    """Oracle monitoring-account username plus a reference to the encrypted password.
    The password itself never lives in this table or anywhere outside the SecretProvider."""

    __tablename__ = "database_credentials"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    secret_ref: Mapped[str] = mapped_column(ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DatabaseCapability(UUIDPkMixin, TimestampMixin, Base):
    """Populated by detect_capabilities() (app/oracle/capability_probe.py) against the
    Section 7 base-view allowlist only - never by anything reading Diagnostics/Tuning
    Pack-gated views."""

    __tablename__ = "database_capabilities"

    monitored_database_id: Mapped[str] = mapped_column(
        ForeignKey("monitored_databases.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    detected_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_edition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_rac: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Enterprise Readiness Plan D.3: means PERFSTAT.STATS$SNAPSHOT is actually queryable
    # (SELECT COUNT(*) succeeds), not merely that a PERFSTAT database user exists - the
    # earlier, weaker check never confirmed the schema/tables were actually installed.
    # This is the ONLY writer of this column (never the Statspack sync job itself, which only
    # reads it) - a single-writer invariant so the two never race on the same row.
    statspack_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # V$PARAMETER's cpu_count - the instance's visible CPU core count, used for Wait
    # Analysis's "Max CPU cores" reference line. Static instance metadata, refreshed only
    # when capabilities are (re-)detected, not on the 15s collector cadence.
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Must be explicitly confirmed by the DBA - never auto-inferred from edition detection.
    diagnostics_tuning_pack_licensed_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {view_name: bool} - which of the allowlisted base views this account can actually read.
    privilege_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
