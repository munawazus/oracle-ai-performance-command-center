"""Shared by every service that needs to open a real Oracle connection (capability
detection, connection testing, the collector) - one place that resolves a registered
database + its stored credential into connection parameters."""

from app.exceptions import NotFoundError
from app.oracle.connector import OracleConnectError, OracleConnectionParams
from app.repositories.database_repo import DatabaseCredentialRepository, MonitoredDatabaseRepository
from app.services.secret_service import SecretProvider

PUBLIC_MESSAGES = {
    OracleConnectError.CATEGORY_AUTH_FAILED: "The stored username/password was rejected by the database.",
    OracleConnectError.CATEGORY_UNREACHABLE: "Could not reach the database at the configured host and port.",
    OracleConnectError.CATEGORY_UNKNOWN: "The database rejected the connection for an unspecified reason.",
}


def public_message(category: str) -> str:
    return PUBLIC_MESSAGES.get(category, PUBLIC_MESSAGES[OracleConnectError.CATEGORY_UNKNOWN])


async def build_connection_params(
    db_repo: MonitoredDatabaseRepository,
    cred_repo: DatabaseCredentialRepository,
    secrets: SecretProvider,
    database_id: str,
) -> OracleConnectionParams:
    database = await db_repo.get(database_id)
    if database is None:
        raise NotFoundError("Database not found.")
    credential = await cred_repo.get_by_database_id(database_id)
    if credential is None:
        raise NotFoundError("No credential is stored for this database.")
    password = await secrets.get_secret(credential.secret_ref)
    return OracleConnectionParams(
        host=database.host,
        port=database.port,
        service_name=database.service_name,
        username=credential.username,
        password=password,
    )
