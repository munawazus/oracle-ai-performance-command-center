"""RCA quality benchmark suite tests (Enterprise Readiness Plan E.1) - CI-safe, no real LLM.

Two layers: pure unit tests for each deterministic check in app/rca_benchmark/scoring.py (no
DB at all), and one integration test per gold-incident scenario that drives the real
RcaService.investigate() with FakeLLMClient twice - once with a hand-written "good" response
that should score cleanly, once with a hand-written "bad" response for the same scenario that
the scorer must catch - proving the scorer actually discriminates, not just that it runs.
"""

import json
from datetime import datetime, timezone

from app.ai.gateway import AIGatewayError
from app.ai.investigation import Finding, Freshness
from app.db import SessionLocal
from app.rca_benchmark.gold_incidents import GOLD_SCENARIOS
from app.rca_benchmark.scoring import (
    CONFIDENCE_CONSISTENCY,
    CONFIDENCE_MATCHES_COVERAGE,
    ENGAGES_WITH_EVIDENCE,
    NO_FABRICATED_IDENTIFIERS,
    NO_STALE_EVIDENCE_AS_CURRENT,
    SCHEMA_ADHERENCE,
    UNCERTAINTY_CALIBRATION,
    check_confidence_consistency,
    check_confidence_matches_coverage,
    check_engages_with_available_evidence,
    check_no_fabricated_identifiers,
    check_no_stale_evidence_presented_as_current,
    check_schema_adherence,
    check_uncertainty_calibration,
    score_scenario,
    summarize_scores,
)
from app.repositories.audit_repo import AuditRepository
from app.services.audit_service import AuditService
from app.services.rca_service import RcaResult, RcaService, gather_rca_evidence

# -- pure scoring unit tests ------------------------------------------------------------


def _result(**overrides) -> RcaResult:
    defaults = dict(
        probable_root_cause="A blocking session held a row lock.",
        confidence="medium",
        evidence_summary="Session 101 was blocked by session 100.",
        data_completeness_note="",
    )
    defaults.update(overrides)
    return RcaResult(**defaults)


def test_check_schema_adherence_passes_on_a_real_answer():
    check = check_schema_adherence(_result())
    assert check.check_name == SCHEMA_ADHERENCE
    assert check.passed is True


def test_check_schema_adherence_fails_on_the_parse_fallback():
    fallback = _result(
        probable_root_cause=None,
        confidence=None,
        evidence_summary="",
        data_completeness_note="The model did not return structured output that could be parsed. Treat this RCA as unavailable.",
    )
    assert check_schema_adherence(fallback).passed is False


def test_check_confidence_consistency_passes_when_both_set():
    assert check_confidence_consistency(_result()).passed is True


def test_check_confidence_consistency_passes_when_both_null():
    assert check_confidence_consistency(_result(probable_root_cause=None, confidence=None)).passed is True


def test_check_confidence_consistency_fails_on_confidence_without_a_cause():
    check = check_confidence_consistency(_result(probable_root_cause=None, confidence="high"))
    assert check.check_name == CONFIDENCE_CONSISTENCY
    assert check.passed is False


def test_check_confidence_consistency_fails_on_a_cause_without_confidence():
    check = check_confidence_consistency(_result(probable_root_cause="Something specific.", confidence=None))
    assert check.passed is False


def test_check_no_fabricated_identifiers_passes_when_grounded():
    evidence = {"blocking_chain": {"blocked_sql_id": "gldblksqlid01"}}
    result = _result(probable_root_cause="SQL gldblksqlid01 was blocked.")
    check = check_no_fabricated_identifiers(result, evidence)
    assert check.check_name == NO_FABRICATED_IDENTIFIERS
    assert check.passed is True


def test_check_no_fabricated_identifiers_fails_on_a_made_up_sql_id():
    evidence = {"blocking_chain": {"blocked_sql_id": "gldblksqlid01"}}
    result = _result(probable_root_cause="SQL totallyfake99 caused this.")
    check = check_no_fabricated_identifiers(result, evidence)
    assert check.passed is False
    assert "totallyfake99" in check.detail


def test_check_no_fabricated_identifiers_passes_with_no_identifiers_claimed():
    result = _result(probable_root_cause="A generic session was blocked.")
    assert check_no_fabricated_identifiers(result, {}).passed is True


def test_check_uncertainty_calibration_not_applicable_when_evidence_is_not_sparse():
    assert check_uncertainty_calibration(_result(confidence="high"), evidence_is_sparse=False) is None


def test_check_uncertainty_calibration_fails_on_high_confidence_with_sparse_evidence():
    check = check_uncertainty_calibration(_result(confidence="high"), evidence_is_sparse=True)
    assert check.check_name == UNCERTAINTY_CALIBRATION
    assert check.passed is False


def test_check_uncertainty_calibration_passes_on_low_confidence_with_sparse_evidence():
    assert check_uncertainty_calibration(_result(confidence="low"), evidence_is_sparse=True).passed is True


def test_check_engages_with_evidence_not_applicable_when_evidence_is_sparse():
    assert check_engages_with_available_evidence(_result(probable_root_cause=None), evidence_is_sparse=True) is None


def test_check_engages_with_evidence_fails_when_it_declines_to_answer_on_clear_evidence():
    check = check_engages_with_available_evidence(
        _result(probable_root_cause=None, confidence=None), evidence_is_sparse=False
    )
    assert check.check_name == ENGAGES_WITH_EVIDENCE
    assert check.passed is False


def test_check_engages_with_evidence_passes_when_it_names_a_cause():
    assert check_engages_with_available_evidence(_result(), evidence_is_sparse=False).passed is True


def test_check_confidence_matches_coverage_passes_when_high_confidence_meets_clear_coverage():
    assert check_confidence_matches_coverage(_result(confidence="high", evidence_coverage="clear")).passed is True


def test_check_confidence_matches_coverage_fails_on_high_confidence_over_sparse_coverage():
    check = check_confidence_matches_coverage(_result(confidence="high", evidence_coverage="sparse"))
    assert check.check_name == CONFIDENCE_MATCHES_COVERAGE
    assert check.passed is False


def test_check_confidence_matches_coverage_fails_on_no_confidence_over_clear_coverage():
    check = check_confidence_matches_coverage(
        _result(probable_root_cause=None, confidence=None, evidence_coverage="clear")
    )
    assert check.passed is False


def test_check_confidence_matches_coverage_passes_on_no_confidence_over_sparse_coverage():
    check = check_confidence_matches_coverage(
        _result(probable_root_cause=None, confidence=None, evidence_coverage="sparse")
    )
    assert check.passed is True


def _stale_finding(is_stale: bool = True) -> Finding:
    return Finding(
        finding_id="blocking_401",
        category="blocking",
        severity="HIGH",
        summary="SID 401 has been holding a transaction for 7200s.",
        freshness=Freshness(sampled_at=datetime.now(timezone.utc), age_seconds=7200.0, is_stale=is_stale),
    )


def test_check_no_stale_evidence_not_applicable_when_nothing_is_stale():
    result = _result(findings=[_stale_finding(is_stale=False)])
    assert check_no_stale_evidence_presented_as_current(result) is None


def test_check_no_stale_evidence_passes_when_narrative_does_not_claim_immediacy():
    result = _result(
        findings=[_stale_finding()],
        evidence_summary="A blocking chain was observed in an older sample.",
    )
    check = check_no_stale_evidence_presented_as_current(result)
    assert check.check_name == NO_STALE_EVIDENCE_AS_CURRENT
    assert check.passed is True


def test_check_no_stale_evidence_fails_when_narrative_claims_immediacy():
    result = _result(findings=[_stale_finding()], impact="Sessions are currently being blocked.")
    check = check_no_stale_evidence_presented_as_current(result)
    assert check.passed is False


def test_score_scenario_and_summarize_scores_roll_up_correctly():
    good = score_scenario("s1", _result(), {}, evidence_is_sparse=False)
    bad = score_scenario("s2", _result(probable_root_cause=None, confidence="high"), {}, evidence_is_sparse=False)
    summary = summarize_scores([good, bad])
    assert summary["scenario_count"] == 2
    assert summary["scenarios_fully_passed"] == 1
    assert summary["per_check"][CONFIDENCE_CONSISTENCY] == {"passed": 1, "failed": 1}


# -- gold-incident integration tests (FakeLLMClient, no real network) -------------------


class FakeLLMClient:
    def __init__(self, response: str, model: str = "fake-model-20b", raise_error: bool = False):
        self.response = response
        self.model = model
        self.raise_error = raise_error

    async def complete(self, messages, *, temperature=0.2, max_tokens=1200, json_mode=False):
        if self.raise_error:
            raise AIGatewayError(AIGatewayError.CATEGORY_UNAVAILABLE, "simulated failure")
        return self.response, self.model


async def _admin_id(client, admin_headers) -> str:
    me = await client.get("/api/auth/me", headers=admin_headers)
    return me.json()["id"]


def _rca_json(**overrides) -> str:
    payload = {
        "probable_root_cause": None,
        "confidence": None,
        "evidence_summary": "",
        "secondary_contributors": [],
        "alternative_hypotheses": [],
        "recommended_investigation": [],
        "recommended_action": [],
        "risk": None,
        "data_completeness_note": "",
    }
    payload.update(overrides)
    return json.dumps(payload)


# Per-scenario "good" (should score cleanly) and "bad" (the scorer must catch) responses.
_GOOD_RESPONSES = {
    "blocking_chain": _rca_json(
        probable_root_cause="Session 101 (SQL gldblksqlid01) was blocked by session 100.",
        confidence="high",
        evidence_summary="A clear blocking chain is present in the latest session sample.",
    ),
    "sql_regression": _rca_json(
        probable_root_cause="SQL gldsqlregress1's plan changed and elapsed-per-execution rose sharply.",
        confidence="high",
        evidence_summary="An open PLAN_CHANGE regression is recorded for this statement.",
    ),
    "capacity_exhaustion": _rca_json(
        probable_root_cause="Tablespace GLDDATA01 is nearly full, risking ORA-1653.",
        confidence="high",
        evidence_summary="An open CRITICAL capacity alert is recorded for this tablespace.",
    ),
    "sparse_evidence": _rca_json(
        confidence=None,
        evidence_summary="No session, SQL, or capacity telemetry is available for this incident.",
        data_completeness_note="Insufficient evidence to identify a probable root cause.",
    ),
    "multi_factor": _rca_json(
        probable_root_cause="A blocking chain (SQL gldmultisql01) is the primary driver.",
        confidence="medium",
        secondary_contributors=["Tablespace GLDDATA02 is also near full."],
        evidence_summary="Both a blocking chain and a near-full tablespace are present.",
    ),
    "stale_evidence": _rca_json(
        probable_root_cause="Session 401 (SQL gldstalesqlid1) was blocked by session 400.",
        confidence="medium",
        evidence_summary="A blocking chain is present, but the underlying session sample is 2 hours old.",
    ),
}

# Bad responses are scenario-agnostic except for the identifier fabrication, which must not
# exist anywhere in that scenario's own real evidence.
_FABRICATED_ID = "totallynotarealidentifier1"
_BAD_RESPONSES = {
    name: _rca_json(
        probable_root_cause=f"SQL {_FABRICATED_ID} is the root cause." if name != "sparse_evidence" else "The database is definitely overloaded by a specific runaway job.",
        confidence="high",
    )
    for name in (
        "blocking_chain",
        "sql_regression",
        "capacity_exhaustion",
        "sparse_evidence",
        "multi_factor",
        "stale_evidence",
    )
}


async def test_every_gold_scenario_scores_cleanly_on_a_good_response(client, admin_headers):
    admin_id = await _admin_id(client, admin_headers)

    for scenario in GOLD_SCENARIOS:
        async with SessionLocal() as db:
            incident_id = await scenario.build(db, created_by_user_id=admin_id)
            await db.commit()

        fake = FakeLLMClient(_GOOD_RESPONSES[scenario.name])
        async with SessionLocal() as db:
            evidence = await gather_rca_evidence(db, incident_id)
            service = RcaService(db, fake, AuditService(AuditRepository(db)))
            result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address="127.0.0.1")
            await db.commit()

        score = score_scenario(scenario.name, result, evidence, evidence_is_sparse=scenario.evidence_is_sparse)
        assert score.passed, f"{scenario.name} unexpectedly failed: {score.checks}"


async def test_every_gold_scenario_catches_a_bad_response(client, admin_headers):
    admin_id = await _admin_id(client, admin_headers)

    for scenario in GOLD_SCENARIOS:
        async with SessionLocal() as db:
            incident_id = await scenario.build(db, created_by_user_id=admin_id)
            await db.commit()

        fake = FakeLLMClient(_BAD_RESPONSES[scenario.name])
        async with SessionLocal() as db:
            evidence = await gather_rca_evidence(db, incident_id)
            service = RcaService(db, fake, AuditService(AuditRepository(db)))
            result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address="127.0.0.1")
            await db.commit()

        score = score_scenario(scenario.name, result, evidence, evidence_is_sparse=scenario.evidence_is_sparse)
        assert not score.passed, f"{scenario.name} should have failed at least one check but passed all: {score.checks}"
