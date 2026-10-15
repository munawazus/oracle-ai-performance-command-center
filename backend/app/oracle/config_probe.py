"""Change Intelligence (master plan Phase 14, Enterprise Readiness Plan D.4) - configuration
parameter drift detection from V$SYSTEM_PARAMETER, a base dynamic-performance view requiring
no Diagnostics/Tuning Pack license.

V$SYSTEM_PARAMETER, not V$PARAMETER: V$SYSTEM_PARAMETER reflects the value in effect for the
instance itself, never a per-session override - the correct view for "what is this database's
own configuration," avoiding the noise a per-session view would introduce.

Filtered to ISDEFAULT = 'FALSE' only: the large majority of an instance's ~300 parameters are
always at Oracle's shipped default and never interesting. Storing only the explicitly-tuned
subset each round is both far cheaper and matches the DBA mental model ("show me what's been
touched") - every transition is still detectable by set membership between two rounds (see
app/analytics/config_metrics.py): newly appears, changes value, or disappears (reverted to
default).

Caveat, not modeled here: V$SYSTEM_PARAMETER shows the value currently in effect, not whether
it was persisted via SCOPE=SPFILE/BOTH or would revert on the next instance restart - that
distinction is out of scope for this phase (see docs/ENTERPRISE_READINESS_PLAN.md D.4)."""

from dataclasses import dataclass

from app.oracle.connector import OracleConnection

_NON_DEFAULT_PARAMETERS_SQL = "SELECT name, value FROM v$system_parameter WHERE isdefault = 'FALSE'"


@dataclass(frozen=True)
class ParameterRow:
    parameter_name: str
    parameter_value: str | None


def sample_non_default_parameters(conn: OracleConnection) -> list[ParameterRow]:
    """The only query this collector issues - no request/DBA-supplied content is ever
    interpolated into this SQL text, so unlike every other probe module, no `# nosec B608`
    guard is needed here."""
    cursor = conn.cursor()
    cursor.execute(_NON_DEFAULT_PARAMETERS_SQL)
    return [ParameterRow(parameter_name=name, parameter_value=value) for name, value in cursor.fetchall()]
