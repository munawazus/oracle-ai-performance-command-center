"""The Section 7 view classification from the Phase 0/1 discovery baseline, as code.

ALLOWLISTED_VIEWS is the ONLY set of views the platform ever queries. BLOCKLISTED_VIEWS
exists purely so a test can assert the two sets never intersect and so a reviewer can see,
in one place, what this platform deliberately never touches - the Diagnostics Pack / Tuning
Pack views that Oracle does not sell for Standard Edition 2 (and which require separate
licensing even on Enterprise Edition). This is engineering guidance, not a licensing
determination - confirm actual entitlement with your Oracle license contact before use.
"""

ALLOWLISTED_VIEWS: tuple[str, ...] = (
    "V$SESSION",
    "V$SQL",
    "V$SQLAREA",
    "V$SQL_PLAN",
    "V$SYSTEM_EVENT",
    "V$SESSION_EVENT",
    "V$SESSTAT",
    "V$SYSSTAT",
    "V$LOCK",
    "V$PROCESS",
    "V$INSTANCE",
    "V$DATABASE",
    "V$TABLESPACE",
    "V$UNDOSTAT",
    "V$TEMP_SPACE_HEADER",
    "V$SGA",
    "V$PGASTAT",
    "DBA_DATA_FILES",
    "DBA_TABLESPACES",
    # Capacity/tablespace collector (master plan Phase 5, Enterprise Readiness Plan D.2) -
    # base data-dictionary views, no Diagnostics/Tuning Pack license required.
    "DBA_FREE_SPACE",
    "DBA_TEMP_FILES",
    # Change Intelligence (master plan Phase 14, Enterprise Readiness Plan D.4) - the
    # instance-wide configuration view (never a per-session override, unlike V$PARAMETER),
    # a base dynamic-performance view with no Diagnostics/Tuning Pack license required.
    "V$SYSTEM_PARAMETER",
)

BLOCKLISTED_VIEWS: tuple[str, ...] = (
    "V$ACTIVE_SESSION_HISTORY",
    "DBA_HIST_ACTIVE_SESS_HISTORY",
    "DBA_HIST_SNAPSHOT",
    "DBA_HIST_SQLSTAT",
    "DBA_HIST_SYSMETRIC_HISTORY",
    "DBA_HIST_SYSTEM_EVENT",
    "V$SQL_MONITOR",
)

# Statspack (master plan Phase 11, Enterprise Readiness Plan D.3) - a separate tuple, not
# folded into ALLOWLISTED_VIEWS, because these are ordinary tables owned by the PERFSTAT
# schema, not V$ views or DBA_* data-dictionary views: every query site prefixes the fixed
# literal "PERFSTAT." (never a request/DBA-supplied schema) rather than querying them bare.
# Statspack itself is a free, bundled Oracle feature requiring no Diagnostics/Tuning Pack
# license - unlike the DBA_HIST_*/V$ACTIVE_SESSION_HISTORY (AWR/ASH) family above, which is
# separately licensed and blocklisted for exactly that reason. Only the tables needed for a
# top-wait-events/top-SQL summary are here; STATS$SYSSTAT/STATS$WAITSTAT/STATS$LATCH*/
# STATS$ENQUEUE_STAT are deliberately not queried (see app/oracle/statspack_probe.py).
STATSPACK_ALLOWLISTED_TABLES: tuple[str, ...] = (
    "STATS$SNAPSHOT",
    "STATS$SYSTEM_EVENT",
    "STATS$SQL_SUMMARY",
)

if not set(ALLOWLISTED_VIEWS).isdisjoint(BLOCKLISTED_VIEWS):
    raise ValueError("ALLOWLISTED_VIEWS and BLOCKLISTED_VIEWS must never overlap")
if not set(STATSPACK_ALLOWLISTED_TABLES).isdisjoint(BLOCKLISTED_VIEWS):
    raise ValueError("STATSPACK_ALLOWLISTED_TABLES and BLOCKLISTED_VIEWS must never overlap")
