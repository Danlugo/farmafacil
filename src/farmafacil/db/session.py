"""Database session management.

This module abstracts the database engine and session factory so swapping
from SQLite to PostgreSQL only requires changing DATABASE_URL in .env.

SQLite (dev):   DATABASE_URL=sqlite+aiosqlite:///farmafacil.db
PostgreSQL:     DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/farmafacil
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from farmafacil.config import DATABASE_URL

# SQLite needs connect_args for async; Postgres does not
_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
    # pool_size / max_overflow only apply to Postgres; SQLite uses StaticPool
    **({"pool_size": 5, "max_overflow": 10} if not _is_sqlite else {}),
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for dependency injection."""
    async with async_session() as session:
        yield session


async def init_db() -> None:
    """Create all tables. Used for development/demo only."""
    from farmafacil.models.database import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()
