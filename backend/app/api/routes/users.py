from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.repositories.audit_repo import AuditRepository
from app.repositories.rbac_repo import RbacRepository
from app.repositories.user_repo import UserRepository
from app.schemas.user import CreateUserRequest, UpdateUserRolesRequest, UpdateUserStatusRequest, UserResponse
from app.security.deps import get_client_ip, require_permission
from app.services.audit_service import AuditService
from app.services.user_service import UserService

router = APIRouter(prefix="/admin/users", tags=["administration"])


def _build_service(db: AsyncSession) -> UserService:
    return UserService(UserRepository(db), RbacRepository(db), AuditService(AuditRepository(db)))


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("admin:users"))
) -> list[UserResponse]:
    service = _build_service(db)
    rows = await service.list_users()
    return [
        UserResponse(
            id=r["user"].id,
            email=r["user"].email,
            display_name=r["user"].display_name,
            is_active=r["user"].is_active,
            roles=r["roles"],
            created_at=r["user"].created_at,
        )
        for r in rows
    ]


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("admin:users")),
) -> UserResponse:
    service = _build_service(db)
    user = await service.create_user(
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        role_names=payload.roles,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=payload.roles,
        created_at=user.created_at,
    )


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_status(
    user_id: str,
    payload: UpdateUserStatusRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("admin:users")),
) -> UserResponse:
    service = _build_service(db)
    user = await service.set_active(
        user_id, payload.is_active, actor_user_id=actor.id, ip_address=get_client_ip(request)
    )
    roles = await RbacRepository(db).get_user_role_names(user.id)
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=roles,
        created_at=user.created_at,
    )


@router.patch("/{user_id}/roles", response_model=UserResponse)
async def update_roles(
    user_id: str,
    payload: UpdateUserRolesRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("admin:users")),
) -> UserResponse:
    service = _build_service(db)
    user = await service.set_roles(
        user_id, payload.roles, actor_user_id=actor.id, ip_address=get_client_ip(request)
    )
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=payload.roles,
        created_at=user.created_at,
    )
