"""Thin, mockable wrapper around python-oracledb.

Every call site depends on the `OracleConnector` protocol, not this module directly, so
tests substitute a fake connector instead of needing a real Oracle instance. Never build a
connect_string, DSN, or error message here from anything an OracleConnectError caller
should see raw - the driver's exception text can embed internal hostnames/ports, so it is
logged server-side only (see services/oracle_capability_service.py) and never returned
verbatim to an API response.
"""

from dataclasses import dataclass
from typing import Protocol

import oracledb

# ORA- error codes mapped to a small, safe-to-expose category. Anything not listed here
# collapses to "unknown_error" rather than leaking driver internals by omission.
_AUTH_FAILURE_CODES = {1017, 28000, 1005}  # invalid username/password; account locked; null password
_UNREACHABLE_CODES = {12154, 12514, 12541, 12170, 12545, 12560}  # TNS resolve/listener/timeout/protocol


@dataclass(frozen=True)
class OracleConnectionParams:
    host: str
    port: int
    service_name: str
    username: str
    password: str
    connect_timeout_seconds: int = 5


class OracleConnectError(Exception):
    CATEGORY_UNREACHABLE = "unreachable"
    CATEGORY_AUTH_FAILED = "authentication_failed"
    CATEGORY_UNKNOWN = "unknown_error"

    def __init__(self, category: str, message: str):
        self.category = category
        self.message = message
        super().__init__(message)


class OracleConnection(Protocol):
    def cursor(self) -> "OracleCursor": ...
    def close(self) -> None: ...


class OracleCursor(Protocol):
    def execute(self, statement: str) -> None: ...
    def fetchone(self) -> tuple | None: ...
    def fetchall(self) -> list[tuple]: ...


class OracleConnector(Protocol):
    def __call__(self, params: OracleConnectionParams) -> OracleConnection: ...


def connect(params: OracleConnectionParams) -> OracleConnection:
    """Real connection via python-oracledb (thin mode - no Oracle Instant Client needed)."""
    dsn = oracledb.makedsn(params.host, params.port, service_name=params.service_name)
    try:
        return oracledb.connect(
            user=params.username,
            password=params.password,
            dsn=dsn,
            tcp_connect_timeout=params.connect_timeout_seconds,
        )
    except oracledb.DatabaseError as exc:
        (error_obj,) = exc.args
        code = getattr(error_obj, "code", None)
        full_code = getattr(error_obj, "full_code", "") or ""
        if code in _AUTH_FAILURE_CODES:
            raise OracleConnectError(OracleConnectError.CATEGORY_AUTH_FAILED, str(exc)) from exc
        # DPY-* codes are python-oracledb's own driver/network-layer errors (thin-mode TCP
        # connect refused/timed out, DNS resolution failure, ...) - raised before the
        # connection ever reaches an Oracle listener, so `code` (the ORA- error number) is
        # always 0 for these and never appears in _UNREACHABLE_CODES. An authentication
        # failure can only happen after a successful TCP handshake, so it always surfaces as
        # a server-returned ORA- code instead - a DPY- code is unambiguously "unreachable".
        if code in _UNREACHABLE_CODES or full_code.startswith("DPY-"):
            raise OracleConnectError(OracleConnectError.CATEGORY_UNREACHABLE, str(exc)) from exc
        raise OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, str(exc)) from exc
    except Exception as exc:
        raise OracleConnectError(OracleConnectError.CATEGORY_UNKNOWN, str(exc)) from exc


def get_oracle_connector() -> OracleConnector:
    """FastAPI dependency provider - overridden with a fake in tests
    (app.dependency_overrides[get_oracle_connector] = ...) since no real Oracle instance
    is available for automated testing."""
    return connect
