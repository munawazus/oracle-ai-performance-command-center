import json

import pytest

from app.ai.gateway import AIGatewayError
from app.db import SessionLocal
from app.exceptions import NotFoundError, UpstreamUnavailableError
from app.repositories.audit_repo import AuditRepository
from app.services.audit_service import AuditService
from app.services.rca_service import RcaService, parse_rca_narrative_response, parse_rca_response

# Call 1's shape - the proven 9-field core (docs/RCA_BENCHMARK_REPORT.md: 5/5 schema adherence).
CORE_ONLY_JSON = json.dumps(
    {
        "probable_root_cause": "A surge in active sessions correlates with the anomaly.",
        "confidence": "medium",
        "evidence_summary": "Active session count spiked well above its rolling baseline.",
        "secondary_contributors": ["Elevated User I/O wait"],
        "alternative_hypotheses": ["A scheduled batch job"],
        "recommended_investigation": ["Check for a new SQL_ID dominating sessions"],
        "recommended_action": ["Review recent application deploys"],
        "risk": "Ongoing degraded response time for end users.",
        "data_completeness_note": "SQL-level attribution is unavailable in this deployment.",
    }
)

# Call 2's shape - the smaller enterprise-report follow-up.
NARRATIVE_ONLY_JSON = json.dumps(
    {
        "executive_summary": "An anomalous surge in active sessions was detected and investigated.",
        "impact": "End users experienced degraded response time during the surge.",
        "resolution": None,
        "why_resolution_works": None,
        "corrective_preventive_actions": ["Add a baseline alert for this metric"],
        "limitations": "SQL-level attribution is unavailable in this deployment.",
    }
)

# A single blob satisfying BOTH shapes at once - convenient for tests that don't care about
# distinguishing which call produced which field, only that the whole flow works end to end.
VALID_RCA_JSON = json.dumps({**json.loads(CORE_ONLY_JSON), **json.loads(NARRATIVE_ONLY_JSON)})


class FakeLLMClient:
    """Returns the same canned response for every call - fine for tests that don't care
    which of RcaService's two calls (core investigation vs. narrative expansion) is which,
    since VALID_RCA_JSON above satisfies both shapes at once."""

    def __init__(self, response: str, model: str = "fake-model-20b", raise_error: bool = False):
        self.response = response
        self.model = model
        self.raise_error = raise_error
        self.calls: list[list[dict]] = []
        self.json_mode_calls: list[bool] = []

    async def complete(self, messages, *, temperature=0.2, max_tokens=1200, json_mode=False):
        self.calls.append(messages)
        self.json_mode_calls.append(json_mode)
        if self.raise_error:
            raise AIGatewayError(AIGatewayError.CATEGORY_UNAVAILABLE, "simulated failure")
        return self.response, self.model


class SequencedFakeLLMClient:
    """Returns a different outcome per call, in order - for tests that need to distinguish
    Call 1 (the core investigation) from Call 2 (the narrative expansion), e.g. to prove a
    Call 2 failure never loses Call 1's already-real result."""

    def __init__(self, outcomes: list):
        self.outcomes = outcomes
        self.model = "fake-model-20b"
        self.calls: list[list[dict]] = []
        self.json_mode_calls: list[bool] = []

    async def complete(self, messages, *, temperature=0.2, max_tokens=1200, json_mode=False):
        self.calls.append(messages)
        self.json_mode_calls.append(json_mode)
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # (content, model) tuple, already in the right shape


DB_PAYLOAD = {
    "name": "RCA01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
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


async def _admin_id(client, admin_headers) -> str:
    me = await client.get("/api/auth/me", headers=admin_headers)
    return me.json()["id"]


# -- parse_rca_response (Call 1) ----------------------------------------------------------


def test_parse_rca_response_handles_valid_json():
    parsed = parse_rca_response(CORE_ONLY_JSON)
    assert parsed["confidence"] == "medium"
    assert parsed["probable_root_cause"].startswith("A surge")
    assert parsed["secondary_contributors"] == ["Elevated User I/O wait"]
    assert parsed["contributing_factors"] == ["Elevated User I/O wait"]  # same values, new name


def test_parse_rca_response_strips_markdown_fences():
    fenced = f"```json\n{CORE_ONLY_JSON}\n```"
    parsed = parse_rca_response(fenced)
    assert parsed["confidence"] == "medium"


def test_parse_rca_response_rejects_invalid_confidence_value():
    payload = json.loads(CORE_ONLY_JSON)
    payload["confidence"] = "extremely certain"  # not one of low/medium/high
    parsed = parse_rca_response(json.dumps(payload))
    assert parsed["confidence"] is None


def test_parse_rca_response_never_crashes_on_garbage():
    parsed = parse_rca_response("I cannot comply with that request.")
    assert parsed["probable_root_cause"] is None
    assert parsed["confidence"] is None
    assert "could be parsed" in parsed["data_completeness_note"]


def test_parse_rca_response_coerces_non_list_fields_to_empty_list():
    payload = json.loads(CORE_ONLY_JSON)
    payload["secondary_contributors"] = "not a list"
    parsed = parse_rca_response(json.dumps(payload))
    assert parsed["secondary_contributors"] == []
    assert parsed["contributing_factors"] == []


# -- parse_rca_narrative_response (Call 2) -------------------------------------------------


def test_parse_rca_narrative_response_handles_valid_json():
    parsed = parse_rca_narrative_response(NARRATIVE_ONLY_JSON)
    assert parsed["executive_summary"].startswith("An anomalous surge")
    assert parsed["corrective_preventive_actions"] == ["Add a baseline alert for this metric"]


def test_parse_rca_narrative_response_never_crashes_on_garbage():
    parsed = parse_rca_narrative_response("Sorry, I can't help with that.")
    assert parsed["executive_summary"] == ""
    assert parsed["resolution"] is None
    assert parsed["corrective_preventive_actions"] == []


def test_parse_rca_narrative_response_coerces_non_list_fields_to_empty_list():
    payload = json.loads(NARRATIVE_ONLY_JSON)
    payload["corrective_preventive_actions"] = "not a list"
    parsed = parse_rca_narrative_response(json.dumps(payload))
    assert parsed["corrective_preventive_actions"] == []


# -- RcaService.investigate() --------------------------------------------------------------


async def test_rca_investigate_returns_grounded_result(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(VALID_RCA_JSON)

    async with SessionLocal() as db:
        service = RcaService(db, fake, AuditService(AuditRepository(db)))
        result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address="127.0.0.1")
        await db.commit()

    assert result.confidence == "medium"
    assert result.model == "fake-model-20b"
    assert result.executive_summary.startswith("An anomalous surge")  # Call 2's field, populated
    # Two real calls - the core investigation, then the seeded narrative expansion.
    assert len(fake.calls) == 2
    core_system, core_user = fake.calls[0]
    assert "JSON" in core_system["content"]
    assert "RCA01" in core_user["content"]
    narrative_system, narrative_user = fake.calls[1]
    assert "JSON" in narrative_system["content"]
    assert "A surge in active sessions" in narrative_user["content"]  # seeded with Call 1's root cause
    # A real local model was found (A.6) not to reliably follow a prompt-only "respond with
    # exactly this JSON shape" instruction - RCA must request response_format=json_object.
    assert fake.json_mode_calls == [True, True]


async def test_rca_investigate_keeps_call_ones_result_when_call_two_fails(client, admin_headers):
    """The exact guarantee the two-call split exists for: a Call 2 (narrative) failure must
    never lose Call 1's real, already-proven investigative result."""
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)
    admin_id = await _admin_id(client, admin_headers)
    fake = SequencedFakeLLMClient(
        [
            (CORE_ONLY_JSON, "fake-model-20b"),
            AIGatewayError(AIGatewayError.CATEGORY_UNAVAILABLE, "simulated failure"),
        ]
    )

    async with SessionLocal() as db:
        service = RcaService(db, fake, AuditService(AuditRepository(db)))
        result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address=None)
        await db.commit()

    assert result.probable_root_cause is not None
    assert result.probable_root_cause.startswith("A surge")
    assert result.confidence == "medium"
    assert result.executive_summary == ""  # Call 2 failed - honest empty default, not a crash
    assert result.resolution is None


async def test_rca_investigate_missing_incident_404s(client, admin_headers):
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(VALID_RCA_JSON)
    async with SessionLocal() as db:
        service = RcaService(db, fake, AuditService(AuditRepository(db)))
        with pytest.raises(NotFoundError):
            await service.investigate("does-not-exist", actor_user_id=admin_id, ip_address=None)


async def test_rca_investigate_gateway_failure_is_never_silently_swallowed(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(response="", raise_error=True)

    async with SessionLocal() as db:
        service = RcaService(db, fake, AuditService(AuditRepository(db)))
        with pytest.raises(UpstreamUnavailableError):
            await service.investigate(incident_id, actor_user_id=admin_id, ip_address=None)
        await db.commit()

    audit_resp = await client.get("/api/audit?action_prefix=ai.rca_requested", headers=admin_headers)
    events = audit_resp.json()
    assert any(e["success"] is False for e in events)


async def test_rca_investigate_unparseable_model_output_is_honest_not_fabricated(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    incident_id = await _open_incident(db_id)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient("Sorry, I can't help with that.")

    async with SessionLocal() as db:
        service = RcaService(db, fake, AuditService(AuditRepository(db)))
        result = await service.investigate(incident_id, actor_user_id=admin_id, ip_address=None)

    assert result.probable_root_cause is None
    assert result.confidence is None
    assert "could be parsed" in result.data_completeness_note
    assert result.executive_summary == ""
