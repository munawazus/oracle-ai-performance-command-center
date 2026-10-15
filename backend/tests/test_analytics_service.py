"""AnalyticsService only reads what the collector already wrote to Postgres/SQLite - no
Oracle connection involved at all, so these tests seed telemetry rows directly and drive
the real service, no fakes needed."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.analytics.baseline_engine import MIN_SAMPLES_FOR_ANOMALY
from app.db import SessionLocal
from app.models.analytics import Anomaly, MetricBaseline
from app.models.incident import Incident
from app.oracle.telemetry_probe import InstanceHealthSample, WaitSnapshotRow
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.analytics_service import AnalyticsService

DB_PAYLOAD = {
    "name": "ANALYTICS01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
WAIT_CLASS = "User I/O"


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_round(db_id: str, round_index: int, rate_per_sec: float, active_sessions: int) -> None:
    sampled_at = BASE_TIME + timedelta(seconds=60 * round_index)
    cumulative_time_waited = round(sum(60 * r for r in _rates_up_to(round_index, rate_per_sec)))
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_wait_snapshots(
            db_id,
            sampled_at,
            [WaitSnapshotRow(event="db file sequential read", wait_class=WAIT_CLASS, total_waits=round_index, time_waited_centisecs=cumulative_time_waited)],
        )
        await repo.insert_instance_health(
            db_id, sampled_at, "up", InstanceHealthSample(status="OPEN", active_session_count=active_sessions)
        )
        await db.commit()


def _rates_up_to(round_index: int, final_rate: float) -> list[float]:
    """Reconstructs the same per-round rate sequence _normal_rate() would have produced for
    every round up to and including `round_index`, so cumulative counters stay consistent
    with whatever rate each earlier round actually used."""
    return [_normal_rate(i) for i in range(1, round_index)] + [final_rate] if round_index > 0 else []


def _normal_rate(round_index: int) -> float:
    # Small, deterministic wobble around 100 - enough variance for a meaningful stddev,
    # nowhere near the magnitude of the spike used to test anomaly detection.
    return 100 + ((round_index % 5) - 2)


async def test_baseline_accumulates_and_flags_a_later_spike(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = AnalyticsService()

    # Round 0 establishes the first cumulative counters; rounds 1..N each produce one rate
    # sample when analytics runs (needs two consecutive rounds to compute a delta).
    await _seed_round(db_id, 0, _normal_rate(0), active_sessions=10)

    for i in range(1, MIN_SAMPLES_FOR_ANOMALY + 1):
        await _seed_round(db_id, i, _normal_rate(i), active_sessions=10)
        await service.run_once_for_database(db_id)

    metric_key = f"wait_rate_centisec_per_sec:{WAIT_CLASS}"
    async with SessionLocal() as db:
        baseline = (
            await db.execute(
                select(MetricBaseline).where(
                    MetricBaseline.monitored_database_id == db_id, MetricBaseline.metric_key == metric_key
                )
            )
        ).scalar_one()
        anomalies_so_far = (await db.execute(select(Anomaly))).scalars().all()

    assert baseline.sample_count == MIN_SAMPLES_FOR_ANOMALY
    assert 95 < baseline.mean < 105
    assert anomalies_so_far == []  # normal wobble never exceeded 2 stddev

    # Now a dramatic spike - the baseline has enough history to judge it.
    spike_round = MIN_SAMPLES_FOR_ANOMALY + 1
    await _seed_round(db_id, spike_round, rate_per_sec=100_000, active_sessions=10)
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        anomalies = (
            await db.execute(select(Anomaly).where(Anomaly.monitored_database_id == db_id))
        ).scalars().all()

    matching = [a for a in anomalies if a.metric_key == metric_key]
    assert len(matching) == 1
    assert matching[0].severity == "CRITICAL"
    assert matching[0].observed_value > 90_000
    assert matching[0].sample_count == MIN_SAMPLES_FOR_ANOMALY  # judged against pre-spike baseline


async def test_no_anomaly_before_minimum_sample_count(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = AnalyticsService()

    await _seed_round(db_id, 0, _normal_rate(0), active_sessions=10)
    await _seed_round(db_id, 1, rate_per_sec=100_000, active_sessions=10)  # spike on round 2 of 2
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        anomalies = (
            await db.execute(select(Anomaly).where(Anomaly.monitored_database_id == db_id))
        ).scalars().all()

    assert anomalies == []  # nowhere near MIN_SAMPLES_FOR_ANOMALY yet


async def test_single_round_only_computes_session_count_not_wait_rate(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    await _seed_round(db_id, 0, _normal_rate(0), active_sessions=7)

    await AnalyticsService().run_once_for_database(db_id)

    async with SessionLocal() as db:
        baselines = (
            await db.execute(select(MetricBaseline).where(MetricBaseline.monitored_database_id == db_id))
        ).scalars().all()

    metric_keys = {b.metric_key for b in baselines}
    assert "active_session_count" in metric_keys
    assert not any(k.startswith("wait_rate_centisec_per_sec:") for k in metric_keys)


async def test_cpu_metric_appears_once_two_real_rounds_exist(client, admin_headers):
    """Finding 7 (docs/QA_LIVE_FINDINGS.md): confirms cpu_centisec_used_per_sec becomes a real
    baseline/anomaly-detectable metric, not just a chart - the whole point of computing it in
    AnalyticsService.compute_current_metrics rather than only in the /cpu/history route."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        await repo.insert_instance_health(
            db_id, BASE_TIME, "up",
            InstanceHealthSample(status="OPEN", active_session_count=1, cpu_centisecs_used=100_000),
        )
        await repo.insert_instance_health(
            db_id, BASE_TIME + timedelta(minutes=1), "up",
            InstanceHealthSample(status="OPEN", active_session_count=1, cpu_centisecs_used=106_000),
        )
        await db.commit()

    await AnalyticsService().run_once_for_database(db_id)

    async with SessionLocal() as db:
        baselines = (
            await db.execute(select(MetricBaseline).where(MetricBaseline.monitored_database_id == db_id))
        ).scalars().all()

    cpu_baseline = next(b for b in baselines if b.metric_key == "cpu_centisec_used_per_sec")
    assert cpu_baseline.mean == round(6_000 / 60, 2)


async def test_cpu_metric_uses_the_two_most_recent_rounds_not_an_even_sample_across_all_history(
    client, admin_headers
):
    """Regression: compute_current_metrics must read the two most recent, adjacent rounds -
    not recent_instance_health's even-sampling, which (once more than a couple of rounds
    exist) spans the *whole* history and would pair the very first round ever recorded with
    the latest one instead of two genuinely adjacent rounds. Caught live against a real Oracle
    instance after 96 real rounds had accumulated - a 2-row fixture can't reproduce it, so this
    seeds 5."""
    db_id = await _register_database(client, admin_headers)
    async with SessionLocal() as db:
        repo = TelemetryRepository(db)
        for i, cpu in enumerate([100_000, 100_010, 100_020, 100_030, 106_030]):
            await repo.insert_instance_health(
                db_id, BASE_TIME + timedelta(minutes=i), "up",
                InstanceHealthSample(status="OPEN", active_session_count=1, cpu_centisecs_used=cpu),
            )
        await db.commit()

    await AnalyticsService().run_once_for_database(db_id)

    async with SessionLocal() as db:
        baselines = (
            await db.execute(select(MetricBaseline).where(MetricBaseline.monitored_database_id == db_id))
        ).scalars().all()

    cpu_baseline = next(b for b in baselines if b.metric_key == "cpu_centisec_used_per_sec")
    assert cpu_baseline.mean == 100.0  # (106_030 - 100_030) / 60s - the last two rounds only


async def test_run_once_all_active_does_not_raise_with_no_telemetry(client, admin_headers):
    await _register_database(client, admin_headers)
    await AnalyticsService().run_once_all_active()  # must not raise even with zero telemetry rows


async def test_detected_anomaly_is_correlated_into_an_incident(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    service = AnalyticsService()

    await _seed_round(db_id, 0, _normal_rate(0), active_sessions=10)
    for i in range(1, MIN_SAMPLES_FOR_ANOMALY + 1):
        await _seed_round(db_id, i, _normal_rate(i), active_sessions=10)
        await service.run_once_for_database(db_id)

    await _seed_round(db_id, MIN_SAMPLES_FOR_ANOMALY + 1, rate_per_sec=100_000, active_sessions=10)
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        anomalies = (
            await db.execute(select(Anomaly).where(Anomaly.monitored_database_id == db_id))
        ).scalars().all()
        incidents = (await db.execute(select(Incident).where(Incident.monitored_database_id == db_id))).scalars().all()

    flagged = [a for a in anomalies if a.severity is not None and a.incident_id is not None]
    assert len(flagged) >= 1
    assert len(incidents) == 1
    assert incidents[0].id == flagged[0].incident_id
    assert incidents[0].status == "OPEN"


async def test_a_reachability_failure_opens_a_real_incident_with_no_anomaly_involved(client, admin_headers):
    """The gap this closes: previously, a database going unreachable meant
    compute_current_metrics returned {} and run_once_for_database silently no-op'd - zero
    anomaly, zero incident, zero notification, forever, for as long as the database stayed
    down. A reachability failure never produces an Anomaly row at all (there's no baseline/
    z-score concept for a binary up/down signal) - this is a direct incident path."""
    db_id = await _register_database(client, admin_headers)
    sampled_at = BASE_TIME
    async with SessionLocal() as db:
        await TelemetryRepository(db).insert_instance_health(db_id, sampled_at, "unreachable", None)
        await db.commit()

    await AnalyticsService().run_once_for_database(db_id)

    async with SessionLocal() as db:
        anomalies = (
            await db.execute(select(Anomaly).where(Anomaly.monitored_database_id == db_id))
        ).scalars().all()
        incidents = (
            await db.execute(select(Incident).where(Incident.monitored_database_id == db_id))
        ).scalars().all()

    assert anomalies == []
    assert len(incidents) == 1
    assert incidents[0].severity == "CRITICAL"
    assert incidents[0].title == "Database unreachable"
    assert incidents[0].status == "OPEN"


async def test_repeated_reachability_failures_attach_to_the_same_incident_not_a_new_one_each_time(
    client, admin_headers
):
    """The main 15s collector retries every 15s while a database stays down - without
    correlation-window dedup, a multi-hour outage would open hundreds of incidents instead
    of one, escalating evidence on the same one (matching how a persistent metric anomaly
    already behaves via correlate_anomaly)."""
    db_id = await _register_database(client, admin_headers)
    service = AnalyticsService()

    async with SessionLocal() as db:
        await TelemetryRepository(db).insert_instance_health(db_id, BASE_TIME, "unreachable", None)
        await db.commit()
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        await TelemetryRepository(db).insert_instance_health(
            db_id, BASE_TIME + timedelta(seconds=15), "unreachable", None
        )
        await db.commit()
    await service.run_once_for_database(db_id)

    async with SessionLocal() as db:
        incidents = (
            await db.execute(select(Incident).where(Incident.monitored_database_id == db_id))
        ).scalars().all()

    assert len(incidents) == 1
    # SQLite round-trips a naive datetime regardless of what was stored - strip tzinfo from
    # the expected side rather than asserting on a timezone-aware value.
    assert incidents[0].last_evidence_at == (BASE_TIME + timedelta(seconds=15)).replace(tzinfo=None)
