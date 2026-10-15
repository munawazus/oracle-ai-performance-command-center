from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import (
    DatabaseCredentialRepository,
    DatabaseGroupRepository,
    MonitoredDatabaseRepository,
)
from app.repositories.secret_repo import SecretRepository
from app.schemas.database import CreateDatabaseGroupRequest, DatabaseGroupResponse, UpdateDatabaseGroupRequest
from app.security.deps import get_client_ip, require_permission
from app.services.audit_service import AuditService
from app.services.database_service import DatabaseService
from app.services.secret_service import LocalEncryptedSecretProvider

router = APIRouter(prefix="/database-groups", tags=["database-groups"])


def _build_service(db: AsyncSession) -> DatabaseService:
    return DatabaseService(
        MonitoredDatabaseRepository(db),
        DatabaseCredentialRepository(db),
        DatabaseGroupRepository(db),
        LocalEncryptedSecretProvider(SecretRepository(db)),
        AuditService(AuditRepository(db)),
    )


@router.get("", response_model=list[DatabaseGroupResponse])
async def list_groups(
    db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("database:view"))
) -> list[DatabaseGroupResponse]:
    service = _build_service(db)
    rows = await service.list_groups()
    return [
        DatabaseGroupResponse(
            id=r["group"].id,
            name=r["group"].name,
            description=r["group"].description,
            database_count=r["database_count"],
            created_at=r["group"].created_at,
            updated_at=r["group"].updated_at,
        )
        for r in rows
    ]


@router.post("", response_model=DatabaseGroupResponse, status_code=201)
async def create_group(
    payload: CreateDatabaseGroupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:add")),
) -> DatabaseGroupResponse:
    service = _build_service(db)
    group = await service.create_group(
        name=payload.name, description=payload.description, actor_user_id=actor.id, ip_address=get_client_ip(request)
    )
    return DatabaseGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        database_count=0,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.patch("/{group_id}", response_model=DatabaseGroupResponse)
async def update_group(
    group_id: str,
    payload: UpdateDatabaseGroupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:edit")),
) -> DatabaseGroupResponse:
    service = _build_service(db)
    group = await service.update_group(
        group_id,
        name=payload.name,
        description=payload.description,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    member_ids = await service.group_repo.database_ids_for_group(group.id)
    return DatabaseGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        database_count=len(member_ids),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("database:delete")),
) -> None:
    service = _build_service(db)
    await service.delete_group(group_id, actor_user_id=actor.id, ip_address=get_client_ip(request))
