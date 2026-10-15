from datetime import datetime

from pydantic import Field

from app.schemas.common import ApiModel


class IncidentResponse(ApiModel):
    id: str
    monitored_database_id: str
    status: str
    severity: str
    title: str
    opened_at: datetime
    last_evidence_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by_user_id: str | None
    assigned_to_user_id: str | None
    resolved_at: datetime | None
    resolved_by_user_id: str | None
    resolution_note: str | None


class IncidentEventResponse(ApiModel):
    id: str
    occurred_at: datetime
    event_type: str
    actor_user_id: str | None
    detail: dict | None


class AssignIncidentRequest(ApiModel):
    assignee_user_id: str = Field(min_length=1)


class ResolveIncidentRequest(ApiModel):
    note: str | None = Field(default=None, max_length=2000)


class AssignableUserResponse(ApiModel):
    """Minimal - just enough to populate an assignee picker. Not the full admin:users view."""

    id: str
    display_name: str
    email: str
