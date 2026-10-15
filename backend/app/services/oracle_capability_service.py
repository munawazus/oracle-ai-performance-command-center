import asyncio
from dataclasses import dataclass

from app.exceptions import UpstreamUnavailableError
from app.logging_config import get_logger
from app.models.database import DatabaseCapability
from app.oracle.capability_probe import probe_capabilities
from app.oracle.connector import OracleConnectError, OracleConnector
from app.oracle.params import build_connection_params, public_message
from app.repositories.database_repo import CapabilityRepository, DatabaseCredentialRepository, MonitoredDatabaseRepository
from app.services.audit_service import AuditService
from app.services.secret_service import SecretProvider

logger = get_logger(__name__)


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    category: str | None
    message: str


class OracleCapabilityService:
    """Never returns a raw driver exception message to a caller - only the fixed,
    generic messages from app.oracle.params.public_message(). The real exception text is
    logged server-side only (it can embed internal hostnames or driver internals).

    Every real Oracle call below (connect, probe_capabilities' queries, close) is
    synchronous/blocking python-oracledb code, wrapped in `asyncio.to_thread` - otherwise it
    runs directly on the single Uvicorn event loop and freezes the whole application for
    every user for as long as the connection attempt takes (Enterprise Readiness Plan F.1 -
    proved live), the same fix applied to every collector in collector_service.py."""

    def __init__(
        self,
        db_repo: MonitoredDatabaseRepository,
        cred_repo: DatabaseCredentialRepository,
        capability_repo: CapabilityRepository,
        secrets: SecretProvider,
        audit: AuditService,
        connector: OracleConnector,
    ):
        self.db_repo = db_repo
        self.cred_repo = cred_repo
        self.capability_repo = capability_repo
        self.secrets = secrets
        self.audit = audit
        self.connector = connector

    async def test_connection(
        self, database_id: str, *, actor_user_id: str, ip_address: str | None
    ) -> ConnectionTestResult:
        params = await build_connection_params(self.db_repo, self.cred_repo, self.secrets, database_id)
        try:
            conn = await asyncio.to_thread(self.connector, params)
            await asyncio.to_thread(conn.close)
            result = ConnectionTestResult(ok=True, category=None, message="Connected successfully.")
        except OracleConnectError as exc:
            logger.warning("oracle_connection_test_failed", database_id=database_id, category=exc.category)
            result = ConnectionTestResult(ok=False, category=exc.category, message=public_message(exc.category))

        await self.audit.record(
            action="database.connection_tested",
            success=result.ok,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database_id,
            ip_address=ip_address,
            metadata={"category": result.category} if result.category else None,
        )
        return result

    async def detect_capabilities(
        self, database_id: str, *, actor_user_id: str, ip_address: str | None
    ) -> DatabaseCapability:
        params = await build_connection_params(self.db_repo, self.cred_repo, self.secrets, database_id)
        try:
            conn = await asyncio.to_thread(self.connector, params)
        except OracleConnectError as exc:
            logger.warning("oracle_capability_probe_connect_failed", database_id=database_id, category=exc.category)
            await self.audit.record(
                action="database.capabilities_detected",
                success=False,
                actor_user_id=actor_user_id,
                target_type="monitored_database",
                target_id=database_id,
                ip_address=ip_address,
                metadata={"category": exc.category},
            )
            raise UpstreamUnavailableError(public_message(exc.category)) from exc

        try:
            profile = await asyncio.to_thread(probe_capabilities, conn)
        finally:
            await asyncio.to_thread(conn.close)

        record = await self.capability_repo.upsert(
            monitored_database_id=database_id,
            detected_version=profile.detected_version,
            detected_edition=profile.detected_edition,
            is_rac=profile.is_rac,
            statspack_available=profile.statspack_available,
            privilege_report=profile.privilege_report,
            cpu_count=profile.cpu_count,
        )

        missing_views = sorted(view for view, ok in profile.privilege_report.items() if not ok)
        await self.audit.record(
            action="database.capabilities_detected",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database",
            target_id=database_id,
            ip_address=ip_address,
            metadata={
                "detected_edition": profile.detected_edition,
                "is_rac": profile.is_rac,
                "statspack_available": profile.statspack_available,
                "missing_views": missing_views,
            },
        )
        return record
