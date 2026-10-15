from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.common import ApiModel

ReportTypeLiteral = Literal["DAILY_DBA", "INCIDENT", "PERFORMANCE", "SQL_REGRESSION", "CAPACITY", "HEALTH"]


class GenerateReportRequest(BaseModel):
    report_type: ReportTypeLiteral
    database_id: str | None = None
    incident_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    # Only consulted for INCIDENT reports. Re-running RCA costs an LLM call, so it is opt-in
    # rather than a side effect of every incident report generation.
    include_rca: bool = False


class ReportSummaryResponse(ApiModel):
    """List view - omits `content`, which can be large, to keep GET /api/reports light."""

    id: str
    report_type: str
    requested_by_email: str
    monitored_database_id: str | None
    database_name_snapshot: str | None
    incident_id: str | None
    incident_title_snapshot: str | None
    period_start: datetime | None
    period_end: datetime | None
    timezone: str
    generated_at: datetime
    data_available: bool


class ReportResponse(ReportSummaryResponse):
    data_completeness_note: str
    limitations: list[str]
    content: dict[str, Any]
