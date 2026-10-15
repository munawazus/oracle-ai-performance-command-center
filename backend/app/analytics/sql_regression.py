"""Deterministic SQL regression detection (master plan Phase 9) - a dedicated comparator,
not the general per-metric-key hour-of-day baseline engine (app/analytics/baseline_engine.py).

That engine needs MIN_SAMPLES_FOR_ANOMALY (20) observations in the *same hour-of-day
bucket* before it can ever judge a metric, and does one extra baseline-fetch/upsert
round-trip pair per tracked key per round - workable for a handful of wait classes, far too
expensive and mostly-never-usable for dozens of tracked SQL_IDs whose cursors can age out of
the shared pool before 20 same-hour samples ever accumulate. "SQL regression" is also not a
statistical outlier - it's one of two deterministic facts: the execution plan changed, or
this statement's cost per execution got materially worse than its own recent history. Both
are decidable from two windows and a threshold; a z-score would dilute a hard fact into a
soft one.

All thresholds below are named v1 defaults, in the same spirit as
baseline_engine.MIN_SAMPLES_FOR_ANOMALY - revisit once real telemetry volume shows what
actually separates noise from a real regression.
"""

from dataclasses import dataclass
from datetime import timedelta

from app.analytics.sql_metrics import WindowTotals

SQL_CURRENT_WINDOW = timedelta(minutes=60)
SQL_BASELINE_WINDOW = timedelta(hours=24)
MIN_BASELINE_ROUNDS = 6
MIN_EXECUTIONS_PER_WINDOW = 10
MIN_ABSOLUTE_ELAPSED_PER_EXEC_US = 10_000  # 10ms - never page on a 0.1ms -> 0.4ms wobble
RATIO_LOW = 2.0
RATIO_MEDIUM = 4.0
RATIO_HIGH = 10.0
RATIO_CRITICAL = 25.0
SQL_REGRESSION_RESOLVE_AFTER = timedelta(minutes=15)

REGRESSION_KIND_PLAN_CHANGE = "PLAN_CHANGE"
REGRESSION_KIND_ELAPSED_PER_EXEC = "ELAPSED_PER_EXEC"


@dataclass(frozen=True)
class RegressionFinding:
    sql_id: str
    regression_kind: str
    severity: str
    baseline_elapsed_per_exec_us: float | None
    current_elapsed_per_exec_us: float | None
    ratio: float | None
    baseline_executions: int | None
    current_executions: int | None
    baseline_plan_hash_value: int | None
    current_plan_hash_value: int | None
    detection_note: str


def classify_regression_severity(ratio: float) -> str | None:
    """None below RATIO_LOW - not flagged as a regression at all."""
    if ratio >= RATIO_CRITICAL:
        return "CRITICAL"
    if ratio >= RATIO_HIGH:
        return "HIGH"
    if ratio >= RATIO_MEDIUM:
        return "MEDIUM"
    if ratio >= RATIO_LOW:
        return "LOW"
    return None


def _overall(by_plan: dict[int, WindowTotals]) -> WindowTotals:
    executions = sum(t.executions for t in by_plan.values())
    elapsed = sum(t.elapsed_time_us for t in by_plan.values())
    rounds = max((t.round_count for t in by_plan.values()), default=0)
    return WindowTotals(executions=executions, elapsed_time_us=elapsed, round_count=rounds)


def _dominant_plan(by_plan: dict[int, WindowTotals]) -> int | None:
    if not by_plan:
        return None
    return max(by_plan.items(), key=lambda item: item[1].executions)[0]


def _detect_plan_change(
    sql_id: str, baseline_by_plan: dict[int, WindowTotals], current_by_plan: dict[int, WindowTotals]
) -> RegressionFinding | None:
    baseline_plan = _dominant_plan(baseline_by_plan)
    current_plan = _dominant_plan(current_by_plan)
    if baseline_plan is None or current_plan is None or baseline_plan == current_plan:
        return None

    baseline_totals = baseline_by_plan[baseline_plan]
    current_totals = current_by_plan[current_plan]
    baseline_per_exec = baseline_totals.elapsed_per_exec_us
    current_per_exec = current_totals.elapsed_per_exec_us

    severity: str
    if baseline_per_exec is None or current_per_exec is None:
        ratio = None
        severity = "LOW"
    elif current_per_exec <= baseline_per_exec:
        ratio = current_per_exec / baseline_per_exec if baseline_per_exec > 0 else None
        severity = "LOW"
    else:
        ratio = current_per_exec / baseline_per_exec if baseline_per_exec > 0 else None
        classified = classify_regression_severity(ratio) if ratio is not None else None
        severity = classified if classified is not None else "LOW"

    note = (
        f"Plan changed from hash {baseline_plan} to {current_plan}. "
        + (
            f"Cost per execution went from {baseline_per_exec:.0f}us to {current_per_exec:.0f}us."
            if baseline_per_exec is not None and current_per_exec is not None
            else "Cost-per-execution comparison unavailable (no executions in one window)."
        )
    )

    return RegressionFinding(
        sql_id=sql_id,
        regression_kind=REGRESSION_KIND_PLAN_CHANGE,
        severity=severity,
        baseline_elapsed_per_exec_us=baseline_per_exec,
        current_elapsed_per_exec_us=current_per_exec,
        ratio=ratio,
        baseline_executions=baseline_totals.executions,
        current_executions=current_totals.executions,
        baseline_plan_hash_value=baseline_plan,
        current_plan_hash_value=current_plan,
        detection_note=note,
    )


def _detect_elapsed_regression(
    sql_id: str, baseline_by_plan: dict[int, WindowTotals], current_by_plan: dict[int, WindowTotals]
) -> RegressionFinding | None:
    baseline = _overall(baseline_by_plan)
    current = _overall(current_by_plan)

    if baseline.round_count < MIN_BASELINE_ROUNDS:
        return None
    if baseline.executions < MIN_EXECUTIONS_PER_WINDOW or current.executions < MIN_EXECUTIONS_PER_WINDOW:
        return None
    baseline_per_exec = baseline.elapsed_per_exec_us
    current_per_exec = current.elapsed_per_exec_us
    if baseline_per_exec is None or baseline_per_exec <= 0 or current_per_exec is None:
        return None
    if current_per_exec < MIN_ABSOLUTE_ELAPSED_PER_EXEC_US:
        return None

    ratio = current_per_exec / baseline_per_exec
    severity = classify_regression_severity(ratio)
    if severity is None:
        return None

    return RegressionFinding(
        sql_id=sql_id,
        regression_kind=REGRESSION_KIND_ELAPSED_PER_EXEC,
        severity=severity,
        baseline_elapsed_per_exec_us=baseline_per_exec,
        current_elapsed_per_exec_us=current_per_exec,
        ratio=ratio,
        baseline_executions=baseline.executions,
        current_executions=current.executions,
        baseline_plan_hash_value=_dominant_plan(baseline_by_plan),
        current_plan_hash_value=_dominant_plan(current_by_plan),
        detection_note=(
            f"Elapsed time per execution went from {baseline_per_exec:.0f}us to "
            f"{current_per_exec:.0f}us ({ratio:.1f}x) over the last "
            f"{int(SQL_CURRENT_WINDOW.total_seconds() // 60)} minutes, compared to this "
            f"statement's own preceding {int(SQL_BASELINE_WINDOW.total_seconds() // 3600)}h."
        ),
    )


def detect_regressions(
    baseline: dict[str, dict[int, WindowTotals]], current: dict[str, dict[int, WindowTotals]]
) -> list[RegressionFinding]:
    """baseline/current: sql_id -> plan_hash_value -> WindowTotals, as produced by
    app/analytics/sql_metrics.aggregate_window over the baseline and current windows
    respectively. Plan-change findings suppress an elapsed-per-execution finding for the
    same sql_id in the same pass - one root cause, not two findings for it."""
    findings: list[RegressionFinding] = []
    for sql_id, current_by_plan in current.items():
        baseline_by_plan = baseline.get(sql_id, {})
        plan_change = _detect_plan_change(sql_id, baseline_by_plan, current_by_plan)
        if plan_change is not None:
            findings.append(plan_change)
            continue
        elapsed_regression = _detect_elapsed_regression(sql_id, baseline_by_plan, current_by_plan)
        if elapsed_regression is not None:
            findings.append(elapsed_regression)
    return findings
