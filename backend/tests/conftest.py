import os
import tempfile
import uuid

# Must happen before any `app.*` import: app.db builds its engine from get_settings() at
# import time. Point tests at an isolated SQLite file so they never touch a real database.
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"ocpcc_test_{uuid.uuid4().hex}.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH}"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["JWT_SECRET_KEY"] = "test-only-jwt-secret-not-for-production-use-0123456789"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_BOOTSTRAP_EMAIL"] = "admin@bootstrap.dev"
os.environ["ADMIN_BOOTSTRAP_PASSWORD"] = "AdminBootstrap123!"
os.environ["COLLECTOR_ENABLED"] = "false"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

import app.redis_client as redis_client_module  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.redis_client import get_redis  # noqa: E402
from app.seed import seed  # noqa: E402

_fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)


async def _override_get_redis():
    return _fake_redis


app.dependency_overrides[get_redis] = _override_get_redis
# app.dependency_overrides only intercepts get_redis when it's resolved via FastAPI's
# Depends() inside a route handler. app/security/middleware.py's RateLimitMiddleware calls
# get_redis() directly as a plain function (ASGI middleware has no dependency injection), so
# without this it would try to open a real TCP connection to redis://localhost:6379/15 on
# every single request and only recover once that connection attempt times out and the
# middleware's own fail-open handling kicks in - correct, but tens of seconds slower per
# request. Pre-seeding the module-level singleton makes every get_redis() call, direct or
# via Depends, return the fake instance immediately.
redis_client_module._redis = _fake_redis


@pytest_asyncio.fixture(autouse=True)
async def _reset_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await _fake_redis.flushall()
    await seed()
    yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_token(client) -> str:
    resp = await client.post(
        "/api/auth/login", json={"email": "admin@bootstrap.dev", "password": "AdminBootstrap123!"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def admin_headers(admin_token) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}
