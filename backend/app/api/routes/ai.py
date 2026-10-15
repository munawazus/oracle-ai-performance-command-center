from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import LLMClient, get_copilot_llm_client
from app.db import get_db
from app.exceptions import UpstreamUnavailableError
from app.redis_client import get_redis
from app.repositories.audit_repo import AuditRepository
from app.schemas.ai import CopilotAskRequest, CopilotAskResponse, finding_to_view, tool_status_to_view
from app.security.deps import get_client_ip, require_permission, require_rate_limit
from app.services.audit_service import AuditService
from app.services.copilot_service import CopilotService

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/ask", response_model=CopilotAskResponse)
async def ask_copilot(
    payload: CopilotAskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("ai:query")),
    _rate_limit=Depends(require_rate_limit("ai_ask", limit=10, window_seconds=60)),
    llm: LLMClient | None = Depends(get_copilot_llm_client),
    redis: Redis = Depends(get_redis),
) -> CopilotAskResponse:
    if llm is None:
        raise UpstreamUnavailableError(
            "AI is not enabled on this deployment. An administrator must configure AI_ENABLED "
            "and a reachable local model endpoint."
        )
    service = CopilotService(db, llm, AuditService(AuditRepository(db)), redis)
    result = await service.ask(
        payload.question,
        database_id=payload.database_id,
        incident_id=payload.incident_id,
        fleet_scope=payload.fleet_scope,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return CopilotAskResponse(
        answer=result.answer,
        model=result.model,
        tools_used=result.tools_used,
        tool_statuses=[tool_status_to_view(s) for s in result.tool_statuses],
        evidence_coverage=result.evidence_coverage,
        findings=[finding_to_view(f) for f in result.findings],
    )
