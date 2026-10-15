from datetime import datetime

from app.schemas.common import ApiModel


class AuditEventResponse(ApiModel):
    id: str
    occurred_at: datetime
    actor_user_id: str | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    success: bool
    ip_address: str | None
    event_metadata: dict | None
