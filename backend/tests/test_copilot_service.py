"""No real local LLM is available - these tests drive CopilotService against a fake
LLMClient (same test-double pattern as the Oracle connector and email sender)."""

import pytest

from app.ai.gateway import AIGatewayError
from app.db import SessionLocal
from app.exceptions import UpstreamUnavailableError
from app.models.base import utcnow
from app.oracle.telemetry_probe import SessionSampleRow
from app.repositories.audit_repo import AuditRepository
from app.repositories.telemetry_repo import TelemetryRepository
from app.services.audit_service import AuditService
from app.services.copilot_service import CopilotService

DB_PAYLOAD = {
    "name": "COPILOT01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


class FakeLLMClient:
    def __init__(self, response="A canned, grounded answer.", model="fake-model-20b", raise_error=False):
        self.response = response
        self.model = model
        self.raise_error = raise_error
        self.calls: list[list[dict]] = []

    async def complete(self, messages, *, temperature=0.2, max_tokens=1200, json_mode=False):
        self.calls.append(messages)
        if self.raise_error:
            raise AIGatewayError(AIGatewayError.CATEGORY_UNAVAILABLE, "simulated failure")
        return self.response, self.model


async def _register_database(client, admin_headers, *, name: str = "COPILOT01") -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json={**DB_PAYLOAD, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _admin_id(client, admin_headers) -> str:
    me = await client.get("/api/auth/me", headers=admin_headers)
    return me.json()["id"]


async def test_ask_with_no_context_still_calls_the_model_in_character(client, admin_headers):
    """A plain greeting with no database/incident selected must still get a real, in-character
    reply from the model - not a hardcoded canned string that never touches the LLM (the
    previous behavior, which made every "hi" look like a static, robotic non-answer)."""
    fake = FakeLLMClient(response="Hey there! Great to see a friendly face on the console today.")
    admin_id = await _admin_id(client, admin_headers)
    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        result = await service.ask("hi", database_id=None, incident_id=None, actor_user_id=admin_id, ip_address=None)

    assert len(fake.calls) == 1
    system_message, user_message = fake.calls[0]
    assert system_message["role"] == "system"
    assert "hi" in user_message["content"].lower()
    assert result.answer == "Hey there! Great to see a friendly face on the console today."
    assert result.model == "fake-model-20b"
    assert result.tools_used == []


async def test_ask_with_no_context_gateway_failure_raises_upstream_unavailable(client, admin_headers):
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(raise_error=True)
    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        with pytest.raises(UpstreamUnavailableError):
            await service.ask("hi", database_id=None, incident_id=None, actor_user_id=admin_id, ip_address=None)
        await db.commit()


async def test_ask_gathers_database_evidence_and_grounds_the_prompt(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    fake = FakeLLMClient(response="The database looks healthy based on the evidence provided.")
    admin_id = await _admin_id(client, admin_headers)

    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        result = await service.ask(
            "Is PROD01 healthy?", database_id=db_id, incident_id=None, actor_user_id=admin_id, ip_address="127.0.0.1"
        )

    assert result.model == "fake-model-20b"
    assert set(result.tools_used) == {
        "get_database_health",
        "get_active_sessions",
        "get_wait_profile",
        "get_blocking_chain",
        "get_top_sql",
        "get_sql_regressions",
        "get_tablespace_trend",
    }
    assert len(fake.calls) == 1
    system_message, user_message = fake.calls[0]
    assert system_message["role"] == "system"
    assert "only" in system_message["content"].lower()
    assert "COPILOT01" in user_message["content"]
    assert "Is PROD01 healthy?" in user_message["content"]


async def test_ask_derives_database_from_incident_context(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    admin_id = await _admin_id(client, admin_headers)

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
        incident_id = incident.id

    fake = FakeLLMClient()
    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        result = await service.ask(
            "What's going on with this incident?",
            database_id=None,
            incident_id=incident_id,
            actor_user_id=admin_id,
            ip_address=None,
        )

    assert "get_incident" in result.tools_used
    assert "get_database_health" in result.tools_used  # database_id was derived from the incident


async def test_gateway_failure_raises_upstream_unavailable_and_audits_failure(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(raise_error=True)

    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        with pytest.raises(UpstreamUnavailableError):
            await service.ask("Why is it slow?", database_id=db_id, incident_id=None, actor_user_id=admin_id, ip_address=None)
        await db.commit()

    audit_resp = await client.get("/api/audit?action_prefix=ai.copilot_query", headers=admin_headers)
    events = audit_resp.json()
    assert any(e["success"] is False for e in events)


async def test_successful_query_is_audited_with_tool_names_not_full_answer(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(response="A very specific, detailed synthesized answer about session counts.")

    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        await service.ask("Why is it slow?", database_id=db_id, incident_id=None, actor_user_id=admin_id, ip_address=None)
        await db.commit()

    audit_resp = await client.get("/api/audit?action_prefix=ai.copilot_query", headers=admin_headers)
    events = audit_resp.json()
    event = next(e for e in events if e["success"] is True)
    assert "get_database_health" in event["event_metadata"]["tools_used"]
    assert "A very specific" not in str(event)


# -- fleet_scope (Activity tab Phase 4b - "Ask about the whole fleet" toggle) -----------------


async def test_ask_fleet_scope_with_a_quiet_fleet_still_calls_the_model(client, admin_headers):
    await _register_database(client, admin_headers)
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(response="Everything looks quiet across the fleet right now.")

    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        result = await service.ask(
            "Is anything wrong anywhere?",
            database_id=None,
            incident_id=None,
            fleet_scope=True,
            actor_user_id=admin_id,
            ip_address=None,
        )

    assert result.answer == "Everything looks quiet across the fleet right now."
    assert result.findings == []
    _, user_message = fake.calls[0]
    assert "evidence categories were checked across every active database" in user_message["content"]


async def test_ask_fleet_scope_gathers_findings_across_every_database_and_ignores_selected_database(
    client, admin_headers
):
    """A real cross-database finding must show up even though a specific database_id/
    incident_id was also passed alongside fleet_scope=True - the explicit flag always wins,
    never a heuristic blend of the two."""
    db_a = await _register_database(client, admin_headers, name="FLEETSVC_A")
    other_db_id = await _register_database(client, admin_headers, name="FLEETSVC_OTHER")
    admin_id = await _admin_id(client, admin_headers)

    async with SessionLocal() as db:
        rows = [
            SessionSampleRow(100, 1, "BATCH_JOB", None, None, None, "ACTIVE", None, "batch01", "batchproc", None, None),
            SessionSampleRow(
                101, 2, "APP_USER", "copilotblksql1", "enq: TX - row lock contention", "Application",
                "ACTIVE", 100, "app01", "sqlplus", None, None,
            ),
        ]
        await TelemetryRepository(db).insert_session_samples(db_a, utcnow(), rows)
        await db.commit()

    fake = FakeLLMClient(response="One database has a blocking chain worth a look.")
    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        result = await service.ask(
            "What's blocking across the fleet?",
            database_id=other_db_id,
            incident_id=None,
            fleet_scope=True,
            actor_user_id=admin_id,
            ip_address=None,
        )

    assert any(f.category == "blocking" and f.entities["database_id"] == db_a for f in result.findings)
    assert "One database has a blocking chain worth a look." in result.answer
    _, user_message = fake.calls[0]
    assert "across every active database in the fleet" in user_message["content"]
    assert "database_health" not in user_message["content"]  # no raw evidence dump in fleet mode


async def test_ask_fleet_scope_gateway_failure_raises_upstream_unavailable_and_audits_without_a_target(
    client, admin_headers
):
    admin_id = await _admin_id(client, admin_headers)
    fake = FakeLLMClient(raise_error=True)

    async with SessionLocal() as db:
        service = CopilotService(db, fake, AuditService(AuditRepository(db)))
        with pytest.raises(UpstreamUnavailableError):
            await service.ask(
                "Anything on fire?", database_id=None, incident_id=None, fleet_scope=True,
                actor_user_id=admin_id, ip_address=None,
            )
        await db.commit()

    audit_resp = await client.get("/api/audit?action_prefix=ai.copilot_query", headers=admin_headers)
    events = audit_resp.json()
    event = next(e for e in events if e["success"] is False and e["event_metadata"].get("fleet_scope") is True)
    assert event["target_type"] is None
    assert event["target_id"] is None
