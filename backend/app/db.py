from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings
from app.exceptions import AppError


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_async_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

if engine.dialect.name == "sqlite":
    # SQLite does not enforce foreign keys (including ON DELETE CASCADE) unless told to per
    # connection - Postgres (production) always enforces them, so without this, cascade
    # behavior would silently differ between the SQLite-backed test/local-dev path and
    # production.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """Commits on success AND on an expected AppError (401/403/404/409/...), since those
    represent a completed, auditable business outcome - e.g. a rejected login must still
    persist its failed-attempt counter and audit event. Only an unexpected exception rolls
    the transaction back."""
    async with SessionLocal() as session:
        try:
            yield session
        except AppError:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def db_is_ready() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False
