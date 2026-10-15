import re
from dataclasses import dataclass

from app.oracle.allowlist import ALLOWLISTED_VIEWS, STATSPACK_ALLOWLISTED_TABLES
from app.oracle.connector import OracleConnection

_VERSION_RE = re.compile(r"Release\s+([\d.]+)")


@dataclass(frozen=True)
class CapabilityProbeResult:
    detected_version: str | None
    detected_edition: str  # "SE2" | "EE" | "UNKNOWN"
    is_rac: bool | None
    # Enterprise Readiness Plan D.3: True only if PERFSTAT.STATS$SNAPSHOT is actually
    # queryable - not merely that a PERFSTAT user exists (see _check_statspack_available).
    statspack_available: bool | None
    privilege_report: dict[str, bool]
    # Wait Analysis's CPU-core reference line (real Oracle ASH Analytics convention: sessions
    # queue for CPU once Average Active Sessions on CPU exceeds this). Static instance
    # metadata, not a per-tick telemetry value, so it lives here alongside version/edition/RAC
    # rather than on a time-series table.
    cpu_count: int | None


def detect_edition(banner: str) -> str:
    upper = banner.upper()
    if "STANDARD EDITION 2" in upper:
        return "SE2"
    if "ENTERPRISE EDITION" in upper:
        return "EE"
    return "UNKNOWN"


def parse_version(banner: str) -> str | None:
    match = _VERSION_RE.search(banner)
    return match.group(1) if match else (banner[:100] or None)


def _fetch_scalar(conn: OracleConnection, statement: str):
    cursor = conn.cursor()
    cursor.execute(statement)
    row = cursor.fetchone()
    return row[0] if row else None


def _check_privilege(conn: OracleConnection, view: str) -> bool:
    if view not in ALLOWLISTED_VIEWS:
        raise ValueError(f"refusing to probe a non-allowlisted view: {view}")
    try:
        cursor = conn.cursor()
        # `view` is checked against the fixed allowlist above, never taken from a request.
        cursor.execute(f"SELECT COUNT(*) FROM {view} WHERE ROWNUM <= 1")  # nosec B608
        cursor.fetchone()
        return True
    except Exception:
        return False


def _check_statspack_available(conn: OracleConnection) -> bool:
    """True only if PERFSTAT.STATS$SNAPSHOT is actually queryable - a PERFSTAT database user
    existing is not itself evidence the schema/tables were ever installed (the check this
    replaced). Any failure (ORA-00942 table/view does not exist, ORA-01031 insufficient
    privileges, PERFSTAT not existing at all) means "not available," never an exception."""
    if "STATS$SNAPSHOT" not in STATSPACK_ALLOWLISTED_TABLES:
        raise ValueError("refusing to probe a non-allowlisted Statspack table: STATS$SNAPSHOT")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM PERFSTAT.STATS$SNAPSHOT WHERE ROWNUM <= 1")  # nosec B608
        cursor.fetchone()
        return True
    except Exception:
        return False


def probe_capabilities(conn: OracleConnection) -> CapabilityProbeResult:
    banner = _fetch_scalar(conn, "SELECT banner FROM v$version WHERE banner LIKE 'Oracle Database%'") or ""

    cluster_flag = _fetch_scalar(conn, "SELECT value FROM v$parameter WHERE name = 'cluster_database'")
    is_rac = (str(cluster_flag).upper() == "TRUE") if cluster_flag is not None else None

    statspack_available = _check_statspack_available(conn)

    privilege_report = {view: _check_privilege(conn, view) for view in ALLOWLISTED_VIEWS}

    cpu_count_raw = _fetch_scalar(conn, "SELECT value FROM v$parameter WHERE name = 'cpu_count'")
    try:
        cpu_count = int(cpu_count_raw) if cpu_count_raw is not None else None
    except (TypeError, ValueError):
        cpu_count = None

    return CapabilityProbeResult(
        detected_version=parse_version(banner) if banner else None,
        detected_edition=detect_edition(banner),
        is_rac=is_rac,
        statspack_available=statspack_available,
        privilege_report=privilege_report,
        cpu_count=cpu_count,
    )
