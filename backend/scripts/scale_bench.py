"""Master Plan Phase 25 (Scale testing) benchmark harness.

Standalone script, not a pytest module - it deliberately seeds large fleets (10/50/100
databases) and multi-thousand-row telemetry/queue backlogs, which would be far too slow and
disk-heavy to run on every CI invocation. Run it directly:

    cd backend
    python scripts/scale_bench.py

It measures, against this platform's own code paths (collector, analytics, API reads,
notification/integration queues, AI copilot orchestration):

  1. Collector overhead per database, at fleet sizes 10 / 50 / 100
  2. Analytics-engine overhead per database, at the same fleet sizes
  3. Fleet-list and incident-list API read latency (p50/p95), at the same fleet sizes
  4. Storage growth: on-disk size after seeding a realistic multi-round telemetry history
  5. Notification queue throughput draining a large backlog (the independent integration/
     webhook outbox shares the identical bounded-batch design and is instead covered by a
     dedicated correctness test - see tests/test_scale_hardening.py)
  6. AI Copilot orchestration overhead under concurrent requests (bypassing the per-user
     rate limit, since that is already covered by tests/test_scale_hardening.py's
     correctness test - this measures raw concurrency behavior instead)

IMPORTANT - what this does and does not prove:
This sandboxed environment has no real Postgres server and no real Redis server reachable
(see docs/SCALE_TEST_REPORT.md's Known Gaps) - production runs both (docker-compose.yml).
Every number here therefore reflects SQLite-on-local-disk plus an in-process fakeredis
queue, not the production Postgres/Redis stack. What IS representative regardless of
backend: this platform's own Python-level overhead per database (ORM object construction,
normalization, Pydantic serialization, correlation/baseline math) and whether any code path
has an accidental O(n^2) or unbounded-result-set shape that would get worse, not better,
under a faster database. What is NOT representative: absolute latency numbers, and any
claim about Postgres/Redis behavior under concurrent connections from multiple worker
processes (this script runs everything in one process).
"""

import asyncio
import json
import math
import os
import statistics
import tempfile
import time
import uuid
from dataclasses import dataclass, field

_DB_PATH = os.path.join(tempfile.gettempdir(), f"ocpcc_scale_bench_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["JWT_SECRET_KEY"] = "scale-bench-only-jwt-secret-not-for-production-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = "admin@bootstrap.dev"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "AdminBootstrap123!"
os.environ["COLLECTOR_ENABLED"] = "false"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import fakeredis.aioredis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

import app.redis_client as redis_client_module  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import utcnow  # noqa: E402
from app.redis_client import get_redis  # noqa: E402
from app.repositories.audit_repo import AuditRepository  # noqa: E402
from app.repositories.database_repo import MonitoredDatabaseRepository  # noqa: E402
from app.repositories.incident_repo import IncidentRepository  # noqa: E402
from app.repositories.notification_repo import NotificationEventRepository, NotificationSettingsRepository  # noqa: E402
from app.repositories.secret_repo import SecretRepository  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.analytics_service import AnalyticsService  # noqa: E402
from app.services.audit_service import AuditService  # noqa: E402
from app.services.collector_service import CollectorService  # noqa: E402
from app.services.copilot_service import CopilotAnswer, CopilotService  # noqa: E402
from app.services.notification_service import NotificationService  # noqa: E402
from app.services.secret_service import LocalEncryptedSecretProvider  # noqa: E402

_fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)


async def _override_get_redis():
    return _fake_redis


app.dependency_overrides[get_redis] = _override_get_redis
redis_client_module._redis = _fake_redis

SESSION_ROWS = [
    (100 + i, 5000 + i, "APPUSER", "abc123sqlid", "db file sequential read", "User I/O", "ACTIVE", None, "app01", "sqlplus", None, None)
    for i in range(20)
]
WAIT_ROWS = [
    ("db file sequential read", "User I/O", 1000, 2500),
    ("log file sync", "Commit", 200, 800),
    ("CPU + Wait for CPU", "CPU", 5000, 5000),
]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._one = None
        self._rows: list[tuple] = []

    def execute(self, statement: str) -> None:
        upper = statement.upper()
        if "V$SESSION" in upper and "COUNT(*)" in upper:
            self._one = (self.conn.active_session_count,)
        elif "V$SESSION" in upper:
            self._rows = SESSION_ROWS
        elif "V$SYSTEM_EVENT" in upper:
            self._rows = WAIT_ROWS
        elif "V$INSTANCE" in upper:
            self._one = ("OPEN",)
        else:
            raise AssertionError(f"unexpected query in scale bench: {statement}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self):
        self.active_session_count = len(SESSION_ROWS)

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


def instant_fake_connector(_params):
    """Returns instantly, unlike a real Oracle TCP round-trip - isolates THIS platform's
    own per-database overhead (ORM writes, normalization) from Oracle network latency,
    which is not something this sandboxed environment can measure at all (no real Oracle
    instance is reachable - see docs/RUNBOOK.md's Known Gaps)."""
    return FakeConnection()


@dataclass
class Timing:
    label: str
    samples_ms: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.samples_ms.append(ms)

    def summary(self) -> dict:
        s = sorted(self.samples_ms)
        n = len(s)
        return {
            "label": self.label,
            "n": n,
            "min_ms": round(s[0], 2) if n else None,
            "p50_ms": round(s[n // 2], 2) if n else None,
            "p95_ms": round(s[min(n - 1, math.ceil(n * 0.95) - 1)], 2) if n else None,
            "max_ms": round(s[-1], 2) if n else None,
            "mean_ms": round(statistics.mean(s), 2) if n else None,
        }


async def _reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await _fake_redis.flushall()
    await seed()


async def _admin_user_id(db) -> str:
    admin_user = await UserRepository(db).get_by_email("admin@bootstrap.dev")
    assert admin_user is not None
    return admin_user.id


async def _register_fleet(n: int) -> list[str]:
    ids = []
    async with SessionLocal() as db:
        repo = MonitoredDatabaseRepository(db)
        admin_id = await _admin_user_id(db)
        for i in range(n):
            row = await repo.create(
                name=f"BENCH{i:05d}",
                host="oracle.internal.ocpcc.dev",
                port=1521,
                service_name="ORCLPDB1",
                edition_hint="SE2",
                created_by_user_id=admin_id,
            )
            ids.append(row.id)
        await db.commit()
    return ids


async def _bench_collector_and_analytics(database_ids: list[str], rounds: int) -> tuple[Timing, Timing]:
    collector_timing = Timing(f"collector.run_once_for_database (fleet={len(database_ids)})")
    analytics_timing = Timing(f"analytics.run_once_for_database (fleet={len(database_ids)})")
    collector = CollectorService(instant_fake_connector)
    analytics = AnalyticsService()

    for _round in range(rounds):
        for db_id in database_ids:
            start = time.perf_counter()
            await collector.run_once_for_database(db_id)
            collector_timing.add((time.perf_counter() - start) * 1000)

            start = time.perf_counter()
            await analytics.run_once_for_database(db_id)
            analytics_timing.add((time.perf_counter() - start) * 1000)

    return collector_timing, analytics_timing


async def _bench_api_reads(fleet_size: int, repeats: int) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/auth/login", json={"email": "admin@bootstrap.dev", "password": "AdminBootstrap123!"}
        )
        assert resp.status_code == 200, resp.text
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        list_databases_timing = Timing(f"GET /api/databases (fleet={fleet_size})")
        list_incidents_timing = Timing(f"GET /api/incidents (fleet={fleet_size})")

        for _ in range(repeats):
            start = time.perf_counter()
            resp = await client.get("/api/databases", headers=headers)
            list_databases_timing.add((time.perf_counter() - start) * 1000)
            assert resp.status_code == 200
            returned_count = len(resp.json())

            start = time.perf_counter()
            resp = await client.get("/api/incidents", headers=headers)
            list_incidents_timing.add((time.perf_counter() - start) * 1000)
            assert resp.status_code == 200

    return {
        "fleet_size": fleet_size,
        "returned_by_list_databases": returned_count,
        "truncated_by_200_row_page_cap": returned_count < fleet_size,
        "list_databases": list_databases_timing.summary(),
        "list_incidents": list_incidents_timing.summary(),
    }


def _db_file_size_bytes() -> int:
    return os.path.getsize(_DB_PATH) if os.path.exists(_DB_PATH) else 0


async def _bench_queue_saturation(backlog_size: int) -> dict:
    async with SessionLocal() as db:
        repo = MonitoredDatabaseRepository(db)
        admin_id = await _admin_user_id(db)
        target = await repo.create(
            name="QUEUEBENCH01",
            host="oracle.internal.ocpcc.dev",
            port=1521,
            service_name="ORCLPDB1",
            edition_hint="SE2",
            created_by_user_id=admin_id,
        )
        db_id = target.id
        settings_repo = NotificationSettingsRepository(db)
        await settings_repo.update(
            smtp_host="smtp.internal.ocpcc.dev",
            smtp_port=587,
            smtp_use_tls=True,
            sender_email="alerts@ocpcc.dev",
            sender_name="OCPCC Alerts",
            default_recipients=["oncall@ocpcc.dev"],
            escalation_recipients=[],
        )
        incident_repo = IncidentRepository(db)
        event_repo = NotificationEventRepository(db)
        now = utcnow()
        for i in range(backlog_size):
            incident = await incident_repo.create(
                monitored_database_id=db_id,
                severity="HIGH",
                title=f"Queue bench incident {i}",
                opened_at=now,
                last_evidence_at=now,
            )
            await event_repo.create(
                incident_id=incident.id,
                dedupe_key=f"queue-bench-{i}",
                severity="HIGH",
                recipients=["oncall@ocpcc.dev"],
                subject=f"[HIGH] Queue bench {i}",
                body_text="synthetic backlog entry",
            )
        await db.commit()

    async def fake_sender(**kwargs):
        return None

    start = time.perf_counter()
    ticks = 0
    drained = 0
    while True:
        async with SessionLocal() as db:
            service = NotificationService(
                NotificationSettingsRepository(db),
                NotificationEventRepository(db),
                MonitoredDatabaseRepository(db),
                IncidentRepository(db),
                LocalEncryptedSecretProvider(SecretRepository(db)),
                sender=fake_sender,
            )
            before = len(await NotificationEventRepository(db).list_pending_or_retrying(limit=1000))
            await service.process_pending()
            await db.commit()
        ticks += 1
        async with SessionLocal() as db:
            remaining = await NotificationEventRepository(db).list_pending_or_retrying(limit=1000)
        drained += before - len(remaining)
        if not remaining:
            break
        if ticks > backlog_size:  # safety valve, should never trigger
            break
    elapsed = time.perf_counter() - start

    return {
        "backlog_size": backlog_size,
        "batch_size_per_tick": 50,
        "ticks_to_drain": ticks,
        "expected_ticks": math.ceil(backlog_size / 50),
        "items_drained": drained,
        "total_seconds": round(elapsed, 3),
        "items_per_second": round(backlog_size / elapsed, 1) if elapsed > 0 else None,
    }


class DelayedFakeLLMClient:
    """Simulates a local LLM's per-request latency (a real gpt-oss-120b call is not
    measurable in this environment - no local model server exists here) so the concurrency
    benchmark reflects realistic overlap rather than a near-zero-latency no-op racing
    through the orchestration code with nothing to actually overlap."""

    def __init__(self, delay_seconds: float = 0.15):
        self.delay_seconds = delay_seconds

    async def complete(self, messages, *, temperature=0.2, max_tokens=1200):
        await asyncio.sleep(self.delay_seconds)
        return "Synthetic concurrent-load answer.", "fake-model-bench"


async def _bench_ai_concurrency(concurrency_levels: list[int]) -> list[dict]:
    async with SessionLocal() as db:
        repo = MonitoredDatabaseRepository(db)
        actor_user_id = await _admin_user_id(db)
        target = await repo.create(
            name="AIBENCH01",
            host="oracle.internal.ocpcc.dev",
            port=1521,
            service_name="ORCLPDB1",
            edition_hint="SE2",
            created_by_user_id=actor_user_id,
        )
        db_id = target.id
        await db.commit()

    results = []
    for concurrency in concurrency_levels:
        llm = DelayedFakeLLMClient(delay_seconds=0.15)

        async def _ask(i: int) -> CopilotAnswer:
            async with SessionLocal() as db:
                service = CopilotService(db, llm, AuditService(AuditRepository(db)))
                answer = await service.ask(
                    f"Why is the fleet slow? (concurrent request {i})",
                    database_id=db_id,
                    incident_id=None,
                    actor_user_id=actor_user_id,
                    ip_address="127.0.0.1",
                )
                await db.commit()
                return answer

        start = time.perf_counter()
        answers = await asyncio.gather(*[_ask(i) for i in range(concurrency)])
        elapsed = time.perf_counter() - start
        serial_lower_bound = concurrency * llm.delay_seconds

        results.append(
            {
                "concurrency": concurrency,
                "total_seconds": round(elapsed, 3),
                "serial_lower_bound_seconds": round(serial_lower_bound, 3),
                "concurrency_speedup_factor": round(serial_lower_bound / elapsed, 2) if elapsed > 0 else None,
                "answers_returned": len(answers),
                "all_answers_non_empty": all(a.answer for a in answers),
            }
        )
    return results


async def main() -> None:
    # Per-fleet-size storage numbers live inside each fleet_scenarios entry
    # (sqlite_file_bytes_after_seeding / sqlite_bytes_per_database).
    report: dict = {"fleet_scenarios": [], "queue": {}, "ai_concurrency": []}

    fleet_sizes = [10, 50, 100]
    rounds_per_fleet_size = {10: 20, 50: 8, 100: 4}  # kept the sweep's own runtime bounded; see report methodology

    for fleet_size in fleet_sizes:
        print(f"\n=== Fleet size {fleet_size} ===", flush=True)
        await _reset_schema()
        database_ids = await _register_fleet(fleet_size)
        rounds = rounds_per_fleet_size[fleet_size]

        collector_timing, analytics_timing = await _bench_collector_and_analytics(database_ids, rounds)
        api_timing = await _bench_api_reads(fleet_size, repeats=20)
        storage_bytes = _db_file_size_bytes()

        scenario = {
            "fleet_size": fleet_size,
            "collection_rounds_seeded": rounds,
            "total_session_wait_health_rows_approx": fleet_size * rounds,
            "collector": collector_timing.summary(),
            "analytics": analytics_timing.summary(),
            "api_reads": api_timing,
            "sqlite_file_bytes_after_seeding": storage_bytes,
            "sqlite_bytes_per_database": round(storage_bytes / fleet_size, 1),
        }
        report["fleet_scenarios"].append(scenario)
        print(json.dumps(scenario, indent=2), flush=True)

    print("\n=== Queue saturation (500-item backlog) ===", flush=True)
    await _reset_schema()
    report["queue"] = await _bench_queue_saturation(backlog_size=500)
    print(json.dumps(report["queue"], indent=2), flush=True)

    print("\n=== AI copilot concurrency ===", flush=True)
    await _reset_schema()
    report["ai_concurrency"] = await _bench_ai_concurrency([1, 10, 25, 50])
    print(json.dumps(report["ai_concurrency"], indent=2), flush=True)

    out_path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "scale_bench_results.json")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull results written to {out_path}", flush=True)

    try:
        os.remove(_DB_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
