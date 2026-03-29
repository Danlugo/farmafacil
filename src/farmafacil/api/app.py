"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from farmafacil import __version__
from farmafacil.api.routes import router
from farmafacil.bot.webhook import webhook_router
from farmafacil.config import LOG_LEVEL
from farmafacil.db.session import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown events."""
    await init_db()
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
    return app


app = create_app()
