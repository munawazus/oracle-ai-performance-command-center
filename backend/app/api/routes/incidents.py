from fastapi import APIRouter, Depends, Query, Request

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import LLMClient, get_llm_client
from app.db import get_db
from app.exceptions import NotFoundError, UpstreamUnavailableError, ValidationAppError
from app.models.incident import Incident
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import DatabaseGroupRepository, MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.user_repo import UserRepository
from app.schemas.ai import RcaResponse, TimelineEntryView, finding_to_view, tool_status_to_view
from app.schemas.incident import (
    AssignableUserResponse,
    AssignIncidentRequest,
    IncidentEventResponse,
    IncidentResponse,
    ResolveIncidentRequest,
)
from app.security.deps import get_client_ip, require_permission, require_rate_limit
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.rca_service import RcaService

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _build_service(db: AsyncSession) -> IncidentService:
    return IncidentService(IncidentRepository(db), AuditService(AuditRepository(db)))


@router.get("", response_model=list[IncidentResponse])
async def list_incidents(
    status: str | None = Query(default=None),
    monitored_database_id: str | None = Query(default=None),
    group_id: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("incident:view")),
) -> list[Incident]:
    """This is the "global incident view" (master plan Sec 26/35 Phase 22) - unfiltered by
    default, i.e. already fleet-wide. `group_id`/`tag` narrow it to one fleet segment;
    `monitored_database_id` narrows it to one database and takes precedence over
    `group_id`/`tag` if more than one is supplied, since a single-database scope is more
    specific than a fleet segment."""
    repo = IncidentRepository(db)
    if monitored_database_id:
        incidents = await repo.list_for_database(monitored_database_id)
        if status:
            incidents = [i for i in incidents if i.status == status]
        return incidents

    if group_id or tag:
        databases = await MonitoredDatabaseRepository(db).list_all(
            database_ids=await DatabaseGroupRepository(db).database_ids_for_group(group_id) if group_id else None
        )
        if tag:
            normalized = tag.strip().lower()
            databases = [d for d in databases if normalized in d.tags]
        return await repo.list_all(status=status, monitored_database_ids=[d.id for d in databases])

    return await repo.list_all(status=status)


@router.get("/assignable-users", response_model=list[AssignableUserResponse])
async def list_assignable_users(
    db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("incident:manage"))
) -> list[AssignableUserResponse]:
    """A minimal directory (id/name/email only) for populating an assignee picker - gated on
    incident:manage rather than admin:users, since a DBA/OPERATOR who can resolve incidents
    may not hold user-administration rights. Registered before /{incident_id} - FastAPI
    matches routes in registration order, and "assignable-users" would otherwise be parsed
    as an incident_id by the route below."""
    users = await UserRepository(db).list_all()
    return [AssignableUserResponse(id=u.id, display_name=u.display_name, email=u.email) for u in users if u.is_active]


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("incident:view"))
) -> Incident:
    incident = await IncidentRepository(db).get(incident_id)
    if incident is None:
        raise NotFoundError("Incident not found.")
    return incident


@router.get("/{incident_id}/timeline", response_model=list[IncidentEventResponse])
async def get_incident_timeline(
    incident_id: str, db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("incident:view"))
) -> list[IncidentEventResponse]:
    repo = IncidentRepository(db)
    incident = await repo.get(incident_id)
    if incident is None:
        raise NotFoundError("Incident not found.")
    events = await repo.list_events(incident_id)
    return [
        IncidentEventResponse(
            id=e.id, occurred_at=e.occurred_at, event_type=e.event_type, actor_user_id=e.actor_user_id, detail=e.detail
        )
        for e in events
    ]


@router.post("/{incident_id}/acknowledge", response_model=IncidentResponse)
async def acknowledge_incident(
    incident_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("incident:manage")),
) -> Incident:
    service = _build_service(db)
    return await service.acknowledge(incident_id, actor_user_id=actor.id, ip_address=get_client_ip(request))


@router.post("/{incident_id}/assign", response_model=IncidentResponse)
async def assign_incident(
    incident_id: str,
    payload: AssignIncidentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("incident:manage")),
) -> Incident:
    assignee = await UserRepository(db).get_by_id(payload.assignee_user_id)
    if assignee is None:
        raise ValidationAppError("assignee_user_id does not refer to a known user.")
    service = _build_service(db)
    return await service.assign(
        incident_id, payload.assignee_user_id, actor_user_id=actor.id, ip_address=get_client_ip(request)
    )


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
async def resolve_incident(
    incident_id: str,
    payload: ResolveIncidentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("incident:manage")),
) -> Incident:
    service = _build_service(db)
    return await service.resolve(
        incident_id, actor_user_id=actor.id, note=payload.note, ip_address=get_client_ip(request)
    )


@router.post("/{incident_id}/rca", response_model=RcaResponse)
async def request_rca(
    incident_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("ai:query")),
    _rate_limit=Depends(require_rate_limit("ai_rca", limit=10, window_seconds=60)),
    llm: LLMClient | None = Depends(get_llm_client),
) -> RcaResponse:
    """Investigates one incident using only this platform's own deterministic evidence
    (master plan Sec 16) - never fabricates a cause when the evidence doesn't support one."""
    if llm is None:
        raise UpstreamUnavailableError(
            "AI is not enabled on this deployment. An administrator must configure AI_ENABLED "
            "and a reachable local model endpoint."
        )
    service = RcaService(db, llm, AuditService(AuditRepository(db)))
    result = await service.investigate(incident_id, actor_user_id=actor.id, ip_address=get_client_ip(request))
    return RcaResponse(
        executive_summary=result.executive_summary,
        impact=result.impact,
        probable_root_cause=result.probable_root_cause,
        confidence=result.confidence,
        evidence_summary=result.evidence_summary,
        contributing_factors=result.contributing_factors,
        secondary_contributors=result.secondary_contributors,
        alternative_hypotheses=result.alternative_hypotheses,
        recommended_investigation=result.recommended_investigation,
        recommended_action=result.recommended_action,
        resolution=result.resolution,
        why_resolution_works=result.why_resolution_works,
        corrective_preventive_actions=result.corrective_preventive_actions,
        risk=result.risk,
        limitations=result.limitations,
        data_completeness_note=result.data_completeness_note,
        model=result.model,
        timeline=[TimelineEntryView(occurred_at=t.occurred_at, label=t.label) for t in result.timeline],
        findings=[finding_to_view(f) for f in result.findings],
        evidence_coverage=result.evidence_coverage,
        tool_statuses=[tool_status_to_view(s) for s in result.tool_statuses],
    )
