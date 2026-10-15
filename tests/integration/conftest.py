"""Integration tests against a REAL PostgreSQL + Redis (not the SQLite/fakeredis doubles
used by backend/tests/). Run via docker compose service containers, or point
DATABASE_URL/REDIS_URL at any reachable instance. Skips automatically if neither is
reachable, so a local run without infra just reports "skipped" instead of failing.

Usage (from repo root, with the backend venv active):
    DATABASE_URL=postgresql+asyncpg://ocmd:change_me_dev_only@localhost:5432/ocmd \
    REDIS_URL=redis://localhost:6379/0 \
    PYTHONPATH=backend python -m pytest tests/integration -q
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://ocmd:change_me_dev_only@localhost:5432/ocmd"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ["JWT_SECRET_KEY"] = "integration-test-jwt-secret-not-for-production-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = "admin@integration.dev"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "IntegrationAdmin123!"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _infra_reachable() -> bool:
    async def _check() -> bool:
        try:
            from app.db import db_is_ready
            from app.redis_client import redis_is_ready

            return await db_is_ready() and await redis_is_ready()
        except Exception:
            return False

    return asyncio.run(_check())


@pytest.fixture(scope="session", autouse=True)
def _require_real_infra():
    if not _infra_reachable():
        pytest.skip(
            "Postgres/Redis not reachable at DATABASE_URL/REDIS_URL - "
            "run `docker compose up -d postgres redis` first.",
            allow_module_level=True,
        )


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(_require_real_infra):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
    )


@pytest_asyncio.fixture(autouse=True)
async def _reset_database(_apply_migrations):
    import app.redis_client as redis_client_module
    from app.db import Base, engine
    from app.seed import seed

    # Both app.db.engine and app.redis_client's module-level `_redis` are process-wide
    # singletons (constructed once, at import time), but pytest-asyncio hands each test
    # function a fresh event loop (asyncio_default_fixture_loop_scope = function, in the
    # root pytest.ini). A pooled asyncpg/redis connection is bound to the event loop that
    # created it and cannot be reused from a different one - the previous test's loop
    # closing left both connection pools holding now-dead connections. `close=False` /
    # dropping the reference outright discards them WITHOUT attempting a graceful
    # disconnect - a graceful close needs to send bytes over the wire, which needs the
    # *old*, now-closed loop, so attempting one just trades "stale connection" for a second
    # "Event loop is closed" crash while trying to close the first. Forces a fresh
    # connection under the current loop on next use either way - the documented fix for "an
    # async client + a per-test event loop", not specific to this platform, though Windows'
    # ProactorEventLoop surfaces the naive version of this as an uglier traceback than
    # others do.
    await engine.dispose(close=False)
    redis_client_module._redis = None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await redis_client_module.get_redis().flushdb()
    await seed()
    yield


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://integration-test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_headers(client) -> dict:
    resp = await client.post(
        "/api/auth/login",
        json={"email": "admin@integration.dev", "password": "IntegrationAdmin123!"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
