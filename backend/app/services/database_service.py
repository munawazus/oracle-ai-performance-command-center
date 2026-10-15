from app.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.database import MonitoredDatabase
from app.models.database_group import DatabaseGroup
from app.repositories.database_repo import (
    DatabaseCredentialRepository,
    DatabaseGroupRepository,
    MonitoredDatabaseRepository,
)
from app.services.audit_service import AuditService
from app.services.secret_service import SecretProvider

VALID_EDITION_HINTS = {"SE2", "EE", "UNKNOWN"}


class DatabaseService:
    """Registers monitored databases and their credentials, and manages fleet-management
    concerns on top of them (Master Plan Phase 22): tags, named groups, and group/tag
    fleet filters.

    Deliberately does not attempt any live Oracle connection - connectivity/capability
    probing belongs to the collector phase, not the platform shell.
    """

    def __init__(
        self,
        db_repo: MonitoredDatabaseRepository,
        cred_repo: DatabaseCredentialRepository,
        group_repo: DatabaseGroupRepository,
        secrets: SecretProvider,
        audit: AuditService,
    ):
        self.db_repo = db_repo
        self.cred_repo = cred_repo
        self.group_repo = group_repo
        self.secrets = secrets
        self.audit = audit

    async def list_databases(
        self, *, group_id: str | None = None, tag: str | None = None
    ) -> list[MonitoredDatabase]:
        """Tag filtering happens in Python rather than a JSON-containment SQL operator,
        since this platform's JSON columns must stay portable between SQLite (tests/local
        dev) and Postgres (production) - the two use incompatible JSON query syntax. Fleet
        sizes here are small enough (per the master plan's own SE2/EE observability scope)
        that this is not a real cost."""
        database_ids: list[str] | None = None
        if group_id is not None:
            database_ids = await self.group_repo.database_ids_for_group(group_id)

        databases = await self.db_repo.list_all(database_ids=database_ids)
        if tag is not None:
            normalized = tag.strip().lower()
            databases = [d for d in databases if normalized in d.tags]
        return databases

    async def get_database(self, database_id: str) -> MonitoredDatabase:
        database = await self.db_repo.get(database_id)
        if database is None:
            raise NotFoundError("Database not found.")
        return database

    async def create_database(
        self,
        *,
        name: str,
        host: str,
        port: int,
        service_name: str,
        edition_hint: str,
        username: str,
        password: str,
        actor_user_id: str,
        ip_address: str | None,
        tags: list[str] | None = None,
    ) -> MonitoredDatabase:
        if await self.db_repo.get_by_name(name):
            raise ConflictError("A database with this name is already registered.")

        database = await self.db_repo.create(
            name=name,
            host=host,
            port=port,
            service_name=service_name,
            edition_hint=edition_hint,
            created_by_user_id=actor_user_id,
            tags=tags,
        )
        secret_ref = await self.secrets.set_secret(password, actor_user_id)
        await self.cred_repo.create(
            monitored_database_id=database.id,
            username=username,
            secret_ref=secret_ref,
            created_by_user_id=actor_user_id,
        )
        await self.audit.record(
            action="database.created",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database.id,
            ip_address=ip_address,
            metadata={"name": name, "host": host, "port": port, "edition_hint": edition_hint},
        )
        await self.audit.record(
            action="database.credential_set",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database.id,
            ip_address=ip_address,
            metadata={"username": username},
        )
        return database

    async def bulk_create_databases(
        self, items: list[dict], *, actor_user_id: str, ip_address: str | None
    ) -> list[dict]:
        """One failure never aborts the batch ("bulk onboarding where safe" - master plan
        Sec 35 Phase 22). Each row goes through the exact same validation/secret-storage/
        audit path as a single create - there is no bulk-only shortcut around security."""
        results = []
        for index, item in enumerate(items):
            try:
                database = await self.create_database(
                    name=item["name"],
                    host=item["host"],
                    port=item["port"],
                    service_name=item["service_name"],
                    edition_hint=item["edition_hint"],
                    username=item["username"],
                    password=item["password"],
                    tags=item.get("tags"),
                    actor_user_id=actor_user_id,
                    ip_address=ip_address,
                )
                results.append({"index": index, "status": "created", "database": database, "error": None})
            except (ConflictError, ValidationAppError) as exc:
                results.append({"index": index, "status": "failed", "database": None, "error": exc.message})
        await self.audit.record(
            action="database.bulk_created",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=None,
            ip_address=ip_address,
            metadata={
                "requested": len(items),
                "created": sum(1 for r in results if r["status"] == "created"),
                "failed": sum(1 for r in results if r["status"] == "failed"),
            },
        )
        return results

    async def update_database(
        self,
        database_id: str,
        *,
        name: str | None = None,
        host: str | None = None,
        port: int | None = None,
        service_name: str | None = None,
        edition_hint: str | None = None,
        is_active: bool | None = None,
        username: str | None = None,
        password: str | None = None,
        tags: list[str] | None = None,
        actor_user_id: str,
        ip_address: str | None,
    ) -> MonitoredDatabase:
        database = await self.get_database(database_id)
        await self.db_repo.update(
            database,
            name=name,
            host=host,
            port=port,
            service_name=service_name,
            edition_hint=edition_hint,
            is_active=is_active,
            tags=tags,
        )
        await self.audit.record(
            action="database.updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database.id,
            ip_address=ip_address,
            metadata={
                k: v
                for k, v in {
                    "name": name,
                    "host": host,
                    "port": port,
                    "edition_hint": edition_hint,
                    "is_active": is_active,
                    "tags": tags,
                }.items()
                if v is not None
            },
        )

        if username is not None or password is not None:
            credential = await self.cred_repo.get_by_database_id(database_id)
            if credential is None:
                raise NotFoundError("Credential record not found for this database.")
            if username is not None:
                await self.cred_repo.update_username(credential, username)
            if password is not None:
                await self.secrets.rotate_secret(credential.secret_ref, password)
                await self.cred_repo.mark_rotated(credential)
            await self.audit.record(
                action="database.credential_rotated",
                success=True,
                actor_user_id=actor_user_id,
                target_type="monitored_database",
                target_id=database.id,
                ip_address=ip_address,
                metadata={"username_changed": username is not None, "password_changed": password is not None},
            )

        return database

    async def set_capacity_thresholds(
        self,
        database_id: str,
        *,
        warn_pct_used: float | None,
        critical_pct_used: float | None,
        warn_days_to_full: float | None,
        critical_days_to_full: float | None,
        actor_user_id: str,
        ip_address: str | None,
    ) -> MonitoredDatabase:
        database = await self.get_database(database_id)
        await self.db_repo.set_capacity_thresholds(
            database,
            warn_pct_used=warn_pct_used,
            critical_pct_used=critical_pct_used,
            warn_days_to_full=warn_days_to_full,
            critical_days_to_full=critical_days_to_full,
        )
        await self.audit.record(
            action="database.capacity_thresholds_updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database.id,
            ip_address=ip_address,
            metadata={
                "warn_pct_used": warn_pct_used,
                "critical_pct_used": critical_pct_used,
                "warn_days_to_full": warn_days_to_full,
                "critical_days_to_full": critical_days_to_full,
            },
        )
        return database

    async def delete_database(self, database_id: str, *, actor_user_id: str, ip_address: str | None) -> None:
        """Deletes the database row first, then the secret - not the reverse. The credential
        row's secret_ref is ON DELETE RESTRICT (a secret must never be deletable while
        something still points at it), so deleting the secret first would violate that
        constraint under real foreign-key enforcement (always on in Postgres; only enabled
        for SQLite via app/db.py's connect-time PRAGMA). Deleting the database first lets its
        own ON DELETE CASCADE remove the credential row, which then makes the secret
        deletion safe."""
        database = await self.get_database(database_id)
        credential = await self.cred_repo.get_by_database_id(database_id)
        secret_ref = credential.secret_ref if credential else None
        await self.db_repo.delete(database)
        if secret_ref:
            await self.secrets.delete_secret(secret_ref)
        await self.audit.record(
            action="database.deleted",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database_id,
            ip_address=ip_address,
            metadata={"name": database.name},
        )

    # -- Fleet groups -----------------------------------------------------------------

    async def list_groups(self) -> list[dict]:
        groups = await self.group_repo.list_all()
        counts = []
        for group in groups:
            member_ids = await self.group_repo.database_ids_for_group(group.id)
            counts.append({"group": group, "database_count": len(member_ids)})
        return counts

    async def create_group(
        self, *, name: str, description: str, actor_user_id: str, ip_address: str | None
    ) -> DatabaseGroup:
        if await self.group_repo.get_by_name(name):
            raise ConflictError("A database group with this name already exists.")
        group = await self.group_repo.create(name=name, description=description)
        await self.audit.record(
            action="database_group.created",
            success=True,
            actor_user_id=actor_user_id,
            target_type="database_group",
            target_id=group.id,
            ip_address=ip_address,
            metadata={"name": name},
        )
        return group

    async def update_group(
        self,
        group_id: str,
        *,
        name: str | None,
        description: str | None,
        actor_user_id: str,
        ip_address: str | None,
    ) -> DatabaseGroup:
        group = await self.group_repo.get(group_id)
        if group is None:
            raise NotFoundError("Database group not found.")
        if name is not None and name != group.name and await self.group_repo.get_by_name(name):
            raise ConflictError("A database group with this name already exists.")
        group = await self.group_repo.update(group, name=name, description=description)
        await self.audit.record(
            action="database_group.updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="database_group",
            target_id=group.id,
            ip_address=ip_address,
            metadata={k: v for k, v in {"name": name, "description": description}.items() if v is not None},
        )
        return group

    async def delete_group(self, group_id: str, *, actor_user_id: str, ip_address: str | None) -> None:
        group = await self.group_repo.get(group_id)
        if group is None:
            raise NotFoundError("Database group not found.")
        await self.group_repo.delete(group)
        await self.audit.record(
            action="database_group.deleted",
            success=True,
            actor_user_id=actor_user_id,
            target_type="database_group",
            target_id=group_id,
            ip_address=ip_address,
            metadata={"name": group.name},
        )

    async def get_group_memberships(self, database_id: str) -> list[str]:
        await self.get_database(database_id)  # 404s if the database itself doesn't exist
        return await self.group_repo.group_ids_for_database(database_id)

    async def set_group_memberships(
        self, database_id: str, group_ids: list[str], *, actor_user_id: str, ip_address: str | None
    ) -> list[str]:
        await self.get_database(database_id)  # 404s if the database itself doesn't exist
        for group_id in group_ids:
            if await self.group_repo.get(group_id) is None:
                raise ValidationAppError(f"group_id '{group_id}' does not refer to a known database group.")
        await self.group_repo.set_memberships(database_id, group_ids)
        await self.audit.record(
            action="database.groups_updated",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database_id,
            ip_address=ip_address,
            metadata={"group_ids": group_ids},
        )
        return group_ids
