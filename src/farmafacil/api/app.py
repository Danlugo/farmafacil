"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from farmafacil import __version__
from farmafacil.api.admin import setup_admin
from farmafacil.api.routes import router
from farmafacil.bot.webhook import webhook_router
from farmafacil.config import LOG_LEVEL
from farmafacil.db.seed import seed_ai_roles, seed_intents
from farmafacil.db.session import close_db, engine, init_db
from farmafacil.services.settings import seed_settings
from farmafacil.services.store_backfill import backfill_stores


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown events."""
    await init_db()
    await seed_intents()
    await seed_ai_roles()
    await seed_settings()
    await backfill_stores()
    yield
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="FarmaFacil API",
        description="Venezuela pharmacy drug finder",
        version=__version__,
        lifespan=lifespan,
    )

    app.include_router(router)
    app.include_router(webhook_router)

    # Mount SQLAdmin dashboard at /admin
    setup_admin(app, engine)

    return app


app = create_app()
