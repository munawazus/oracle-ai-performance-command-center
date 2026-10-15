from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import ApiModel


class NotificationSettingsResponse(ApiModel):
    smtp_host: str | None
    smtp_port: int
    smtp_use_tls: bool
    smtp_username: str | None
    has_smtp_password: bool
    sender_email: str | None
    sender_name: str
    default_recipients: list[str]
    escalation_recipients: list[str]


class UpdateNotificationSettingsRequest(ApiModel):
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_use_tls: bool | None = None
    smtp_username: str | None = Field(default=None, max_length=255)
    smtp_password: str | None = Field(default=None, max_length=500)
    sender_email: EmailStr | None = None
    sender_name: str | None = Field(default=None, max_length=200)
    default_recipients: list[EmailStr] | None = None
    escalation_recipients: list[EmailStr] | None = None


class NotificationEventResponse(ApiModel):
    id: str
    incident_id: str
    severity: str
    recipients: list[str]
    subject: str
    body_text: str
    status: str
    attempt_count: int
    last_attempt_at: datetime | None
    last_error_category: str | None
    sent_at: datetime | None
    created_at: datetime
