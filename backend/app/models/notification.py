from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPkMixin

STATUS_PENDING = "PENDING"
STATUS_RETRYING = "RETRYING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"


class NotificationSettings(UUIDPkMixin, TimestampMixin, Base):
    """Singleton row (NotificationSettingsRepository always operates on the first one,
    creating a default if none exists) - one SMTP profile for v1, not per-recipient-group
    routing. default_recipients get every HIGH/CRITICAL alert; escalation_recipients get
    added on top for CRITICAL only (master plan Sec 17's "configurable escalation")."""

    __tablename__ = "notification_settings"

    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Never the plaintext password - a reference into the SecretProvider, same as Oracle
    # monitoring-account credentials.
    smtp_password_secret_ref: Mapped[str | None] = mapped_column(
        ForeignKey("secrets.id", ondelete="SET NULL"), nullable=True
    )
    sender_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sender_name: Mapped[str] = mapped_column(
        String(200), default="Oracle AI Performance Command Center", nullable=False
    )
    default_recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    escalation_recipients: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class NotificationEvent(UUIDPkMixin, TimestampMixin, Base):
    """The outbox. One row per (incident, severity-threshold-first-reached) - dedupe_key
    enforces "don't re-alert while the same metric stays above threshold" (master plan
    Sec 19) even under concurrent creation attempts."""

    __tablename__ = "notification_events"

    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDING, nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
