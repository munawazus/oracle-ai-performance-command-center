async def test_health_liveness(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readiness_reports_dependencies(client):
    resp = await client.get("/api/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["dependencies"] == {"postgres": "ok", "redis": "ok"}
