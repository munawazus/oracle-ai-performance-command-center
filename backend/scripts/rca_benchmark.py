"""Enterprise Readiness Plan E.1 (master plan Phase 20) RCA quality benchmark.

Standalone script, not a pytest module - same reasoning as scripts/scale_bench.py: this
deliberately calls a REAL local LLM server over the network, which would be slow, flaky, and
simply unavailable in CI (no real LLM is ever reachable there). Run it directly, once a local
OpenAI-compatible server (Ollama/vLLM/llama.cpp) is reachable:

    cd backend
    PYTHONPATH=. python scripts/rca_benchmark.py

(PYTHONPATH=. is required here - this repo has no installed package/pyproject.toml putting
`app` on sys.path, so a bare `python scripts/rca_benchmark.py` fails with `ModuleNotFoundError:
No module named 'app'`; confirmed directly, and the same is true of scripts/scale_bench.py's
own documented invocation, a pre-existing inaccuracy in that script's docstring, not
introduced here.)

It builds the five gold-incident scenarios (app/rca_benchmark/gold_incidents.py) against this
platform's own real repositories/services (not literal dict mocks), drives each through the
REAL RcaService.investigate() -> a real OpenAICompatibleClient -> a real model, and scores
each real answer with the deterministic checks in app/rca_benchmark/scoring.py. This is a
genuine benchmark of one specific model's actual behavior, not a simulation - an honest
"3 of 5 scenarios pass every check" is a valid, useful result, not a bug to hide.

IMPORTANT - what this does and does not prove: like scale_bench.py, this runs against its own
isolated SQLite database and in-process fakeredis queue (so it never touches the real
docker-compose Postgres/Redis, and can be re-run repeatedly without side effects) - but the
LLM call itself is 100% real network I/O to a real model server. What the deterministic checks
can and cannot catch is documented in app/rca_benchmark/scoring.py's own module docstring -
most importantly, "no fabricated identifiers" is a heuristic, not a perfect hallucination
detector.
"""

import asyncio
import json
import os
import tempfile
import time
import uuid

_DB_PATH = os.path.join(tempfile.gettempdir(), f"ocpcc_rca_bench_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["JWT_SECRET_KEY"] = "rca-bench-only-jwt-secret-not-for-production-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = "admin@bootstrap.dev"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "AdminBootstrap123!"
os.environ["COLLECTOR_ENABLED"] = "false"
# backend/.env (loaded by Settings when running host-side, outside docker-compose) is a bare
# local-dev fallback with no AI_* keys at all - it does NOT carry the real model configuration
# from the repo-root .env (which docker-compose's backend container reads instead). Default to
# the real, already-pulled model this benchmark is meant to score; set AI_PRIMARY_MODEL/
# AI_FALLBACK_MODEL yourself before running this script to benchmark a different one.
os.environ.setdefault("AI_PRIMARY_MODEL", "qwen2.5:7b-instruct")
os.environ.setdefault("AI_FALLBACK_MODEL", "qwen2.5:7b-instruct")
# Settings' own class default (30s) is tuned for a hosted/fast model - too short for a local
# 7B model generating a ~1500-token JSON response on a single machine (confirmed directly: two
# of five scenarios timed out at 30s on the first run of this script). 240s matches what the
# real docker-compose deployment's .env already configures for this exact model.
os.environ.setdefault("AI_REQUEST_TIMEOUT_SECONDS", "240")

from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import fakeredis.aioredis  # noqa: E402

import app.redis_client as redis_client_module  # noqa: E402
from app.ai.gateway import build_llm_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.rca_benchmark.gold_incidents import GOLD_SCENARIOS  # noqa: E402
from app.rca_benchmark.scoring import CheckResult, ScenarioScore, score_scenario, summarize_scores  # noqa: E402
from app.repositories.audit_repo import AuditRepository  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.seed import seed  # noqa: E402
from app.services.audit_service import AuditService  # noqa: E402
from app.services.rca_service import RcaService, gather_rca_evidence  # noqa: E402

_fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
redis_client_module._redis = _fake_redis

# host.docker.internal (this platform's own .env, for the backend container) only resolves
# from inside Docker - this script runs on the host, so it talks to Ollama directly.
_DEFAULT_HOST_BASE_URL = "http://localhost:11434/v1"


async def _reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await _fake_redis.flushall()
    await seed()


async def _admin_user_id() -> str:
    async with SessionLocal() as db:
        admin_user = await UserRepository(db).get_by_email("admin@bootstrap.dev")
        assert admin_user is not None
        return admin_user.id


async def main() -> None:
    settings = get_settings()
    base_url = os.environ.get("RCA_BENCH_BASE_URL", _DEFAULT_HOST_BASE_URL)
    llm = build_llm_client(
        base_url=base_url,
        api_key=settings.ai_api_key,
        primary_model=settings.ai_primary_model,
        fallback_model=settings.ai_fallback_model,
        timeout_seconds=settings.ai_request_timeout_seconds,
    )
    print(f"Benchmarking against {base_url} - primary model {settings.ai_primary_model!r}.", flush=True)

    await _reset_schema()
    admin_id = await _admin_user_id()

    results = []
    for scenario in GOLD_SCENARIOS:
        print(f"\n=== {scenario.name}: {scenario.description} ===", flush=True)
        async with SessionLocal() as db:
            incident_id = await scenario.build(db, created_by_user_id=admin_id)
            await db.commit()

        start = time.perf_counter()
        async with SessionLocal() as db:
            evidence = await gather_rca_evidence(db, incident_id)
            service = RcaService(db, llm, AuditService(AuditRepository(db)))
            try:
                rca_result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address="127.0.0.1")
            except Exception as exc:  # noqa: BLE001 - a benchmark reports a gateway failure, never crashes on one
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                print(f"  LLM call failed: {type(exc).__name__}: {exc}", flush=True)
                results.append(
                    {"scenario_name": scenario.name, "available": False, "error": str(exc), "elapsed_ms": elapsed_ms}
                )
                continue
            await db.commit()
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        score = score_scenario(scenario.name, rca_result, evidence, evidence_is_sparse=scenario.evidence_is_sparse)
        print(f"  passed={score.passed} elapsed_ms={elapsed_ms}", flush=True)
        for check in score.checks:
            print(f"    [{'PASS' if check.passed else 'FAIL'}] {check.check_name}: {check.detail}", flush=True)

        results.append(
            {
                "scenario_name": scenario.name,
                "available": True,
                "passed": score.passed,
                "elapsed_ms": elapsed_ms,
                "model": rca_result.model,
                "checks": [c.__dict__ for c in score.checks],
                "rca_result": {
                    "probable_root_cause": rca_result.probable_root_cause,
                    "confidence": rca_result.confidence,
                    "evidence_summary": rca_result.evidence_summary,
                    "secondary_contributors": rca_result.secondary_contributors,
                    "data_completeness_note": rca_result.data_completeness_note,
                },
            }
        )

    # Rebuild ScenarioScore objects from the raw per-scenario check lists collected above, so
    # summarize_scores' rollup can run over every real result, including a scenario where the
    # LLM call itself failed (scored as a single failed check, not silently dropped).
    for r in results:
        if r["available"]:
            r["_score"] = ScenarioScore(r["scenario_name"], [CheckResult(**c) for c in r["checks"]])
        else:
            r["_score"] = ScenarioScore(
                r["scenario_name"], [CheckResult("llm_call_succeeded", False, r["error"])]
            )
    summary = summarize_scores([r["_score"] for r in results])
    for r in results:
        del r["_score"]

    report = {
        "base_url": base_url,
        "primary_model": settings.ai_primary_model,
        "fallback_model": settings.ai_fallback_model,
        "results": results,
        "summary": summary,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "rca_benchmark_results.json")
    out_path = os.path.abspath(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n{summary['scenarios_fully_passed']}/{summary['scenario_count']} scenarios passed every check.", flush=True)
    print(f"Full results written to {out_path}", flush=True)

    try:
        os.remove(_DB_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
