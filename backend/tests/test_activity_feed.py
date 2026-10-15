"""Pure unit tests for the Activity tab's fleet-wide event merger (no DB, no I/O - literal
in-memory ORM model instances, same convention as tests/test_activity_service.py's
_capacity_trend tests and app/reports/health_check.py's own test suite)."""

from datetime import datetime, timedelta, timezone

from app.models.analytics import Anomaly
from app.models.capacity_telemetry import CapacityAlertEvent
from app.models.incident import Incident
from app.models.sql_telemetry import SqlRegressionEvent
from app.reports.activity_feed import (
    ANOMALY,
    CAPACITY_ALERT,
    INCIDENT_OPENED,
    INCIDENT_RESOLVED,
    SQL_REGRESSION,
    STORM,
    build_activity_feed,
)

T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

DATABASE_NAMES = {"db-1": "PROD01", "db-2": "PROD02", "db-3": "PROD03", "db-4": "PROD04"}


def _anomaly(
    *, id: str, database_id: str, at: datetime, severity: str = "HIGH", metric_key: str = "active_session_count"
) -> Anomaly:
    return Anomaly(
        id=id,
        monitored_database_id=database_id,
        detected_at=at,
        metric_key=metric_key,
        observed_value=500.0,
        baseline_mean=10.0,
        severity=severity,
        incident_id=None,
    )


def _regression(*, id: str, database_id: str, at: datetime, severity: str = "HIGH") -> SqlRegressionEvent:
    return SqlRegressionEvent(
        id=id,
        monitored_database_id=database_id,
        last_detected_at=at,
        severity=severity,
        sql_id="actvsql01",
        regression_kind="PLAN_CHANGE",
        ratio=9.0,
        status="OPEN",
        detection_note="test regression",
    )


def _alert(*, id: str, database_id: str, at: datetime, severity: str = "HIGH") -> CapacityAlertEvent:
    return CapacityAlertEvent(
        id=id,
        monitored_database_id=database_id,
        last_detected_at=at,
        severity=severity,
        tablespace_name="DATA01",
        alert_kind="PCT_USED",
        pct_used=95.0,
        status="OPEN",
        detection_note="test alert",
    )


def _incident(*, id: str, database_id: str, opened_at: datetime, resolved_at: datetime | None = None) -> Incident:
    return Incident(
        id=id,
        monitored_database_id=database_id,
        opened_at=opened_at,
        resolved_at=resolved_at,
        severity="HIGH",
        status="RESOLVED" if resolved_at else "OPEN",
        title="Test incident",
    )


# -- merge/interleave ordering ---------------------------------------------------------------


def test_merges_all_four_sources_into_one_newest_first_list():
    events = build_activity_feed(
        anomalies=[_anomaly(id="a1", database_id="db-1", at=T0 - timedelta(minutes=30))],
        sql_regressions=[_regression(id="r1", database_id="db-1", at=T0 - timedelta(minutes=10))],
        capacity_alerts=[_alert(id="c1", database_id="db-2", at=T0 - timedelta(minutes=20))],
        incidents=[_incident(id="i1", database_id="db-2", opened_at=T0 - timedelta(minutes=40))],
        database_names=DATABASE_NAMES,
    )
    categories_in_order = [e.category for e in events]
    assert categories_in_order == [SQL_REGRESSION, CAPACITY_ALERT, ANOMALY, INCIDENT_OPENED]
    assert [e.occurred_at for e in events] == sorted((e.occurred_at for e in events), reverse=True)


def test_a_resolved_incident_contributes_two_distinct_timeline_entries():
    events = build_activity_feed(
        anomalies=[],
        sql_regressions=[],
        capacity_alerts=[],
        incidents=[_incident(id="i1", database_id="db-1", opened_at=T0 - timedelta(hours=1), resolved_at=T0)],
        database_names=DATABASE_NAMES,
    )
    categories = {e.category for e in events}
    assert categories == {INCIDENT_OPENED, INCIDENT_RESOLVED}
    assert len(events) == 2


def test_an_open_incident_contributes_only_one_entry():
    events = build_activity_feed(
        anomalies=[], sql_regressions=[], capacity_alerts=[],
        incidents=[_incident(id="i1", database_id="db-1", opened_at=T0)],
        database_names=DATABASE_NAMES,
    )
    assert len(events) == 1
    assert events[0].category == INCIDENT_OPENED


def test_events_carry_the_real_database_name_not_just_the_id():
    events = build_activity_feed(
        anomalies=[_anomaly(id="a1", database_id="db-2", at=T0)],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert events[0].database_id == "db-2"
    assert events[0].database_name == "PROD02"


def test_falls_back_to_a_placeholder_name_for_an_unknown_database_id():
    events = build_activity_feed(
        anomalies=[_anomaly(id="a1", database_id="db-missing", at=T0)],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert events[0].database_name == "Unknown"


# -- empty/partial source handling -----------------------------------------------------------


def test_all_sources_empty_returns_an_empty_list():
    assert build_activity_feed(
        anomalies=[], sql_regressions=[], capacity_alerts=[], incidents=[], database_names={}
    ) == []


def test_one_source_empty_still_merges_the_others_correctly():
    events = build_activity_feed(
        anomalies=[_anomaly(id="a1", database_id="db-1", at=T0)],
        sql_regressions=[],
        capacity_alerts=[_alert(id="c1", database_id="db-1", at=T0 - timedelta(minutes=5))],
        incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert [e.category for e in events] == [ANOMALY, CAPACITY_ALERT]


# -- since/until/severity filtering -----------------------------------------------------------


def test_since_excludes_events_before_the_cutoff():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="old", database_id="db-1", at=T0 - timedelta(hours=2)),
            _anomaly(id="new", database_id="db-1", at=T0),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
        since=T0 - timedelta(hours=1),
    )
    assert [e.event_id for e in events] == ["anomaly:new"]


def test_until_excludes_events_after_the_cutoff():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="old", database_id="db-1", at=T0 - timedelta(hours=2)),
            _anomaly(id="new", database_id="db-1", at=T0),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
        until=T0 - timedelta(hours=1),
    )
    assert [e.event_id for e in events] == ["anomaly:old"]


def test_severity_filter_keeps_only_exact_matches_across_every_source():
    events = build_activity_feed(
        anomalies=[_anomaly(id="a1", database_id="db-1", at=T0, severity="CRITICAL")],
        sql_regressions=[_regression(id="r1", database_id="db-1", at=T0, severity="HIGH")],
        capacity_alerts=[_alert(id="c1", database_id="db-1", at=T0, severity="CRITICAL")],
        incidents=[],
        database_names=DATABASE_NAMES,
        severity="CRITICAL",
    )
    assert {e.event_id for e in events} == {"anomaly:a1", "capacity_alert:c1"}


def test_limit_caps_the_result_after_sorting_not_before():
    events = build_activity_feed(
        anomalies=[_anomaly(id=f"a{i}", database_id="db-1", at=T0 - timedelta(minutes=i)) for i in range(5)],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
        limit=2,
    )
    assert [e.event_id for e in events] == ["anomaly:a0", "anomaly:a1"]


# -- storm/burst collapsing (Phase 3) ---------------------------------------------------------


def test_two_databases_on_the_same_metric_does_not_collapse_into_a_storm():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=2)),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    categories = {e.category for e in events}
    assert categories == {ANOMALY}
    assert len(events) == 2


def test_three_databases_on_the_same_metric_within_the_window_collapses_into_one_storm():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=3)),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=6)),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert len(events) == 1
    storm = events[0]
    assert storm.category == STORM
    assert storm.entities["database_ids"] == ["db-1", "db-2", "db-3"]
    assert storm.entities["metric_key"] == "active_session_count"


def test_a_fourth_database_joining_mid_window_is_absorbed_into_the_same_storm():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=3)),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=6)),
            _anomaly(id="a4", database_id="db-4", at=T0 + timedelta(minutes=9)),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert len(events) == 1
    assert events[0].entities["database_ids"] == ["db-1", "db-2", "db-3", "db-4"]


def test_a_database_repeating_the_same_metric_does_not_inflate_the_distinct_count():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0),
            _anomaly(id="a2", database_id="db-1", at=T0 + timedelta(minutes=1)),
            _anomaly(id="a3", database_id="db-2", at=T0 + timedelta(minutes=2)),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    # Only 2 distinct databases (db-1 fired twice) - below STORM_MIN_DATABASES, no collapse.
    categories = {e.category for e in events}
    assert categories == {ANOMALY}
    assert len(events) == 3


def test_two_different_metric_keys_are_never_conflated_into_one_storm():
    """Two independent metric_keys, each with its own 3-database burst - each must form its
    OWN storm, never merged together just because they overlap in time."""
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0, metric_key="active_session_count"),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=1), metric_key="active_session_count"),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=2), metric_key="active_session_count"),
            _anomaly(id="b1", database_id="db-1", at=T0, metric_key="wait_rate_centisec_per_sec:User I/O"),
            _anomaly(id="b2", database_id="db-2", at=T0 + timedelta(minutes=1), metric_key="wait_rate_centisec_per_sec:User I/O"),
            _anomaly(id="b3", database_id="db-3", at=T0 + timedelta(minutes=2), metric_key="wait_rate_centisec_per_sec:User I/O"),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert len(events) == 2
    assert all(e.category == STORM for e in events)
    metric_keys = {e.entities["metric_key"] for e in events}
    assert metric_keys == {"active_session_count", "wait_rate_centisec_per_sec:User I/O"}


def test_storm_severity_is_the_worst_of_its_constituents():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0, severity="LOW"),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=1), severity="CRITICAL"),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=2), severity="MEDIUM"),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    assert len(events) == 1
    assert events[0].severity == "CRITICAL"


def test_storms_outside_the_window_do_not_collapse():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=5)),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=11)),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
    )
    # a3 is 11 minutes after the window anchor (a1) - outside STORM_WINDOW, so only a1/a2
    # form a (2-database, sub-threshold) non-storm cluster and a3 stands alone.
    categories = [e.category for e in events]
    assert categories == [ANOMALY, ANOMALY, ANOMALY]


def test_a_severity_filter_still_applies_to_a_storm_using_its_own_computed_severity():
    events = build_activity_feed(
        anomalies=[
            _anomaly(id="a1", database_id="db-1", at=T0, severity="HIGH"),
            _anomaly(id="a2", database_id="db-2", at=T0 + timedelta(minutes=1), severity="HIGH"),
            _anomaly(id="a3", database_id="db-3", at=T0 + timedelta(minutes=2), severity="HIGH"),
        ],
        sql_regressions=[], capacity_alerts=[], incidents=[],
        database_names=DATABASE_NAMES,
        severity="CRITICAL",
    )
    assert events == []
