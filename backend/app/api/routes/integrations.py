from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.integration import Integration, IntegrationDelivery
from app.repositories.audit_repo import AuditRepository
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.repositories.secret_repo import SecretRepository
from app.schemas.integration import (
    CreateIntegrationRequest,
    IntegrationDeliveryResponse,
    IntegrationResponse,
    TestIntegrationResponse,
    UpdateIntegrationRequest,
)
from app.security.deps import get_client_ip, require_permission, require_rate_limit
from app.services.audit_service import AuditService
from app.services.integration_service import IntegrationService
from app.services.secret_service import LocalEncryptedSecretProvider

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _build_service(db: AsyncSession) -> IntegrationService:
    return IntegrationService(
        IntegrationRepository(db),
        IntegrationDeliveryRepository(db),
        MonitoredDatabaseRepository(db),
        LocalEncryptedSecretProvider(SecretRepository(db)),
        AuditService(AuditRepository(db)),
    )


def _to_response(integration: Integration) -> IntegrationResponse:
    return IntegrationResponse(
        id=integration.id,
        type=integration.type,
        name=integration.name,
        target_url=integration.target_url,
        has_secret=integration.secret_ref is not None,
        enabled=integration.enabled,
        trigger_severities=integration.trigger_severities,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


def _delivery_to_response(delivery: IntegrationDelivery) -> IntegrationDeliveryResponse:
    return IntegrationDeliveryResponse(
        id=delivery.id,
        integration_id=delivery.integration_id,
        incident_id=delivery.incident_id,
        event_type=delivery.event_type,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        last_error_category=delivery.last_error_category,
        response_status_code=delivery.response_status_code,
        sent_at=delivery.sent_at,
        created_at=delivery.created_at,
    )


@router.get("", response_model=list[IntegrationResponse])
async def list_integrations(
    db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("notification:configure"))
) -> list[IntegrationResponse]:
    service = _build_service(db)
    return [_to_response(i) for i in await service.list_integrations()]


@router.post("", response_model=IntegrationResponse, status_code=201)
async def create_integration(
    payload: CreateIntegrationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("notification:configure")),
) -> IntegrationResponse:
    service = _build_service(db)
    integration = await service.create_integration(
        integration_type=payload.type,
        name=payload.name,
        target_url=payload.target_url,
        secret=payload.secret,
        enabled=payload.enabled,
        trigger_severities=payload.trigger_severities,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return _to_response(integration)


@router.patch("/{integration_id}", response_model=IntegrationResponse)
async def update_integration(
    integration_id: str,
    payload: UpdateIntegrationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("notification:configure")),
) -> IntegrationResponse:
    service = _build_service(db)
    integration = await service.update_integration(
        integration_id,
        name=payload.name,
        target_url=payload.target_url,
        secret=payload.secret,
        enabled=payload.enabled,
        trigger_severities=payload.trigger_severities,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return _to_response(integration)


@router.delete("/{integration_id}", status_code=204)
async def delete_integration(
    integration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("notification:configure")),
) -> None:
    service = _build_service(db)
    await service.delete_integration(integration_id, actor_user_id=actor.id, ip_address=get_client_ip(request))


@router.post("/{integration_id}/test", response_model=TestIntegrationResponse)
async def test_integration(
    integration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("notification:configure")),
    _rate_limit=Depends(require_rate_limit("integration_test", limit=10, window_seconds=60)),
) -> TestIntegrationResponse:
    service = _build_service(db)
    result = await service.test_integration(integration_id, actor_user_id=actor.id, ip_address=get_client_ip(request))
    return TestIntegrationResponse(**result)


@router.get("/deliveries", response_model=list[IntegrationDeliveryResponse])
async def list_deliveries(
    integration_id: str | None = Query(default=None),
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("incident:view")),
) -> list[IntegrationDeliveryResponse]:
    """Delivery status, same treatment as GET /api/notifications/events - visible even
    (especially) when a webhook endpoint is failing, since the incident itself is never
    lost when a delivery is."""
    repo = IntegrationDeliveryRepository(db)
    if incident_id:
        deliveries = await repo.list_for_incident(incident_id)
    elif integration_id:
        deliveries = await repo.list_for_integration(integration_id, limit=limit)
    else:
        deliveries = await repo.list_recent(limit=limit)
    return [_delivery_to_response(d) for d in deliveries]
