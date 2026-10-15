from app.analytics.sql_metrics import WindowTotals
from app.analytics.sql_regression import (
    MIN_ABSOLUTE_ELAPSED_PER_EXEC_US,
    MIN_BASELINE_ROUNDS,
    MIN_EXECUTIONS_PER_WINDOW,
    REGRESSION_KIND_ELAPSED_PER_EXEC,
    REGRESSION_KIND_PLAN_CHANGE,
    classify_regression_severity,
    detect_regressions,
)


def _totals(executions, elapsed, rounds=MIN_BASELINE_ROUNDS):
    return WindowTotals(executions=executions, elapsed_time_us=elapsed, round_count=rounds)


# -- classify_regression_severity --------------------------------------------------------


def test_severity_boundaries():
    assert classify_regression_severity(1.9) is None
    assert classify_regression_severity(2.0) == "LOW"
    assert classify_regression_severity(3.9) == "LOW"
    assert classify_regression_severity(4.0) == "MEDIUM"
    assert classify_regression_severity(9.9) == "MEDIUM"
    assert classify_regression_severity(10.0) == "HIGH"
    assert classify_regression_severity(24.9) == "HIGH"
    assert classify_regression_severity(25.0) == "CRITICAL"
    assert classify_regression_severity(100.0) == "CRITICAL"


# -- ELAPSED_PER_EXEC signal --------------------------------------------------------------


def test_elapsed_regression_fires_when_cost_per_exec_crosses_the_ratio_floor():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}  # 10ms/exec
    current = {"SQL1": {111: _totals(executions=100, elapsed=4_000_000, rounds=1)}}  # 40ms/exec, 4x

    findings = detect_regressions(baseline, current)

    assert len(findings) == 1
    assert findings[0].sql_id == "SQL1"
    assert findings[0].regression_kind == REGRESSION_KIND_ELAPSED_PER_EXEC
    assert findings[0].severity == "MEDIUM"
    assert findings[0].ratio == 4.0


def test_elapsed_regression_does_not_fire_below_the_ratio_floor():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}  # 10ms/exec
    current = {"SQL1": {111: _totals(executions=100, elapsed=1_500_000, rounds=1)}}  # 15ms/exec, 1.5x

    assert detect_regressions(baseline, current) == []


def test_elapsed_regression_requires_minimum_baseline_rounds():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000, rounds=MIN_BASELINE_ROUNDS - 1)}}
    current = {"SQL1": {111: _totals(executions=100, elapsed=4_000_000, rounds=1)}}

    assert detect_regressions(baseline, current) == []


def test_elapsed_regression_requires_minimum_executions_in_both_windows():
    baseline = {"SQL1": {111: _totals(executions=MIN_EXECUTIONS_PER_WINDOW - 1, elapsed=100_000)}}
    current = {"SQL1": {111: _totals(executions=100, elapsed=4_000_000, rounds=1)}}
    assert detect_regressions(baseline, current) == []

    baseline2 = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}
    current2 = {"SQL1": {111: _totals(executions=MIN_EXECUTIONS_PER_WINDOW - 1, elapsed=400_000, rounds=1)}}
    assert detect_regressions(baseline2, current2) == []


def test_elapsed_regression_requires_a_minimum_absolute_cost_never_pages_on_a_tiny_wobble():
    # 0.1ms -> 0.4ms is a real 4x ratio but nowhere near a DBA-actionable absolute cost.
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=100_000)}}  # 0.1ms/exec
    current = {"SQL1": {111: _totals(executions=100, elapsed=40_000, rounds=1)}}  # 0.4ms/exec
    assert MIN_ABSOLUTE_ELAPSED_PER_EXEC_US > 400  # sanity: the test fixture is below the floor

    assert detect_regressions(baseline, current) == []


def test_elapsed_regression_requires_a_nonzero_baseline():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=0)}}
    current = {"SQL1": {111: _totals(executions=100, elapsed=4_000_000, rounds=1)}}

    assert detect_regressions(baseline, current) == []


def test_no_finding_for_a_sql_id_missing_from_the_baseline_entirely():
    baseline: dict = {}
    current = {"SQL1": {111: _totals(executions=100, elapsed=4_000_000, rounds=1)}}

    assert detect_regressions(baseline, current) == []


# -- PLAN_CHANGE signal ---------------------------------------------------------------------


def test_plan_change_fires_when_the_dominant_plan_hash_changes():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}
    current = {"SQL1": {222: _totals(executions=100, elapsed=4_000_000, rounds=1)}}  # new dominant plan

    findings = detect_regressions(baseline, current)

    assert len(findings) == 1
    assert findings[0].regression_kind == REGRESSION_KIND_PLAN_CHANGE
    assert findings[0].baseline_plan_hash_value == 111
    assert findings[0].current_plan_hash_value == 222


def test_plan_change_severity_is_low_when_the_new_plan_is_not_worse():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}  # 10ms/exec
    current = {"SQL1": {222: _totals(executions=100, elapsed=500_000, rounds=1)}}  # 5ms/exec - better

    findings = detect_regressions(baseline, current)

    assert findings[0].regression_kind == REGRESSION_KIND_PLAN_CHANGE
    assert findings[0].severity == "LOW"


def test_plan_change_severity_escalates_when_the_new_plan_is_worse():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}  # 10ms/exec
    current = {"SQL1": {222: _totals(executions=100, elapsed=25_000_000, rounds=1)}}  # 250ms/exec, 25x

    findings = detect_regressions(baseline, current)

    assert findings[0].regression_kind == REGRESSION_KIND_PLAN_CHANGE
    assert findings[0].severity == "CRITICAL"


def test_no_plan_change_finding_when_the_dominant_plan_is_unchanged():
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}
    current = {"SQL1": {111: _totals(executions=100, elapsed=1_000_000, rounds=1)}}  # same plan, no regression

    assert detect_regressions(baseline, current) == []


def test_plan_change_suppresses_an_elapsed_per_exec_finding_for_the_same_sql_id():
    # Both signals would independently fire here (dominant plan changed AND cost per exec
    # rose sharply) - only the plan-change finding should surface, one root cause not two.
    baseline = {"SQL1": {111: _totals(executions=1000, elapsed=10_000_000)}}  # 10ms/exec
    current = {"SQL1": {222: _totals(executions=100, elapsed=25_000_000, rounds=1)}}  # 250ms/exec

    findings = detect_regressions(baseline, current)

    assert len(findings) == 1
    assert findings[0].regression_kind == REGRESSION_KIND_PLAN_CHANGE


def test_multiple_sql_ids_are_judged_independently():
    baseline = {
        "SQL1": {111: _totals(executions=1000, elapsed=10_000_000)},
        "SQL2": {333: _totals(executions=1000, elapsed=10_000_000)},
    }
    current = {
        "SQL1": {111: _totals(executions=100, elapsed=1_000_000, rounds=1)},  # unchanged, no finding
        "SQL2": {333: _totals(executions=100, elapsed=4_000_000, rounds=1)},  # 4x regression
    }

    findings = detect_regressions(baseline, current)

    assert len(findings) == 1
    assert findings[0].sql_id == "SQL2"
