"""No real Oracle instance is available in this environment - these tests drive the full
route -> service -> capability_probe path against a fake connector/connection/cursor that
mimics python-oracledb's interface closely enough to exercise the real parsing and
privilege-check logic. Live-instance verification is a documented gap (see docs/RUNBOOK.md)."""

import pytest

from app.main import app
from app.oracle.allowlist import ALLOWLISTED_VIEWS, BLOCKLISTED_VIEWS
from app.oracle.connector import OracleConnectError, get_oracle_connector

DB_PAYLOAD = {
    "name": "CAPTEST01",
    "host": "oracle.internal.ocpcc.dev",
    "port": 1521,
    "service_name": "ORCLPDB1",
    "edition_hint": "SE2",
    "username": "monitoring_ro",
    "password": "SuperSecretOraclePassword!",
}


def _extract_view(statement: str) -> str:
    return statement.upper().split("FROM", 1)[1].split("WHERE", 1)[0].strip()


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_result = None

    def execute(self, statement: str) -> None:
        self.conn.executed.append(statement)
        upper = statement.upper()
        if "V$VERSION" in upper:
            self._last_result = (self.conn.banner,) if self.conn.banner else None
        elif "V$PARAMETER" in upper and "CLUSTER_DATABASE" in upper:
            self._last_result = ("TRUE" if self.conn.is_rac else "FALSE",)
        elif "V$PARAMETER" in upper and "CPU_COUNT" in upper:
            self._last_result = (str(self.conn.cpu_count),) if self.conn.cpu_count is not None else None
        elif "STATS$SNAPSHOT" in upper:
            if not self.conn.statspack:
                raise Exception("ORA-00942: table or view does not exist (PERFSTAT.STATS$SNAPSHOT)")
            self._last_result = (0,)
        else:
            view = _extract_view(statement)
            if view in self.conn.denied_views:
                raise Exception(f"ORA-00942: table or view does not exist ({view})")
            self._last_result = (0,)

    def fetchone(self):
        return self._last_result


class FakeConnection:
    def __init__(self, banner="Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production",
                 is_rac=False, statspack=False, denied_views=None, cpu_count=16):
        self.banner = banner
        self.is_rac = is_rac
        self.statspack = statspack
        self.denied_views = denied_views or set()
        self.cpu_count = cpu_count
        self.executed: list[str] = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def make_fake_connector(**conn_kwargs):
    created: list[FakeConnection] = []

    def _connector(_params):
        conn = FakeConnection(**conn_kwargs)
        created.append(conn)
        return conn

    _connector.created = created
    return _connector


def make_failing_connector(category: str, message: str = "simulated failure"):
    def _connector(_params):
        raise OracleConnectError(category, message)

    return _connector


@pytest.fixture
def use_connector():
    applied = []

    def _apply(connector):
        app.dependency_overrides[get_oracle_connector] = lambda: connector
        applied.append(connector)

    yield _apply
    app.dependency_overrides.pop(get_oracle_connector, None)


async def _register_database(client, admin_headers) -> str:
    resp = await client.post("/api/databases", headers=admin_headers, json=DB_PAYLOAD)
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_test_connection_success(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(make_fake_connector())

    resp = await client.post(f"/api/databases/{db_id}/test-connection", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "category": None, "message": "Connected successfully."}

    audit = await client.get("/api/audit?action_prefix=database.connection_tested", headers=admin_headers)
    assert any(e["target_id"] == db_id and e["success"] is True for e in audit.json())


async def test_test_connection_auth_failure_never_leaks_raw_driver_text(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(
        make_failing_connector(OracleConnectError.CATEGORY_AUTH_FAILED, "ORA-01017: invalid username/password")
    )

    resp = await client.post(f"/api/databases/{db_id}/test-connection", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["category"] == "authentication_failed"
    assert "ORA-01017" not in body["message"]

    audit = await client.get("/api/audit?action_prefix=database.connection_tested", headers=admin_headers)
    events = audit.json()
    assert any(e["target_id"] == db_id and e["success"] is False for e in events)
    assert not any("ORA-01017" in str(e) for e in events)


async def test_detect_capabilities_full_ee_profile(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(
        make_fake_connector(
            banner="Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production",
            is_rac=True,
            statspack=True,
        )
    )

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected_edition"] == "EE"
    assert body["detected_version"] == "19.0.0.0.0"
    assert body["is_rac"] is True
    assert body["statspack_available"] is True
    assert body["privilege_report"] == {view: True for view in ALLOWLISTED_VIEWS}
    assert body["diagnostics_tuning_pack_licensed_confirmed"] is False
    assert body["cpu_count"] == 16

    persisted = await client.get(f"/api/databases/{db_id}/capabilities", headers=admin_headers)
    assert persisted.status_code == 200
    assert persisted.json()["detected_edition"] == "EE"
    assert persisted.json()["cpu_count"] == 16


async def test_detect_capabilities_tolerates_a_missing_cpu_count(client, admin_headers, use_connector):
    # cpu_count is a real, virtually-always-present parameter, but the probe must stay honest
    # (None, not a crash or a fabricated number) if this account can't read it for some reason.
    db_id = await _register_database(client, admin_headers)
    use_connector(make_fake_connector(cpu_count=None))

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["cpu_count"] is None


async def test_detect_capabilities_se2_no_rac_no_statspack(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(
        make_fake_connector(
            banner="Oracle Database 19c Standard Edition 2 Release 19.0.0.0.0 - Production",
            is_rac=False,
            statspack=False,
        )
    )

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    body = resp.json()
    assert body["detected_edition"] == "SE2"
    assert body["is_rac"] is False
    assert body["statspack_available"] is False


async def test_detect_capabilities_reports_missing_privileges(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(make_fake_connector(denied_views={"V$LOCK", "V$UNDOSTAT"}))

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    report = resp.json()["privilege_report"]
    assert report["V$LOCK"] is False
    assert report["V$UNDOSTAT"] is False
    assert report["V$SESSION"] is True

    audit = await client.get("/api/audit?action_prefix=database.capabilities_detected", headers=admin_headers)
    event = next(e for e in audit.json() if e["target_id"] == db_id)
    assert sorted(event["event_metadata"]["missing_views"]) == ["V$LOCK", "V$UNDOSTAT"]


async def test_detect_capabilities_never_queries_blocklisted_views(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    connector = make_fake_connector()
    use_connector(connector)

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    assert resp.status_code == 200

    assert len(connector.created) == 1
    executed_sql = " ".join(connector.created[0].executed).upper()
    for blocked in BLOCKLISTED_VIEWS:
        assert blocked not in executed_sql
    assert connector.created[0].closed is True


async def test_detect_capabilities_unreachable_returns_502(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    use_connector(make_failing_connector(OracleConnectError.CATEGORY_UNREACHABLE, "TNS timeout to 10.0.0.5"))

    resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=admin_headers)
    assert resp.status_code == 502
    assert "10.0.0.5" not in resp.json()["detail"]

    audit = await client.get("/api/audit?action_prefix=database.capabilities_detected", headers=admin_headers)
    assert any(e["target_id"] == db_id and e["success"] is False for e in audit.json())


async def test_capabilities_before_probe_is_an_honest_unknown_not_404(client, admin_headers):
    db_id = await _register_database(client, admin_headers)
    resp = await client.get(f"/api/databases/{db_id}/capabilities", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected_edition"] is None
    assert body["last_probed_at"] is None
    assert body["privilege_report"] is None
    assert body["cpu_count"] is None


async def test_viewer_can_read_capabilities_but_not_probe(client, admin_headers, use_connector):
    db_id = await _register_database(client, admin_headers)
    await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "email": "capviewer@ocpcc.dev",
            "password": "ViewerPassw0rd!123",
            "display_name": "Cap Viewer",
            "roles": ["VIEWER"],
        },
    )
    login = await client.post(
        "/api/auth/login", json={"email": "capviewer@ocpcc.dev", "password": "ViewerPassw0rd!123"}
    )
    viewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    read_resp = await client.get(f"/api/databases/{db_id}/capabilities", headers=viewer_headers)
    assert read_resp.status_code == 200

    use_connector(make_fake_connector())
    probe_resp = await client.post(f"/api/databases/{db_id}/detect-capabilities", headers=viewer_headers)
    assert probe_resp.status_code == 403

    test_resp = await client.post(f"/api/databases/{db_id}/test-connection", headers=viewer_headers)
    assert test_resp.status_code == 403
