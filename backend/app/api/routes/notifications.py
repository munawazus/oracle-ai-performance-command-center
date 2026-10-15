from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.notification import NotificationSettings
from app.repositories.audit_repo import AuditRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.repositories.secret_repo import SecretRepository
from app.schemas.notification import (
    NotificationEventResponse,
    NotificationSettingsResponse,
    UpdateNotificationSettingsRequest,
)
from app.security.deps import get_client_ip, require_permission
from app.services.audit_service import AuditService
from app.services.notification_settings_service import NotificationSettingsService
from app.services.secret_service import LocalEncryptedSecretProvider

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _build_settings_service(db: AsyncSession) -> NotificationSettingsService:
    return NotificationSettingsService(
        NotificationSettingsRepository(db),
        LocalEncryptedSecretProvider(SecretRepository(db)),
        AuditService(AuditRepository(db)),
    )


def _to_response(settings: NotificationSettings) -> NotificationSettingsResponse:
    return NotificationSettingsResponse(
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_use_tls=settings.smtp_use_tls,
        smtp_username=settings.smtp_username,
        has_smtp_password=settings.smtp_password_secret_ref is not None,
        sender_email=settings.sender_email,
        sender_name=settings.sender_name,
        default_recipients=settings.default_recipients,
        escalation_recipients=settings.escalation_recipients,
    )


@router.get("/settings", response_model=NotificationSettingsResponse)
async def get_notification_settings(
    db: AsyncSession = Depends(get_db), _actor=Depends(require_permission("notification:configure"))
) -> NotificationSettingsResponse:
    settings = await _build_settings_service(db).get()
    return _to_response(settings)


@router.put("/settings", response_model=NotificationSettingsResponse)
async def update_notification_settings(
    payload: UpdateNotificationSettingsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    actor=Depends(require_permission("notification:configure")),
) -> NotificationSettingsResponse:
    service = _build_settings_service(db)
    settings = await service.update(
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_use_tls=payload.smtp_use_tls,
        smtp_username=payload.smtp_username,
        smtp_password=payload.smtp_password,
        sender_email=payload.sender_email,
        sender_name=payload.sender_name,
        default_recipients=payload.default_recipients,
        escalation_recipients=payload.escalation_recipients,
        actor_user_id=actor.id,
        ip_address=get_client_ip(request),
    )
    return _to_response(settings)


@router.get("/events", response_model=list[NotificationEventResponse])
async def list_notification_events(
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _actor=Depends(require_permission("incident:view")),
) -> list[NotificationEventResponse]:
    """Delivery status per master plan Sec 19 - the dashboard must show this even (especially)
    when SMTP is failing, since the incident itself is never lost when email delivery is."""
    repo = NotificationEventRepository(db)
    events = await repo.list_for_incident(incident_id) if incident_id else await repo.list_recent(limit=limit)
    return [
        NotificationEventResponse(
            id=e.id,
            incident_id=e.incident_id,
            severity=e.severity,
            recipients=e.recipients,
            subject=e.subject,
            body_text=e.body_text,
            status=e.status,
            attempt_count=e.attempt_count,
            last_attempt_at=e.last_attempt_at,
            last_error_category=e.last_error_category,
            sent_at=e.sent_at,
            created_at=e.created_at,
        )
        for e in events
    ]
