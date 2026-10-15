from app.models.notification import NotificationSettings
from app.repositories.notification_repo import NotificationSettingsRepository
from app.services.audit_service import AuditService
from app.services.secret_service import SecretProvider


class NotificationSettingsService:
    """The SMTP password is never returned to a client and never logged - only a
    has_smtp_password boolean, same treatment as Oracle monitoring-account credentials."""

    def __init__(self, repo: NotificationSettingsRepository, secrets: SecretProvider, audit: AuditService):
        self.repo = repo
        self.secrets = secrets
        self.audit = audit

    async def get(self) -> NotificationSettings:
        return await self.repo.get_or_create()

    async def update(
        self,
        *,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_use_tls: bool | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        sender_email: str | None = None,
        sender_name: str | None = None,
        default_recipients: list[str] | None = None,
        escalation_recipients: list[str] | None = None,
        actor_user_id: str,
        ip_address: str | None,
    ) -> NotificationSettings:
        current = await self.repo.get_or_create()

        password_secret_ref = None
        if smtp_password is not None:
            if current.smtp_password_secret_ref:
                await self.secrets.rotate_secret(current.smtp_password_secret_ref, smtp_password)
                password_secret_ref = current.smtp_password_secret_ref
            else:
                password_secret_ref = await self.secrets.set_secret(smtp_password, actor_user_id)

        updated = await self.repo.update(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_use_tls=smtp_use_tls,
            smtp_username=smtp_username,
            smtp_password_secret_ref=password_secret_ref,
            sender_email=sender_email,
            sender_name=sender_name,
            default_recipients=default_recipients,
            escalation_recipients=escalation_recipients,
        )

        await self.audit.record(
            action="notification.settings_updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="notification_settings",
            target_id=updated.id,
            ip_address=ip_address,
            metadata={
                "smtp_host": updated.smtp_host,
                "smtp_port": updated.smtp_port,
                "sender_email": updated.sender_email,
                "default_recipient_count": len(updated.default_recipients),
                "escalation_recipient_count": len(updated.escalation_recipients),
                "password_rotated": smtp_password is not None,
            },
        )
        return updated
