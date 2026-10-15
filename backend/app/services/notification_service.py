"""Async notification pipeline (master plan Sec 18): create -> queue -> process
asynchronously -> send -> retry on failure -> record delivery status. An incident is never
lost because SMTP failed - the notification_event just sits in RETRYING/FAILED, visible via
the API, while the incident itself is untouched.
"""

from app.logging_config import get_logger
from app.models.base import ensure_aware, utcnow
from app.models.incident import Incident
from app.notifications.mailer import EmailSender
from app.notifications.mailer import send_email as real_send_email
from app.notifications.templates import render_incident_email
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.repositories.incident_repo import IncidentRepository
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository
from app.services.secret_service import SecretProvider

logger = get_logger(__name__)

NOTIFIABLE_SEVERITIES = {"HIGH", "CRITICAL"}
MAX_ATTEMPTS = 5
# Attempt N's minimum age (seconds) before it's eligible for attempt N+1 - index 0 is the
# gate before the 2nd attempt, since the 1st always fires immediately on creation.
RETRY_BACKOFF_SECONDS = [60, 300, 900, 3600]


class NotificationService:
    def __init__(
        self,
        settings_repo: NotificationSettingsRepository,
        event_repo: NotificationEventRepository,
        db_repo: MonitoredDatabaseRepository,
        incident_repo: IncidentRepository,
        secrets: SecretProvider,
        sender: EmailSender = real_send_email,
        dashboard_base_url: str = "http://localhost:5173",
    ):
        self.settings_repo = settings_repo
        self.event_repo = event_repo
        self.db_repo = db_repo
        self.incident_repo = incident_repo
        self.secrets = secrets
        self.sender = sender
        self.dashboard_base_url = dashboard_base_url

    async def notify_if_threshold_reached(self, incident: Incident) -> None:
        if incident.severity not in NOTIFIABLE_SEVERITIES:
            return

        dedupe_key = f"{incident.id}:{incident.severity}"
        if await self.event_repo.exists_for_dedupe_key(dedupe_key):
            return  # already alerted for this incident at this severity - don't repeat

        settings = await self.settings_repo.get_or_create()
        recipients = list(settings.default_recipients)
        if incident.severity == "CRITICAL":
            recipients += [r for r in settings.escalation_recipients if r not in recipients]
        if not recipients:
            logger.warning("notification_skipped_no_recipients", incident_id=incident.id, severity=incident.severity)
            return

        database = await self.db_repo.get(incident.monitored_database_id)
        if database is None:
            logger.error("notification_skipped_database_missing", incident_id=incident.id)
            return

        evidence_count = len(await self.incident_repo.list_events(incident.id))
        subject, body = render_incident_email(
            incident, database, evidence_count, f"{self.dashboard_base_url}/incidents"
        )

        await self.event_repo.create(
            incident_id=incident.id,
            dedupe_key=dedupe_key,
            severity=incident.severity,
            recipients=recipients,
            subject=subject,
            body_text=body,
        )
        logger.info("notification_queued", incident_id=incident.id, severity=incident.severity, recipient_count=len(recipients))

    async def process_pending(self) -> None:
        """Commits after every event, not once at the end of the batch (Master Plan Phase
        26: DR/recovery) - a crash between two events must never leave an event that was
        already sent over real SMTP still marked PENDING/RETRYING, which would resend it on
        the next tick. Committing per-item means a mid-batch crash can only cost the
        in-flight event's own progress (safe to retry - it never reached SENT), not redo
        already-completed sends."""
        candidates = await self.event_repo.list_pending_or_retrying(limit=50)
        for event in candidates:
            if not self._is_eligible_now(event):
                continue
            await self._attempt_send(event)
            await self.event_repo.db.commit()

    def _is_eligible_now(self, event) -> bool:
        if event.attempt_count == 0:
            return True
        if event.last_attempt_at is None:
            return True
        backoff_index = min(event.attempt_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)
        wait_seconds = RETRY_BACKOFF_SECONDS[backoff_index]
        elapsed = (utcnow() - ensure_aware(event.last_attempt_at)).total_seconds()
        return elapsed >= wait_seconds

    async def _attempt_send(self, event) -> None:
        settings = await self.settings_repo.get_or_create()

        if not settings.smtp_host or not settings.sender_email:
            await self.event_repo.mark_failed(event.id, category="not_configured", exhausted=True)
            logger.warning("notification_send_skipped_unconfigured", event_id=event.id)
            return

        password = None
        if settings.smtp_password_secret_ref:
            password = await self.secrets.get_secret(settings.smtp_password_secret_ref)

        sender_address = f"{settings.sender_name} <{settings.sender_email}>"
        try:
            await self.sender(
                host=settings.smtp_host,
                port=settings.smtp_port,
                use_tls=settings.smtp_use_tls,
                username=settings.smtp_username,
                password=password,
                sender=sender_address,
                recipients=event.recipients,
                subject=event.subject,
                body=event.body_text,
            )
            await self.event_repo.mark_sent(event.id)
            logger.info("notification_sent", event_id=event.id, incident_id=event.incident_id)
        except Exception as exc:
            exhausted = event.attempt_count + 1 >= MAX_ATTEMPTS
            await self.event_repo.mark_failed(event.id, category="smtp_error", exhausted=exhausted)
            logger.error(
                "notification_send_failed",
                event_id=event.id,
                incident_id=event.incident_id,
                error_type=type(exc).__name__,
                exhausted=exhausted,
            )
