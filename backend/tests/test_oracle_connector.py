"""app.oracle.connector.connect()'s ORA-/DPY-code-to-category mapping had zero test coverage
before this - found via real testing against a live Oracle instance (Enterprise Readiness
Plan A.4), where a real "connection refused" (no listener on the target port) surfaced as
`full_code="DPY-6005"`, `code=0` - not in _UNREACHABLE_CODES - and silently fell through to
`unknown_error`. These tests pin the mapping so a regression is caught without needing a
real instance."""

import pytest

from app.oracle.connector import OracleConnectionParams, OracleConnectError, connect

PARAMS = OracleConnectionParams(
    host="oracle.internal.ocpcc.dev",
    port=1521,
    service_name="ORCLPDB1",
    username="monitoring_ro",
    password="SuperSecretOraclePassword!",
)


class FakeOracleError:
    def __init__(self, code: int, full_code: str):
        self.code = code
        self.full_code = full_code


def _raise(monkeypatch, code: int, full_code: str, message: str):
    import oracledb

    def _connect(**kwargs):
        raise oracledb.DatabaseError(FakeOracleError(code, full_code)) from None

    monkeypatch.setattr(oracledb, "connect", _connect)


def test_auth_failure_code_maps_to_authentication_failed(monkeypatch):
    _raise(monkeypatch, 1017, "ORA-01017", "invalid username/password")
    with pytest.raises(OracleConnectError) as exc_info:
        connect(PARAMS)
    assert exc_info.value.category == OracleConnectError.CATEGORY_AUTH_FAILED


def test_ora_unreachable_code_maps_to_unreachable(monkeypatch):
    _raise(monkeypatch, 12541, "ORA-12541", "TNS:no listener")
    with pytest.raises(OracleConnectError) as exc_info:
        connect(PARAMS)
    assert exc_info.value.category == OracleConnectError.CATEGORY_UNREACHABLE


def test_dpy_driver_connect_refused_maps_to_unreachable(monkeypatch):
    """Regression test for the real bug found in A.4: a raw TCP-level failure (connection
    refused, timeout, DNS failure) never reaches an Oracle listener, so python-oracledb
    raises a DPY-* error with code=0 instead of an ORA- code."""
    _raise(monkeypatch, 0, "DPY-6005", "cannot connect to database")
    with pytest.raises(OracleConnectError) as exc_info:
        connect(PARAMS)
    assert exc_info.value.category == OracleConnectError.CATEGORY_UNREACHABLE


def test_unmapped_ora_code_maps_to_unknown(monkeypatch):
    _raise(monkeypatch, 942, "ORA-00942", "table or view does not exist")
    with pytest.raises(OracleConnectError) as exc_info:
        connect(PARAMS)
    assert exc_info.value.category == OracleConnectError.CATEGORY_UNKNOWN
