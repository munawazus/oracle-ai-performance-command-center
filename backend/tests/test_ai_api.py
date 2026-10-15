import pytest

from app.ai.gateway import get_copilot_llm_client, get_llm_client
from app.main import app
from tests.test_copilot_service import FakeLLMClient

DB_PAYLOAD = {
    "name": "AIAPI01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


@pytest.fixture
def use_llm_client():
    """RcaService's model only - /api/incidents/{id}/rca (app.ai.gateway.get_llm_client)."""

    def _apply(client_instance):
        app.dependency_overrides[get_llm_client] = lambda: client_instance

    yield _apply
    app.dependency_overrides.pop(get_llm_client, None)


@pytest.fixture
def use_copilot_llm_client():
    """CopilotService's model only - /api/ai/ask (app.ai.gateway.get_copilot_llm_client),
    a separate, smaller/faster model from RCA's."""

    def _apply(client_instance):
        app.dependency_overrides[get_copilot_llm_client] = lambda: client_instance

    yield _apply
    app.dependency_overrides.pop(get_copilot_llm_client, None)


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_ask_without_ai_configured_returns_502(client, admin_headers, use_copilot_llm_client):
    use_copilot_llm_client(None)
    resp = await client.post("/api/ai/ask", headers=admin_headers, json={"question": "How's PROD01?"})
    assert resp.status_code == 502
    assert "not enabled" in resp.json()["detail"].lower()


async def test_ask_with_fake_model_returns_grounded_answer(client, admin_headers, use_copilot_llm_client):
    db_id = await _register_database(client, admin_headers)
    use_copilot_llm_client(FakeLLMClient(response="Sessions look normal based on the evidence."))

    resp = await client.post(
        "/api/ai/ask", headers=admin_headers, json={"question": "How's it doing?", "database_id": db_id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Sessions look normal based on the evidence."
    assert body["model"] == "fake-model-20b"
    assert "get_database_health" in body["tools_used"]


async def test_ask_requires_ai_query_permission(client, admin_headers, use_copilot_llm_client):
    use_copilot_llm_client(FakeLLMClient())
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "auditor-noai@ocpcc.dev",
            "password": "AuditorPassw0rd!123",
            "display_name": "Auditor",
            "roles": ["AUDITOR"],
        },
    )
    login = await client.post("/api/auth/login", json={"email": "auditor-noai@ocpcc.dev", "password": "AuditorPassw0rd!123"})
    auditor_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await client.post("/api/ai/ask", headers=auditor_headers, json={"question": "Anything wrong?"})
    assert resp.status_code == 403


async def test_ask_rejects_overlong_question(client, admin_headers, use_copilot_llm_client):
    use_copilot_llm_client(FakeLLMClient())
    resp = await client.post("/api/ai/ask", headers=admin_headers, json={"question": "x" * 3000})
    assert resp.status_code == 422


async def test_rca_endpoint_without_ai_configured_returns_502(client, admin_headers, use_llm_client):
    use_llm_client(None)
    resp = await client.post("/api/incidents/does-not-exist/rca", headers=admin_headers)
    assert resp.status_code == 502


async def test_unauthenticated_ask_is_rejected(client):
    resp = await client.post("/api/ai/ask", json={"question": "hi"})
    assert resp.status_code == 401


async def test_ask_with_fleet_scope_ignores_database_id_and_answers_fleet_wide(
    client, admin_headers, use_copilot_llm_client
):
    """The explicit fleet_scope flag reaches CopilotService through the real route (including
    the Redis dependency) and takes priority over a database_id also present on the request."""
    db_id = await _register_database(client, admin_headers)
    use_copilot_llm_client(FakeLLMClient(response="Fleet-wide, everything looks calm right now."))

    resp = await client.post(
        "/api/ai/ask",
        headers=admin_headers,
        json={"question": "Is anything wrong anywhere?", "database_id": db_id, "fleet_scope": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Fleet-wide, everything looks calm right now."
