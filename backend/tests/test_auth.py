ADMIN_EMAIL = "admin@bootstrap.dev"
ADMIN_PASSWORD = "AdminBootstrap123!"


async def test_login_success_returns_access_token(client):
    resp = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "refresh_token" in resp.cookies


async def test_login_wrong_password_generic_error(client):
    resp = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


async def test_login_unknown_email_same_generic_error(client):
    resp = await client.post(
        "/api/auth/login", json={"email": "nobody@ocpcc.dev", "password": "whatever12345"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password."


async def test_repeated_failures_lock_account(client):
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
        assert resp.status_code == 401

    # Even the correct password is now rejected while locked.
    resp = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert resp.status_code == 401
    assert "locked" in resp.json()["detail"].lower()


async def test_me_requires_authentication(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_me_returns_roles_and_permissions(client):
    login = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    token = login.json()["access_token"]
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == ADMIN_EMAIL
    assert "ADMIN" in body["roles"]
    assert "database:view" in body["permissions"]
    assert "admin:users" in body["permissions"]


async def test_refresh_issues_new_access_token(client):
    login = await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    first_token = login.json()["access_token"]

    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200
    second_token = refresh.json()["access_token"]
    assert second_token != first_token


async def test_refresh_without_cookie_fails(client):
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_logout_revokes_session(client):
    await client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 200

    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 401
