"""Capacity/tablespace collection (master plan Phase 5, Enterprise Readiness Plan D.2) - how
full each tablespace is and how much space it has to grow into, from DBA_TABLESPACES,
DBA_DATA_FILES, DBA_FREE_SPACE, DBA_TEMP_FILES, and V$TEMP_SPACE_HEADER. All five are base
data-dictionary/dynamic-performance views, not Diagnostics/Tuning Pack views.

PERMANENT and UNDO tablespaces are queried together (DBA_DATA_FILES + DBA_FREE_SPACE): a
datafile's allocated bytes minus its tablespace's free bytes gives used bytes. TEMPORARY
tablespaces need a different pair (DBA_TEMP_FILES + V$TEMP_SPACE_HEADER) because tempfiles
don't populate DBA_FREE_SPACE - V$TEMP_SPACE_HEADER gives bytes_used directly instead.

Deliberately allowlisted but never queried here, each for a stated reason:
- V$TABLESPACE: gives nothing DBA_TABLESPACES doesn't already give (name, a few flags) - kept
  allowlisted from an earlier phase, unused by this module.
- V$UNDOSTAT: undo *activity/tuning* metrics (generation rate, tuned retention, transaction
  count), not undo *space* usage - this phase is space-only (Enterprise Readiness Plan D.2's
  own scope cut-line); undo tablespace fullness is already covered via DBA_DATA_FILES/
  DBA_FREE_SPACE like any other tablespace.
"""

from dataclasses import dataclass

from app.oracle.connector import OracleConnection

# Oracle's real per-file maximum for MAXSIZE UNLIMITED is a genuine, finite,
# version/block-size-dependent number - not a documented universal sentinel like 2**64-1 -
# so hardcoding Oracle's internal per-blocksize max-file-size table would be fragile across
# versions. This conservative threshold safely covers every real per-file maximum while
# honestly reporting "no meaningful capacity-planning ceiling" instead of a nonsensical
# huge number.
UNLIMITED_MAXBYTES_THRESHOLD = 100 * 1024**4  # 100 TB

_PERMANENT_AND_UNDO_SQL = (
    "SELECT t.tablespace_name, t.contents, t.status,"
    "       df.allocated_bytes, df.max_bytes, df.unconstrained_file_count, df.file_count,"
    "       NVL(fs.free_bytes, 0) free_bytes"
    " FROM dba_tablespaces t"
    " JOIN ("
    "   SELECT tablespace_name, SUM(bytes) allocated_bytes,"
    "          SUM(CASE WHEN autoextensible = 'YES' THEN GREATEST(maxbytes, bytes) ELSE bytes END) max_bytes,"
    "          SUM(CASE WHEN autoextensible = 'YES' AND maxbytes >= {threshold} THEN 1 ELSE 0 END)"
    "            unconstrained_file_count,"
    "          COUNT(*) file_count"
    "   FROM dba_data_files"
    "   GROUP BY tablespace_name"
    " ) df ON df.tablespace_name = t.tablespace_name"
    " LEFT JOIN ("
    "   SELECT tablespace_name, SUM(bytes) free_bytes"
    "   FROM dba_free_space"
    "   GROUP BY tablespace_name"
    " ) fs ON fs.tablespace_name = t.tablespace_name"
    " WHERE t.contents IN ('PERMANENT', 'UNDO')"
)

_TEMPORARY_SQL = (
    # allocated_bytes and used_bytes both come from V$TEMP_SPACE_HEADER (bytes_used +
    # bytes_free = the file's current total) so the two are always self-consistent - a
    # separate DBA_TEMP_FILES.BYTES read here is NOT guaranteed to agree with
    # V$TEMP_SPACE_HEADER at the same instant (confirmed live against orclpdb during
    # development: BYTES_USED can exceed a separately-read DBA_TEMP_FILES.BYTES; Oracle
    # doesn't always shrink a tempfile's high-water-mark back down after a large sort, so the
    # two views can disagree by the time two independent queries read them). DBA_TEMP_FILES is
    # used only for the max-bytes autoextend ceiling, which V$TEMP_SPACE_HEADER doesn't carry.
    "SELECT t.tablespace_name, t.contents, t.status,"
    "       th.allocated_bytes, tf.max_bytes, tf.unconstrained_file_count, th.file_count,"
    "       th.used_bytes"
    " FROM dba_tablespaces t"
    " JOIN ("
    "   SELECT tablespace_name, SUM(bytes_used + bytes_free) allocated_bytes,"
    "          SUM(bytes_used) used_bytes, COUNT(*) file_count"
    "   FROM v$temp_space_header"
    "   GROUP BY tablespace_name"
    " ) th ON th.tablespace_name = t.tablespace_name"
    " LEFT JOIN ("
    "   SELECT tablespace_name,"
    "          SUM(CASE WHEN autoextensible = 'YES' THEN GREATEST(maxbytes, bytes) ELSE bytes END) max_bytes,"
    "          SUM(CASE WHEN autoextensible = 'YES' AND maxbytes >= {threshold} THEN 1 ELSE 0 END)"
    "            unconstrained_file_count"
    "   FROM dba_temp_files"
    "   GROUP BY tablespace_name"
    " ) tf ON tf.tablespace_name = t.tablespace_name"
    " WHERE t.contents = 'TEMPORARY'"
)


@dataclass(frozen=True)
class TablespaceRow:
    tablespace_name: str
    contents: str
    status: str | None
    allocated_bytes: int
    used_bytes: int
    max_bytes: int | None
    file_count: int


def sample_permanent_tablespaces(conn: OracleConnection) -> list[TablespaceRow]:
    """PERMANENT and UNDO tablespaces via DBA_DATA_FILES/DBA_FREE_SPACE. This is the "must
    succeed" probe (see app/services/collector_service.py's run_capacity_once_for_database) -
    tablespace/UNDO exhaustion (ORA-1653/ORA-1652) is the more universally critical DBA
    concern than TEMP exhaustion, so its failure aborts the whole collection round rather
    than persisting a partial result."""
    cursor = conn.cursor()
    # UNLIMITED_MAXBYTES_THRESHOLD is a fixed module constant, never request-derived - same
    # `# nosec B608` guard pattern as app/oracle/sql_probe.py's top_n.
    cursor.execute(_PERMANENT_AND_UNDO_SQL.format(threshold=UNLIMITED_MAXBYTES_THRESHOLD))  # nosec B608
    return [_row_from_allocated_and_free(row) for row in cursor.fetchall()]


def sample_temp_tablespaces(conn: OracleConnection) -> list[TablespaceRow]:
    """TEMPORARY tablespaces via DBA_TEMP_FILES/V$TEMP_SPACE_HEADER. Best-effort: a missing
    grant on either view still lets sample_permanent_tablespaces's data persist."""
    cursor = conn.cursor()
    cursor.execute(_TEMPORARY_SQL.format(threshold=UNLIMITED_MAXBYTES_THRESHOLD))  # nosec B608
    return [_row_from_allocated_and_used(row) for row in cursor.fetchall()]


def _resolved_max_bytes(max_bytes: int | None, unconstrained_file_count: int | None) -> int | None:
    if unconstrained_file_count:
        return None
    return max_bytes


def _row_from_allocated_and_free(row: tuple) -> TablespaceRow:
    tablespace_name, contents, status, allocated_bytes, max_bytes, unconstrained_file_count, file_count, free_bytes = row
    allocated_bytes = allocated_bytes or 0
    return TablespaceRow(
        tablespace_name=tablespace_name,
        contents=contents,
        status=status,
        allocated_bytes=allocated_bytes,
        used_bytes=max(allocated_bytes - (free_bytes or 0), 0),
        max_bytes=_resolved_max_bytes(max_bytes, unconstrained_file_count),
        file_count=file_count or 0,
    )


def _row_from_allocated_and_used(row: tuple) -> TablespaceRow:
    tablespace_name, contents, status, allocated_bytes, max_bytes, unconstrained_file_count, file_count, used_bytes = row
    return TablespaceRow(
        tablespace_name=tablespace_name,
        contents=contents,
        status=status,
        allocated_bytes=allocated_bytes or 0,
        used_bytes=used_bytes or 0,
        max_bytes=_resolved_max_bytes(max_bytes, unconstrained_file_count),
        file_count=file_count or 0,
    )
