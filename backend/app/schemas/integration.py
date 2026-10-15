from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.common import ApiModel

IntegrationTypeLiteral = Literal["WEBHOOK", "SLACK", "TEAMS"]


def _validate_url(value: str) -> str:
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("target_url must start with http:// or https://")
    return value


class CreateIntegrationRequest(ApiModel):
    type: IntegrationTypeLiteral
    name: str = Field(min_length=1, max_length=200)
    target_url: str = Field(min_length=1, max_length=2000)
    secret: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    trigger_severities: list[str] = Field(default_factory=lambda: ["HIGH", "CRITICAL"])

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        return _validate_url(value)


class UpdateIntegrationRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_url: str | None = Field(default=None, min_length=1, max_length=2000)
    secret: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    trigger_severities: list[str] | None = None

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_url(value)


class IntegrationResponse(ApiModel):
    id: str
    type: str
    name: str
    target_url: str
    has_secret: bool
    enabled: bool
    trigger_severities: list[str]
    created_at: datetime
    updated_at: datetime


class TestIntegrationResponse(ApiModel):
    ok: bool
    status_code: int | None
    message: str


class IntegrationDeliveryResponse(ApiModel):
    id: str
    integration_id: str
    incident_id: str
    event_type: str
    status: str
    attempt_count: int
    last_error_category: str | None
    response_status_code: int | None
    sent_at: datetime | None
    created_at: datetime
