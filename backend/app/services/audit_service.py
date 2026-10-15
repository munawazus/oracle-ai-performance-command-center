from app.models.audit import AuditEvent
from app.repositories.audit_repo import AuditRepository

# Keys that must never appear in audit metadata, even by accident.
_FORBIDDEN_METADATA_KEYS = {"password", "secret", "token", "ciphertext", "access_token", "refresh_token"}


class AuditService:
    def __init__(self, repo: AuditRepository):
        self.repo = repo

    async def record(
        self,
        *,
        action: str,
        success: bool,
        actor_user_id: str | None = None,
        actor_email: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        ip_address: str | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        if metadata:
            leaked = _FORBIDDEN_METADATA_KEYS & {k.lower() for k in metadata}
            if leaked:
                raise ValueError(f"refusing to audit-log forbidden keys: {leaked}")
        return await self.repo.create(
            action=action,
            success=success,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            metadata=metadata,
        )

    async def list_events(self, limit: int = 100, offset: int = 0, action_prefix: str | None = None):
        return await self.repo.list_events(limit=limit, offset=offset, action_prefix=action_prefix)
