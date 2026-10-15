from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import LLMClient, get_llm_client
from app.db import get_db
from app.models.report import REPORT_TYPE_INCIDENT, Report
from app.repositories.analytics_repo import AnomalyRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.capacity_repo import CapacityRepository
from app.repositories.config_repo import ConfigTelemetryRepository
from app.repositories.database_repo import CapabilityRepository, MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository
from app.repositories.report_repo import ReportRepository
from app.repositories.sql_repo import SqlTelemetryRepository
from app.repositories.statspack_repo import StatspackRepository
from app.repositories.telemetry_repo import CollectorStatusRepository, TelemetryRepository
from app.schemas.report import GenerateReportRequest, ReportResponse, ReportSummaryResponse
from app.security.deps import get_client_ip, require_permission, require_rate_limit
from app.services.audit_service import AuditService
from app.services.rca_service import RcaService, RcaResult
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


def _build_service(db: AsyncSession) -> ReportService:
    return ReportService(
        report_repo=ReportRepository(db),
        database_repo=MonitoredDatabaseRepository(db),
        incident_repo=IncidentRepository(db),
        telemetry_repo=TelemetryRepository(db),
        collector_status_repo=CollectorStatusRepository(db),
        anomaly_repo=AnomalyRepository(db),
        notification_repo=NotificationEventRepository(db),
        sql_repo=SqlTelemetryRepository(db),
        capacity_repo=CapacityRepository(db),
        capability_repo=CapabilityRepository(db),
        statspack_repo=StatspackRepository(db),
        config_repo=ConfigTelemetryRepository(db),
        audit=AuditService(AuditRepository(db)),
    )


def _rca_result_to_dict(result: RcaResult) -> dict:
    return {
        "probable_root_cause": result.probable_root_cause,
        "confidence": result.confidence,
        "evidence_summary": result.evidence_summary,
        "secondary_contributors": result.secondary_contributors,
        "alternative_hypotheses": result.alternative_hypotheses,
        "recommended_investigation": result.recommended_investigation,
        "recommended_action": result.recommended_action,
        "risk": result.risk,
        "data_completeness_note": result.data_completeness_note,
        "model": result.model,
    }


@router.post("", response_model=ReportResponse, status_code=201)
async def generate_report(
    payload: GenerateReportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("report:create")),
    _rate_limit=Depends(require_rate_limit("report_generate", limit=20, window_seconds=60)),
    llm: LLMClient | None = Depends(get_llm_client),
) -> Report:
    rca_result: dict | None = None
    if payload.report_type == REPORT_TYPE_INCIDENT and payload.include_rca:
        if llm is not None and payload.incident_id:
            rca_service = RcaService(db, llm, AuditService(AuditRepository(db)))
            result = await rca_service.investigate(
                payload.incident_id, actor_user_id=actor.id, ip_address=get_client_ip(request)
            )
            rca_result = _rca_result_to_dict(result)

    service = _build_service(db)
    return await service.generate(
        report_type=payload.report_type,
        database_id=payload.database_id,
        incident_id=payload.incident_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        rca_result=rca_result,
        actor_user_id=actor.id,
        actor_email=actor.email,
        ip_address=get_client_ip(request),
    )


@router.get("", response_model=list[ReportSummaryResponse])
async def list_reports(
    report_type: str | None = Query(default=None),
    database_id: str | None = Query(default=None),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("report:create")),
) -> list[Report]:
    service = _build_service(db)
    return await service.list_reports(
        report_type=report_type, monitored_database_id=database_id, incident_id=incident_id, limit=limit, offset=offset
    )


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("report:create"))
) -> Report:
    service = _build_service(db)
    return await service.get(report_id)


@router.get("/{report_id}/export")
async def export_report(
    report_id: str,
    request: Request,
    export_format: str = Query(default="pdf", alias="format"),
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("report:create")),
) -> Response:
    """Streams the export directly into the response body - never written to a filesystem
    path, so there is no server-side filename derived from user input and no path-traversal
    surface. The download filename is built server-side from the report's own id/type/format
    only (all already validated/enum-constrained), never from anything the client supplies."""
    service = _build_service(db)
    data, content_type, report = await service.export(
        report_id, export_format, actor_user_id=actor.id, actor_email=actor.email, ip_address=get_client_ip(request)
    )
    filename = f"report-{report.report_type.lower()}-{report.id}.{export_format}"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
