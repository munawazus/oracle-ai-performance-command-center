from datetime import datetime

from app.schemas.common import ApiModel


class FleetVitalsRowResponse(ApiModel):
    database_id: str
    database_name: str
    overall_status: str
    open_incident_count: int
    blocking_present: bool
    capacity_trend: str


class ActivityEventResponse(ApiModel):
    event_id: str
    category: str
    severity: str
    occurred_at: datetime
    database_id: str
    database_name: str
    summary: str
    entities: dict
