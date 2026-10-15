from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.database import DatabaseCapability, DatabaseCredential, MonitoredDatabase
from app.models.database_group import DatabaseGroup, DatabaseGroupMembership


class MonitoredDatabaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(
        self, limit: int = 200, offset: int = 0, database_ids: list[str] | None = None
    ) -> list[MonitoredDatabase]:
        """`database_ids`, when given, restricts the result to that set - used by fleet
        group/tag filtering (app/services/database_service.py resolves the id set first via
        relational queries, then this narrows the final row fetch). An empty list is a
        legitimate "matched nothing" result, not "no filter" - only `None` means unfiltered."""
        query = select(MonitoredDatabase).order_by(MonitoredDatabase.name).limit(limit).offset(offset)
        if database_ids is not None:
            query = query.where(MonitoredDatabase.id.in_(database_ids))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_active_ids(self) -> list[str]:
        """Every active database id, unpaginated for the caller - used by the fleet-wide
        collector/analytics jobs (app/collectors/scheduler.py), where list_all()'s default
        page-size limit would silently stop polling once the fleet exceeds it. Pages
        internally in fixed-size batches so a large fleet never needs one unbounded query."""
        ids: list[str] = []
        offset = 0
        page_size = 500
        while True:
            query = (
                select(MonitoredDatabase.id)
                .where(MonitoredDatabase.is_active.is_(True))
                .order_by(MonitoredDatabase.name)
                .limit(page_size)
                .offset(offset)
            )
            result = await self.db.execute(query)
            page = list(result.scalars().all())
            ids.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return ids

    async def get(self, database_id: str) -> MonitoredDatabase | None:
        return await self.db.get(MonitoredDatabase, database_id)

    async def get_by_name(self, name: str) -> MonitoredDatabase | None:
        result = await self.db.execute(select(MonitoredDatabase).where(MonitoredDatabase.name == name))
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        host: str,
        port: int,
        service_name: str,
        edition_hint: str,
        created_by_user_id: str,
        tags: list[str] | None = None,
    ) -> MonitoredDatabase:
        database = MonitoredDatabase(
            name=name,
            host=host,
            port=port,
            service_name=service_name,
            edition_hint=edition_hint,
            created_by_user_id=created_by_user_id,
            tags=tags or [],
        )
        self.db.add(database)
        await self.db.flush()
        return database

    async def update(self, database: MonitoredDatabase, **fields) -> MonitoredDatabase:
        for key, value in fields.items():
            if value is not None:
                setattr(database, key, value)
        await self.db.flush()
        return database

    async def set_capacity_thresholds(
        self,
        database: MonitoredDatabase,
        *,
        warn_pct_used: float | None,
        critical_pct_used: float | None,
        warn_days_to_full: float | None,
        critical_days_to_full: float | None,
    ) -> MonitoredDatabase:
        """Unlike update()'s **fields (None means "don't touch"), every field here is always
        applied, None included - explicitly clearing an override back to the global default is
        a first-class action for this feature, not just setting a new number."""
        database.capacity_warn_pct_used = warn_pct_used
        database.capacity_critical_pct_used = critical_pct_used
        database.capacity_warn_days_to_full = warn_days_to_full
        database.capacity_critical_days_to_full = critical_days_to_full
        await self.db.flush()
        return database

    async def delete(self, database: MonitoredDatabase) -> None:
        await self.db.delete(database)
        await self.db.flush()


class DatabaseCredentialRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_database_id(self, monitored_database_id: str) -> DatabaseCredential | None:
        result = await self.db.execute(
            select(DatabaseCredential).where(DatabaseCredential.monitored_database_id == monitored_database_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self, *, monitored_database_id: str, username: str, secret_ref: str, created_by_user_id: str
    ) -> DatabaseCredential:
        credential = DatabaseCredential(
            monitored_database_id=monitored_database_id,
            username=username,
            secret_ref=secret_ref,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(credential)
        await self.db.flush()
        return credential

    async def update_username(self, credential: DatabaseCredential, username: str) -> None:
        credential.username = username
        await self.db.flush()

    async def mark_rotated(self, credential: DatabaseCredential) -> None:
        credential.rotated_at = utcnow()
        await self.db.flush()


class CapabilityRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_database_id(self, monitored_database_id: str) -> DatabaseCapability | None:
        result = await self.db.execute(
            select(DatabaseCapability).where(DatabaseCapability.monitored_database_id == monitored_database_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        monitored_database_id: str,
        detected_version: str | None,
        detected_edition: str | None,
        is_rac: bool | None,
        statspack_available: bool | None,
        privilege_report: dict,
        cpu_count: int | None = None,
    ) -> DatabaseCapability:
        existing = await self.get_by_database_id(monitored_database_id)
        if existing is None:
            existing = DatabaseCapability(monitored_database_id=monitored_database_id)
            self.db.add(existing)

        existing.detected_version = detected_version
        existing.detected_edition = detected_edition
        existing.is_rac = is_rac
        existing.statspack_available = statspack_available
        existing.privilege_report = privilege_report
        existing.cpu_count = cpu_count
        existing.last_probed_at = utcnow()

        await self.db.flush()
        return existing


class DatabaseGroupRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[DatabaseGroup]:
        result = await self.db.execute(select(DatabaseGroup).order_by(DatabaseGroup.name))
        return list(result.scalars().all())

    async def get(self, group_id: str) -> DatabaseGroup | None:
        return await self.db.get(DatabaseGroup, group_id)

    async def get_by_name(self, name: str) -> DatabaseGroup | None:
        result = await self.db.execute(select(DatabaseGroup).where(DatabaseGroup.name == name))
        return result.scalar_one_or_none()

    async def create(self, *, name: str, description: str) -> DatabaseGroup:
        group = DatabaseGroup(name=name, description=description)
        self.db.add(group)
        await self.db.flush()
        return group

    async def update(self, group: DatabaseGroup, *, name: str | None, description: str | None) -> DatabaseGroup:
        if name is not None:
            group.name = name
        if description is not None:
            group.description = description
        await self.db.flush()
        return group

    async def delete(self, group: DatabaseGroup) -> None:
        await self.db.delete(group)
        await self.db.flush()

    async def group_ids_for_database(self, monitored_database_id: str) -> list[str]:
        result = await self.db.execute(
            select(DatabaseGroupMembership.group_id).where(
                DatabaseGroupMembership.monitored_database_id == monitored_database_id
            )
        )
        return [row[0] for row in result.all()]

    async def database_ids_for_group(self, group_id: str) -> list[str]:
        result = await self.db.execute(
            select(DatabaseGroupMembership.monitored_database_id).where(DatabaseGroupMembership.group_id == group_id)
        )
        return [row[0] for row in result.all()]

    async def set_memberships(self, monitored_database_id: str, group_ids: list[str]) -> None:
        """Replace-the-full-set semantics - simpler and less error-prone for a UI checkbox
        list than incremental add/remove endpoints."""
        existing = await self.db.execute(
            select(DatabaseGroupMembership).where(
                DatabaseGroupMembership.monitored_database_id == monitored_database_id
            )
        )
        for row in existing.scalars().all():
            await self.db.delete(row)
        await self.db.flush()

        for group_id in group_ids:
            self.db.add(DatabaseGroupMembership(monitored_database_id=monitored_database_id, group_id=group_id))
        await self.db.flush()
