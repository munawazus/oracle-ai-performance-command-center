"""Unit tests for the pure correlators/coverage logic (no DB, no LLM - plain evidence-shaped
dicts, exactly the contracts app/ai/tools.py's real functions return) plus integration tests
for gather_evidence()'s database/incident scope validation, which does need a real DB."""

from datetime import timedelta

import pytest

from app.ai.investigation import (
    FLEET_EVIDENCE_CACHE_KEY,
    EvidenceBundle,
    EvidenceScopeConflictError,
    Finding,
    Freshness,
    ToolCallStatus,
    _compute_coverage,
    _correlate_blocking,
    _correlate_capacity,
    _correlate_sql_regression,
    _correlate_waits,
    _deserialize_fleet_bundle,
    _freshness,
    _serialize_fleet_bundle,
    format_age,
    gather_evidence,
    gather_fleet_evidence,
    render_findings_as_facts,
)
from app.db import SessionLocal
from app.exceptions import NotFoundError
from app.models.base import utcnow
from app.oracle.telemetry_probe import SessionSampleRow
from app.repositories.telemetry_repo import TelemetryRepository

DB_PAYLOAD = {
    "name": "INVESTIGATION01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def _register_database(client, admin_headers, *, name: str = "INVESTIGATION01") -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json={**DB_PAYLOAD, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _open_incident(db_id: str) -> str:
    async with SessionLocal() as db:
        from app.repositories.analytics_repo import AnomalyRepository
        from app.repositories.incident_repo import IncidentRepository
        from app.services.incident_service import IncidentService

        anomaly = await AnomalyRepository(db).create(
            monitored_database_id=db_id,
            metric_key="active_session_count",
            observed_value=500.0,
            baseline_mean=10.0,
            baseline_stddev=2.0,
            sample_count=25,
            severity="HIGH",
        )
        incident = await IncidentService(IncidentRepository(db)).correlate_anomaly(anomaly)
        await db.commit()
        return incident.id


# -- _freshness --------------------------------------------------------------------------


def test_freshness_is_none_without_a_timestamp():
    assert _freshness(None, stale_after=timedelta(minutes=5)) is None


def test_freshness_flags_stale_once_past_the_threshold():
    old = (utcnow() - timedelta(minutes=10)).isoformat()
    fresh = _freshness(old, stale_after=timedelta(minutes=5))
    assert fresh is not None
    assert fresh.is_stale is True
    assert fresh.age_seconds >= 590  # ~10 minutes, allowing for test execution time


def test_freshness_is_not_stale_within_the_threshold():
    recent = (utcnow() - timedelta(seconds=5)).isoformat()
    fresh = _freshness(recent, stale_after=timedelta(minutes=5))
    assert fresh is not None
    assert fresh.is_stale is False


# -- _correlate_blocking -------------------------------------------------------------------


def test_correlate_blocking_names_the_blocker_hold_duration_and_waiter_count():
    blocking_chain_evidence = {
        "available": True,
        "sampled_at": utcnow().isoformat(),
        "blocking_chains": [
            {
                "blocked_sid": 102,
                "blocked_username": "APP_USER",
                "blocked_sql_id": "abc123",
                "blocker_sid": 101,
                "blocker_username": "BATCH_JOB",
                "blocker_sql_id": None,
                "blocker_in_this_sample": True,
            }
        ],
    }
    active_sessions_evidence = {
        "available": True,
        "sessions": [
            {"sid": 101, "username": "BATCH_JOB", "last_call_et": 45, "event": None},
            {"sid": 102, "username": "APP_USER", "last_call_et": 3, "event": "enq: TX - row lock contention"},
        ],
    }

    findings = _correlate_blocking(blocking_chain_evidence, active_sessions_evidence)

    assert len(findings) == 1
    f = findings[0]
    assert f.category == "blocking"
    assert f.entities == {"blocker_sid": 101, "waiter_count": 1, "blocker_username": "BATCH_JOB"}
    assert "SID 101" in f.summary
    assert "45s" in f.summary
    assert "1 session(s)" in f.summary
    assert "enq: TX - row lock contention" in f.summary
    assert f.severity == "HIGH"  # 1 waiter, short hold - below either critical threshold


def test_correlate_blocking_escalates_to_critical_on_a_long_hold_even_with_one_waiter():
    blocking_chain_evidence = {
        "available": True,
        "sampled_at": utcnow().isoformat(),
        "blocking_chains": [
            {"blocked_sid": 102, "blocked_username": "APP_USER", "blocked_sql_id": None,
             "blocker_sid": 101, "blocker_username": "BATCH_JOB", "blocker_sql_id": None,
             "blocker_in_this_sample": True}
        ],
    }
    active_sessions_evidence = {"available": True, "sessions": [{"sid": 101, "username": "BATCH_JOB", "last_call_et": 812, "event": None}]}

    findings = _correlate_blocking(blocking_chain_evidence, active_sessions_evidence)

    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"  # 812s hold alone crosses the 300s critical threshold


def test_correlate_blocking_escalates_to_critical_with_many_waiters():
    blocking_chain_evidence = {
        "available": True,
        "sampled_at": utcnow().isoformat(),
        "blocking_chains": [
            {"blocked_sid": 100 + i, "blocked_username": "APP_USER", "blocked_sql_id": None,
             "blocker_sid": 1, "blocker_username": "BATCH_JOB", "blocker_sql_id": None,
             "blocker_in_this_sample": True}
            for i in range(37)
        ],
    }
    active_sessions_evidence = {"available": True, "sessions": [{"sid": 1, "username": "BATCH_JOB", "last_call_et": 30, "event": None}]}

    findings = _correlate_blocking(blocking_chain_evidence, active_sessions_evidence)

    assert len(findings) == 1
    assert findings[0].severity == "CRITICAL"
    assert findings[0].entities["waiter_count"] == 37


def test_correlate_blocking_skips_a_blocker_not_present_in_this_sample():
    blocking_chain_evidence = {
        "available": True,
        "sampled_at": utcnow().isoformat(),
        "blocking_chains": [
            {"blocked_sid": 102, "blocked_username": "APP_USER", "blocked_sql_id": None,
             "blocker_sid": 999, "blocker_username": None, "blocker_sql_id": None,
             "blocker_in_this_sample": False}
        ],
    }
    assert _correlate_blocking(blocking_chain_evidence, {"available": False}) == []


def test_correlate_blocking_is_empty_when_unavailable_or_no_chains():
    assert _correlate_blocking({"available": False}, {"available": False}) == []
    assert _correlate_blocking({"available": True, "blocking_chains": []}, {"available": False}) == []


# -- _correlate_sql_regression --------------------------------------------------------------


def test_correlate_sql_regression_names_the_sql_id_and_ratio():
    evidence = {
        "available": True,
        "regressions": [
            {
                "sql_id": "abc123sqlid",
                "regression_kind": "PLAN_CHANGE",
                "severity": "HIGH",
                "first_detected_at": "2026-01-01T00:00:00",
                "last_detected_at": "2026-01-01T00:05:00",
                "ratio": 9.0,
            }
        ],
    }
    findings = _correlate_sql_regression(evidence)
    assert len(findings) == 1
    assert findings[0].severity == "HIGH"
    assert "abc123sqlid" in findings[0].summary
    assert "9.0x" in findings[0].summary
    assert findings[0].entities == {"sql_id": "abc123sqlid", "regression_kind": "PLAN_CHANGE"}


def test_correlate_sql_regression_is_empty_when_unavailable():
    assert _correlate_sql_regression({"available": False}) == []


# -- _correlate_capacity ---------------------------------------------------------------------


def test_correlate_capacity_only_surfaces_tablespaces_with_a_real_open_alert():
    evidence = {
        "available": True,
        "tablespaces": [
            {"tablespace_name": "SYSTEM", "pct_used": 12.0, "open_alert_severity": None},
            {"tablespace_name": "GLDDATA01", "pct_used": 97.0, "open_alert_severity": "CRITICAL"},
        ],
    }
    findings = _correlate_capacity(evidence)
    assert len(findings) == 1
    assert findings[0].entities["tablespace_name"] == "GLDDATA01"
    assert findings[0].severity == "CRITICAL"
    assert findings[0].freshness is None  # this evidence shape carries no top-level sampled_at


# -- _correlate_waits -------------------------------------------------------------------------


def test_correlate_waits_names_the_dominant_wait_class():
    evidence = {
        "available": True,
        "sampled_at": utcnow().isoformat(),
        "wait_classes": [{"wait_class": "User I/O", "centisec_waited_per_sec": 42.5}],
    }
    findings = _correlate_waits(evidence)
    assert len(findings) == 1
    assert findings[0].severity == "INFO"
    assert "User I/O" in findings[0].summary


def test_correlate_waits_is_empty_when_the_dominant_class_is_zero():
    evidence = {"available": True, "wait_classes": [{"wait_class": "User I/O", "centisec_waited_per_sec": 0}]}
    assert _correlate_waits(evidence) == []


# -- _compute_coverage ------------------------------------------------------------------------


def test_coverage_is_sparse_with_no_clear_findings():
    from app.ai.investigation import ToolCallStatus

    statuses = [ToolCallStatus("get_wait_profile", called=True, returned_evidence=False, reason="none yet")]
    coverage = _compute_coverage(statuses, findings=[])
    assert coverage.coverage_label == "sparse"
    assert coverage.categories_empty == ["get_wait_profile"]


def test_coverage_is_clear_with_one_high_severity_category():
    from app.ai.investigation import Finding, ToolCallStatus

    statuses = [ToolCallStatus("get_blocking_chain", called=True, returned_evidence=True)]
    findings = [Finding("blocking_1", "blocking", "CRITICAL", "SID 1 is blocking 5 sessions.")]
    coverage = _compute_coverage(statuses, findings)
    assert coverage.coverage_label == "clear"
    assert coverage.ambiguous is False


def test_coverage_is_partial_with_two_independent_high_severity_categories():
    from app.ai.investigation import Finding, ToolCallStatus

    statuses = [
        ToolCallStatus("get_blocking_chain", called=True, returned_evidence=True),
        ToolCallStatus("get_tablespace_trend", called=True, returned_evidence=True),
    ]
    findings = [
        Finding("blocking_1", "blocking", "HIGH", "..."),
        Finding("capacity_x", "capacity", "CRITICAL", "..."),
    ]
    coverage = _compute_coverage(statuses, findings)
    assert coverage.coverage_label == "partial"
    assert coverage.ambiguous is True


# -- gather_evidence: scope integrity (needs a real DB) ----------------------------------------


async def test_gather_evidence_rejects_a_database_id_that_disagrees_with_the_incident(client, admin_headers):
    db_a = await _register_database(client, admin_headers, name="SCOPE_A")
    db_b = await _register_database(client, admin_headers, name="SCOPE_B")
    incident_id = await _open_incident(db_a)

    async with SessionLocal() as db:
        with pytest.raises(EvidenceScopeConflictError):
            await gather_evidence(db, database_id=db_b, incident_id=incident_id)


async def test_gather_evidence_derives_database_id_when_only_incident_id_is_given(client, admin_headers):
    db_a = await _register_database(client, admin_headers, name="SCOPE_C")
    incident_id = await _open_incident(db_a)

    async with SessionLocal() as db:
        bundle = await gather_evidence(db, database_id=None, incident_id=incident_id)

    assert bundle.database_id == db_a
    assert "get_database_health" in [s.tool_name for s in bundle.tool_statuses]


async def test_gather_evidence_allows_a_database_id_that_matches_the_incident(client, admin_headers):
    db_a = await _register_database(client, admin_headers, name="SCOPE_D")
    incident_id = await _open_incident(db_a)

    async with SessionLocal() as db:
        bundle = await gather_evidence(db, database_id=db_a, incident_id=incident_id)

    assert bundle.database_id == db_a


async def test_gather_evidence_raises_not_found_for_a_bogus_incident_id(client, admin_headers):
    async with SessionLocal() as db:
        with pytest.raises(NotFoundError):
            await gather_evidence(db, database_id=None, incident_id="does-not-exist")


# -- format_age / render_findings_as_facts -------------------------------------------------


def test_format_age_buckets_like_the_frontends_formatRelativeTime():
    assert format_age(45) == "45s"
    assert format_age(125) == "2m"
    assert format_age(7200) == "2h"
    assert format_age(200_000) == "2d"


def test_render_findings_as_facts_is_honest_when_there_are_none():
    assert render_findings_as_facts([]) == ""


def test_render_findings_as_facts_states_the_summary_severity_and_freshness_verbatim():
    finding = Finding(
        finding_id="blocking_421",
        category="blocking",
        severity="CRITICAL",
        summary="SID 421 has been holding a transaction for 812s and is blocking 37 session(s).",
        entities={"blocker_sid": 421, "waiter_count": 37},
        freshness=Freshness(sampled_at=utcnow(), age_seconds=18.0, is_stale=False),
    )
    rendered = render_findings_as_facts([finding])
    assert "[CRITICAL]" in rendered
    assert "SID 421 has been holding a transaction for 812s and is blocking 37 session(s)." in rendered
    assert "sampled 18s ago" in rendered
    assert "stale" not in rendered


def test_render_findings_as_facts_flags_stale_evidence_for_caution():
    finding = Finding(
        finding_id="capacity_x", category="capacity", severity="HIGH", summary="Tablespace X is 96% used.",
        freshness=Freshness(sampled_at=utcnow(), age_seconds=999_999, is_stale=True),
    )
    assert "this may be stale" in render_findings_as_facts([finding])


def test_render_findings_as_facts_is_honest_about_unknown_freshness():
    finding = Finding("capacity_x", "capacity", "CRITICAL", "Tablespace X is 97% used.", freshness=None)
    assert "freshness unknown" in render_findings_as_facts([finding])


# -- gather_fleet_evidence (Activity tab Phase 4a, needs a real DB) ---------------------------


class _FakeRedis:
    """In-memory stand-in for redis.asyncio.Redis - just enough of the get/set surface
    gather_fleet_evidence actually calls."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


class _BrokenRedis:
    """Simulates Redis being genuinely unreachable - every call raises."""

    async def get(self, key):
        raise ConnectionError("redis unreachable")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis unreachable")


async def test_gather_fleet_evidence_is_empty_with_no_active_databases(client, admin_headers):
    async with SessionLocal() as db:
        bundle = await gather_fleet_evidence(db)
    assert bundle.findings == []
    assert bundle.database_id is None
    assert bundle.coverage.coverage_label == "sparse"


async def test_gather_fleet_evidence_merges_and_tags_findings_across_databases(client, admin_headers):
    db_a = await _register_database(client, admin_headers, name="FLEET_A")
    db_b = await _register_database(client, admin_headers, name="FLEET_B")

    async with SessionLocal() as db:
        rows = [
            SessionSampleRow(100, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
            SessionSampleRow(
                101, 2, "APP_USER", "fleetblksql1", "enq: TX - row lock contention", "Application",
                "ACTIVE", 100, "app01", "sqlplus", None, None,
            ),
        ]
        await TelemetryRepository(db).insert_session_samples(db_a, utcnow(), rows)
        await db.commit()

    async with SessionLocal() as db:
        bundle = await gather_fleet_evidence(db)

    assert bundle.database_id is None  # fleet-wide, never scoped to one database
    blocking_findings = [f for f in bundle.findings if f.category == "blocking"]
    assert len(blocking_findings) == 1
    finding = blocking_findings[0]
    assert finding.entities["database_id"] == db_a
    assert finding.entities["database_name"] == "FLEET_A"
    assert finding.finding_id.startswith(f"{db_a}:")
    assert db_b not in finding.finding_id


async def test_gather_fleet_evidence_tool_status_reflects_whether_any_database_had_evidence(client, admin_headers):
    await _register_database(client, admin_headers, name="FLEET_QUIET")

    async with SessionLocal() as db:
        bundle = await gather_fleet_evidence(db)

    statuses = {s.tool_name: s for s in bundle.tool_statuses}
    assert "get_blocking_chain" in statuses
    assert statuses["get_blocking_chain"].returned_evidence is False
    assert "database(s)" in statuses["get_blocking_chain"].reason


async def test_gather_fleet_evidence_uses_the_cache_on_a_hit_without_touching_the_db(client, admin_headers):
    await _register_database(client, admin_headers, name="FLEET_CACHE_HIT")
    fake = _FakeRedis()
    sentinel = Finding("sentinel:1", "blocking", "CRITICAL", "This came from the cache, not the DB.")
    fake.store[FLEET_EVIDENCE_CACHE_KEY] = _serialize_fleet_bundle(
        EvidenceBundle(
            database_id=None, incident_id=None, raw_evidence={}, tool_statuses=[], findings=[sentinel],
            coverage=_compute_coverage([], [sentinel]),
        )
    )

    async with SessionLocal() as db:
        bundle = await gather_fleet_evidence(db, redis=fake)

    assert [f.finding_id for f in bundle.findings] == ["sentinel:1"]


async def test_gather_fleet_evidence_populates_the_cache_on_a_miss(client, admin_headers):
    await _register_database(client, admin_headers, name="FLEET_CACHE_MISS")
    fake = _FakeRedis()

    async with SessionLocal() as db:
        await gather_fleet_evidence(db, redis=fake)

    assert FLEET_EVIDENCE_CACHE_KEY in fake.store


async def test_gather_fleet_evidence_falls_back_to_uncached_when_redis_is_unreachable(client, admin_headers):
    await _register_database(client, admin_headers, name="FLEET_REDIS_DOWN")

    async with SessionLocal() as db:
        bundle = await gather_fleet_evidence(db, redis=_BrokenRedis())

    assert bundle.coverage.coverage_label == "sparse"  # never raised, degraded to a real uncached compute


def test_fleet_bundle_serialize_round_trips_findings_and_freshness():
    finding = Finding(
        finding_id="db-1:blocking_100",
        category="blocking",
        severity="HIGH",
        summary="SID 100 is blocking.",
        entities={"database_id": "db-1", "database_name": "PROD01"},
        freshness=Freshness(sampled_at=utcnow(), age_seconds=12.0, is_stale=False),
    )
    bundle = EvidenceBundle(
        database_id=None, incident_id=None, raw_evidence={},
        tool_statuses=[ToolCallStatus("get_blocking_chain", called=True, returned_evidence=True)],
        findings=[finding], coverage=_compute_coverage([], [finding]),
    )
    restored = _deserialize_fleet_bundle(_serialize_fleet_bundle(bundle))
    assert restored.findings[0].finding_id == "db-1:blocking_100"
    assert restored.findings[0].freshness.age_seconds == 12.0
    assert restored.tool_statuses[0].tool_name == "get_blocking_chain"
