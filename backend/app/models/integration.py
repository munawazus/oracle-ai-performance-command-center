from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin

INTEGRATION_TYPE_WEBHOOK = "WEBHOOK"
INTEGRATION_TYPE_SLACK = "SLACK"
INTEGRATION_TYPE_TEAMS = "TEAMS"
INTEGRATION_TYPES = (INTEGRATION_TYPE_WEBHOOK, INTEGRATION_TYPE_SLACK, INTEGRATION_TYPE_TEAMS)

STATUS_PENDING = "PENDING"
STATUS_RETRYING = "RETRYING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"


class Integration(UUIDPkMixin, TimestampMixin, Base):
    """One configured outbound channel (master plan Sec 35/36 Phase 23: "Webhooks first,
    Slack/Teams next"). Unlike NotificationSettings (a single SMTP profile), a fleet can
    have any number of these - e.g. one Slack channel and one generic webhook at once."""

    __tablename__ = "integrations"

    type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    # Optional HMAC-SHA256 signing secret (X-Signature-256 header) - never required, since
    # Slack/Teams incoming webhooks don't support verifying one, but a generic WEBHOOK
    # receiver can check it. SET NULL (not RESTRICT) - same treatment as
    # NotificationSettings.smtp_password_secret_ref, deliberately not DatabaseCredential's
    # RESTRICT (a signing secret has no dependent row that must survive it).
    secret_ref: Mapped[str | None] = mapped_column(ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Subset of INFO/LOW/MEDIUM/HIGH/CRITICAL that fires this integration - defaults to the
    # same mandatory minimum as email (master plan Sec 15's severity/email table).
    trigger_severities: Mapped[list] = mapped_column(JSON, default=lambda: ["HIGH", "CRITICAL"], nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class IntegrationDelivery(UUIDPkMixin, TimestampMixin, Base):
    """The outbox - one row per (integration, incident, event, severity-threshold-first-
    reached), same dedupe shape as NotificationEvent. The payload is rendered once at queue
    time and stored, so what's delivered never drifts from what was decided even if the
    incident changes before the worker gets to it."""

    __tablename__ = "integration_deliveries"

    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
