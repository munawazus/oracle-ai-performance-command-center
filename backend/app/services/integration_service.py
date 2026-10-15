"""Async integration-delivery pipeline (master plan Sec 35/36 Phase 23) - the same
create -> queue -> process asynchronously -> send -> retry -> record shape as
app/services/notification_service.py's email pipeline, fired from the same trigger point
(AnalyticsService.run_once_for_database) at the same HIGH/CRITICAL threshold. An incident is
never affected by a webhook endpoint being down - the delivery just sits in
RETRYING/FAILED, visible via the API.
"""

from app.config import get_settings
from app.exceptions import NotFoundError, ValidationAppError
from app.integrations.payloads import build_payload
from app.integrations.webhook_sender import WebhookSendError, WebhookSender
from app.integrations.webhook_sender import send_webhook as real_send_webhook
from app.logging_config import get_logger
from app.models.base import ensure_aware, utcnow
from app.models.incident import Incident
from app.models.integration import INTEGRATION_TYPES, Integration
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.integration_repo import IntegrationDeliveryRepository, IntegrationRepository
from app.security.ssrf_guard import HostResolver, assert_target_url_is_safe, resolve_host_real
from app.services.audit_service import AuditService
from app.services.secret_service import SecretProvider

logger = get_logger(__name__)

NOTIFIABLE_SEVERITIES = {"HIGH", "CRITICAL"}
VALID_SEVERITIES = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = [60, 300, 900, 3600]


class IntegrationService:
    def __init__(
        self,
        integration_repo: IntegrationRepository,
        delivery_repo: IntegrationDeliveryRepository,
        db_repo: MonitoredDatabaseRepository,
        secrets: SecretProvider,
        audit: AuditService,
        sender: WebhookSender = real_send_webhook,
        resolve_host: HostResolver = resolve_host_real,
        dashboard_base_url: str = "http://localhost:5173",
    ):
        self.integration_repo = integration_repo
        self.delivery_repo = delivery_repo
        self.db_repo = db_repo
        self.secrets = secrets
        self.audit = audit
        self.sender = sender
        self.resolve_host = resolve_host
        self.dashboard_base_url = dashboard_base_url

    # -- Configuration (CRUD) ----------------------------------------------------------

    async def list_integrations(self) -> list[Integration]:
        return await self.integration_repo.list_all()

    async def get_integration(self, integration_id: str) -> Integration:
        integration = await self.integration_repo.get(integration_id)
        if integration is None:
            raise NotFoundError("Integration not found.")
        return integration

    async def create_integration(
        self,
        *,
        integration_type: str,
        name: str,
        target_url: str,
        secret: str | None,
        enabled: bool,
        trigger_severities: list[str],
        actor_user_id: str,
        ip_address: str | None,
    ) -> Integration:
        if integration_type not in INTEGRATION_TYPES:
            raise ValidationAppError(f"type must be one of {INTEGRATION_TYPES}")
        _validate_severities(trigger_severities)
        await assert_target_url_is_safe(
            target_url, allow_internal=get_settings().allow_internal_integration_targets, resolve=self.resolve_host
        )

        secret_ref = await self.secrets.set_secret(secret, actor_user_id) if secret else None
        integration = await self.integration_repo.create(
            integration_type=integration_type,
            name=name,
            target_url=target_url,
            secret_ref=secret_ref,
            enabled=enabled,
            trigger_severities=trigger_severities,
            created_by_user_id=actor_user_id,
        )
        await self.audit.record(
            action="integration.created",
            success=True,
            actor_user_id=actor_user_id,
            target_type="integration",
            target_id=integration.id,
            ip_address=ip_address,
            metadata={"type": integration_type, "name": name, "trigger_severities": trigger_severities},
        )
        return integration

    async def update_integration(
        self,
        integration_id: str,
        *,
        name: str | None,
        target_url: str | None,
        secret: str | None,
        enabled: bool | None,
        trigger_severities: list[str] | None,
        actor_user_id: str,
        ip_address: str | None,
    ) -> Integration:
        integration = await self.get_integration(integration_id)
        if trigger_severities is not None:
            _validate_severities(trigger_severities)
        if target_url is not None:
            await assert_target_url_is_safe(
                target_url,
                allow_internal=get_settings().allow_internal_integration_targets,
                resolve=self.resolve_host,
            )

        secret_ref = None
        if secret is not None:
            if integration.secret_ref:
                await self.secrets.rotate_secret(integration.secret_ref, secret)
                secret_ref = integration.secret_ref
            else:
                secret_ref = await self.secrets.set_secret(secret, actor_user_id)

        updated = await self.integration_repo.update(
            integration,
            name=name,
            target_url=target_url,
            secret_ref=secret_ref,
            enabled=enabled,
            trigger_severities=trigger_severities,
        )
        await self.audit.record(
            action="integration.updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="integration",
            target_id=updated.id,
            ip_address=ip_address,
            metadata={
                k: v
                for k, v in {
                    "name": name,
                    "enabled": enabled,
                    "trigger_severities": trigger_severities,
                    "secret_rotated": secret is not None,
                }.items()
                if v is not None
            },
        )
        return updated

    async def delete_integration(self, integration_id: str, *, actor_user_id: str, ip_address: str | None) -> None:
        integration = await self.get_integration(integration_id)
        secret_ref = integration.secret_ref
        await self.integration_repo.delete(integration)
        if secret_ref:
            await self.secrets.delete_secret(secret_ref)
        await self.audit.record(
            action="integration.deleted",
            success=True,
            actor_user_id=actor_user_id,
            target_type="integration",
            target_id=integration_id,
            ip_address=ip_address,
            metadata={"name": integration.name},
        )

    async def test_integration(self, integration_id: str, *, actor_user_id: str, ip_address: str | None) -> dict:
        """Fires one real, synchronous request with a clearly-marked test payload - never
        queued, never retried, never counted against delivery dedup. Always returns 200 at
        the API layer with success/failure in the body, same as
        OracleCapabilityService.test_connection - a failed test is an expected diagnostic
        outcome, not a server error."""
        integration = await self.get_integration(integration_id)
        secret = await self.secrets.get_secret(integration.secret_ref) if integration.secret_ref else None
        payload = {
            "event": "integration.test",
            "message": f"Test delivery from Oracle AI Performance Command Center ({integration.name}).",
        }
        try:
            result = await self.sender(url=integration.target_url, payload=payload, secret=secret)
            await self.audit.record(
                action="integration.tested",
                success=True,
                actor_user_id=actor_user_id,
                target_type="integration",
                target_id=integration.id,
                ip_address=ip_address,
                metadata={"status_code": result.status_code},
            )
            return {"ok": True, "status_code": result.status_code, "message": "Delivered."}
        except WebhookSendError as exc:
            await self.audit.record(
                action="integration.tested",
                success=False,
                actor_user_id=actor_user_id,
                target_type="integration",
                target_id=integration.id,
                ip_address=ip_address,
                metadata={"category": exc.category},
            )
            return {"ok": False, "status_code": None, "message": str(exc)}

    # -- Triggering + delivery ----------------------------------------------------------

    async def notify_if_threshold_reached(self, incident: Incident) -> None:
        if incident.severity not in NOTIFIABLE_SEVERITIES:
            return

        integrations = [i for i in await self.integration_repo.list_enabled() if incident.severity in i.trigger_severities]
        if not integrations:
            return

        database = await self.db_repo.get(incident.monitored_database_id)
        if database is None:
            logger.error("integration_delivery_skipped_database_missing", incident_id=incident.id)
            return

        for integration in integrations:
            dedupe_key = f"{integration.id}:{incident.id}:{incident.severity}"
            if await self.delivery_repo.exists_for_dedupe_key(dedupe_key):
                continue
            payload = build_payload(integration.type, incident, database, self.dashboard_base_url)
            await self.delivery_repo.create(
                integration_id=integration.id,
                incident_id=incident.id,
                event_type="incident_escalated",
                dedupe_key=dedupe_key,
                payload=payload,
            )
            logger.info("integration_delivery_queued", integration_id=integration.id, incident_id=incident.id)

    async def process_pending(self) -> None:
        """Commits after every delivery, not once at the end of the batch (Master Plan
        Phase 26: DR/recovery) - same reasoning as NotificationService.process_pending: a
        crash between two deliveries must never leave one that already reached a real
        webhook endpoint still marked PENDING/RETRYING, which would resend it on the next
        tick."""
        candidates = await self.delivery_repo.list_pending_or_retrying(limit=50)
        for delivery in candidates:
            if not self._is_eligible_now(delivery):
                continue
            await self._attempt_send(delivery)
            await self.delivery_repo.db.commit()

    def _is_eligible_now(self, delivery) -> bool:
        if delivery.attempt_count == 0:
            return True
        if delivery.last_attempt_at is None:
            return True
        backoff_index = min(delivery.attempt_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)
        wait_seconds = RETRY_BACKOFF_SECONDS[backoff_index]
        elapsed = (utcnow() - ensure_aware(delivery.last_attempt_at)).total_seconds()
        return elapsed >= wait_seconds

    async def _attempt_send(self, delivery) -> None:
        integration = await self.integration_repo.get(delivery.integration_id)
        if integration is None or not integration.enabled:
            await self.delivery_repo.mark_failed(delivery.id, category="integration_disabled", exhausted=True)
            logger.warning("integration_delivery_skipped_disabled", delivery_id=delivery.id)
            return

        secret = await self.secrets.get_secret(integration.secret_ref) if integration.secret_ref else None
        try:
            result = await self.sender(url=integration.target_url, payload=delivery.payload, secret=secret)
            await self.delivery_repo.mark_sent(
                delivery.id, response_status_code=result.status_code, response_snippet=result.response_snippet
            )
            logger.info("integration_delivery_sent", delivery_id=delivery.id, incident_id=delivery.incident_id)
        except WebhookSendError as exc:
            exhausted = delivery.attempt_count + 1 >= MAX_ATTEMPTS
            await self.delivery_repo.mark_failed(delivery.id, category=exc.category, exhausted=exhausted)
            logger.error(
                "integration_delivery_failed",
                delivery_id=delivery.id,
                incident_id=delivery.incident_id,
                category=exc.category,
                exhausted=exhausted,
            )


def _validate_severities(severities: list[str]) -> None:
    invalid = set(severities) - VALID_SEVERITIES
    if invalid:
        raise ValidationAppError(f"trigger_severities contains invalid value(s): {sorted(invalid)}")
