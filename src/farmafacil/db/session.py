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
    """Create all tables and apply idempotent column migrations.

    `create_all` only creates missing tables — it does NOT add new columns
    to existing tables. For additive column changes we run
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for Postgres, or check
    `PRAGMA table_info` for SQLite, so existing deployments pick up new
    columns on container startup without a manual migration step.
    """
    from sqlalchemy import text

    from farmafacil.models.database import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Additive column migrations (idempotent)
    # Format: (table, column, type_sql)
    additive_migrations: list[tuple[str, str, str]] = [
        ("users", "awaiting_clarification_context", "VARCHAR(300)"),
        ("users", "awaiting_category_search", "VARCHAR(50)"),
    ]

    async with engine.begin() as conn:
        for table, column, type_sql in additive_migrations:
            if _is_sqlite:
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result.fetchall()}
                if column not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {column} {type_sql}")
                    )
            else:
                # Postgres supports IF NOT EXISTS on ADD COLUMN since 9.6
                await conn.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {type_sql}"
                    )
                )

    # One-shot backfill for product_keywords (Item 30, v0.12.6).
    # If the table is empty but products already have keyword JSON rows,
    # populate product_keywords from Product.keywords so find_cross_chain_matches
    # works on existing deployments without requiring a full re-scrape.
    await _backfill_product_keywords()


async def _backfill_product_keywords() -> None:
    """Populate ``product_keywords`` from legacy ``Product.keywords`` JSON.

    Idempotent: no-op if the table already has rows or if no products have
    keyword data. Runs inside a single transaction so a crash mid-backfill
    leaves the table empty for the next startup to retry.
    """
    from sqlalchemy import func as sql_func, select

    from farmafacil.models.database import Product, ProductKeyword

    async with async_session() as session:
        kw_count = await session.scalar(
            select(sql_func.count()).select_from(ProductKeyword)
        )
        if kw_count and kw_count > 0:
            return  # Already backfilled or being written by live traffic

        product_count = await session.scalar(
            select(sql_func.count()).select_from(Product).where(
                Product.keywords.is_not(None)
            )
        )
        if not product_count:
            return  # Fresh DB — nothing to backfill

        result = await session.execute(
            select(Product.id, Product.keywords).where(
                Product.keywords.is_not(None)
            )
        )
        rows_added = 0
        for product_id, keywords in result.all():
            if not keywords:
                continue
            unique = sorted({str(kw).lower() for kw in keywords if kw})
            for kw in unique:
                session.add(
                    ProductKeyword(product_id=product_id, keyword=kw)
                )
                rows_added += 1

        if rows_added:
            await session.commit()


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    await engine.dispose()
