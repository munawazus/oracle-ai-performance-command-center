async def _viewer_headers(client, admin_headers, email="notifyviewer@ocpcc.dev"):
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"email": email, "password": "ViewerPassw0rd!123", "display_name": "Viewer", "roles": ["VIEWER"]},
    )
    login = await client.post("/api/auth/login", json={"email": email, "password": "ViewerPassw0rd!123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_get_default_settings_has_no_smtp_password(client, admin_headers):
    resp = await client.get("/api/notifications/settings", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_smtp_password"] is False
    assert "smtp_password" not in body


async def test_update_settings_never_returns_password_and_sets_has_password_flag(client, admin_headers):
    resp = await client.put(
        "/api/notifications/settings",
        headers=admin_headers,
        json={
            "smtp_host": "smtp.internal.ocpcc.dev",
            "smtp_port": 587,
            "smtp_use_tls": True,
            "sender_email": "alerts@ocpcc.dev",
            "sender_name": "OCPCC Alerts",
            "smtp_password": "SuperSecretSmtpPassword!",
            "default_recipients": ["oncall@ocpcc.dev"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_smtp_password"] is True
    assert "SuperSecretSmtpPassword" not in str(body)
    assert body["default_recipients"] == ["oncall@ocpcc.dev"]

    audit = await client.get("/api/audit?action_prefix=notification.settings_updated", headers=admin_headers)
    events = audit.json()
    assert len(events) >= 1
    assert "SuperSecretSmtpPassword" not in str(events)


async def test_settings_require_notification_configure_permission(client, admin_headers):
    viewer_headers = await _viewer_headers(client, admin_headers)
    resp = await client.get("/api/notifications/settings", headers=viewer_headers)
    assert resp.status_code == 403


async def test_events_list_requires_incident_view(client):
    resp = await client.get("/api/notifications/events")
    assert resp.status_code == 401


async def test_events_list_returns_empty_when_nothing_queued(client, admin_headers):
    resp = await client.get("/api/notifications/events", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_invalid_recipient_email_is_rejected(client, admin_headers):
    resp = await client.put(
        "/api/notifications/settings", headers=admin_headers, json={"default_recipients": ["not-an-email"]}
    )
    assert resp.status_code == 422
