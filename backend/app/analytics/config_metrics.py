"""Pure configuration-drift diffing (master plan Phase 14, Enterprise Readiness Plan D.4) - no
DB/ORM coupling, directly unit-testable, same pattern as app/analytics/capacity_metrics.py.

Unlike SQL/wait-class deltas (a numeric rate over a shared cumulative counter, reusable as-is
from app/analytics/sql_metrics.py and app/analytics/metrics.py), a configuration round is a
set of (name, value) pairs with no counter to subtract - this is a set-diff over parameter
names, the one place this platform's D.1-era delta machinery doesn't apply."""

from dataclasses import dataclass
from typing import Protocol, Sequence

NEWLY_SET = "newly_set"
CHANGED = "changed"
REVERTED_TO_DEFAULT = "reverted_to_default"


class ConfigParameterRowLike(Protocol):
    parameter_name: str
    parameter_value: str | None


@dataclass(frozen=True)
class ParameterChange:
    parameter_name: str
    previous_value: str | None
    current_value: str | None
    change_type: str  # newly_set | changed | reverted_to_default


def compute_parameter_changes(
    previous: Sequence[ConfigParameterRowLike], current: Sequence[ConfigParameterRowLike]
) -> list[ParameterChange]:
    """Set-diffs two rounds' non-default parameter sets by name. A parameter present in both
    rounds with an identical value is a no-op and excluded from the result entirely - only
    genuine transitions are returned."""
    previous_values = {row.parameter_name: row.parameter_value for row in previous}
    current_values = {row.parameter_name: row.parameter_value for row in current}

    changes = []
    for name in sorted(set(previous_values) | set(current_values)):
        prev_value = previous_values.get(name)
        curr_value = current_values.get(name)
        if name not in previous_values:
            changes.append(ParameterChange(name, None, curr_value, NEWLY_SET))
        elif name not in current_values:
            changes.append(ParameterChange(name, prev_value, None, REVERTED_TO_DEFAULT))
        elif prev_value != curr_value:
            changes.append(ParameterChange(name, prev_value, curr_value, CHANGED))
    return changes
