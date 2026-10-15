from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.audit_repo import AuditRepository
from app.schemas.audit import AuditEventResponse
from app.security.deps import require_permission
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventResponse])
async def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action_prefix: str | None = None,
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("audit:view")),
) -> list[AuditEventResponse]:
    service = AuditService(AuditRepository(db))
    return await service.list_events(limit=limit, offset=offset, action_prefix=action_prefix)
