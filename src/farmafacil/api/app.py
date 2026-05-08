"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from farmafacil import __version__
from farmafacil.api.admin import setup_admin
from farmafacil.api.limiter import limiter
from farmafacil.api.routes import router
from farmafacil.bot.webhook import webhook_router
from farmafacil.config import LOG_LEVEL
from farmafacil.db.seed import seed_ai_roles, seed_intents, sync_seeded_roles
from farmafacil.db.session import close_db, engine, init_db
from farmafacil.services.settings import seed_settings
from farmafacil.services.scheduler import scheduler_loop, seed_scheduled_tasks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown events."""
    import asyncio

    await init_db()
    await seed_intents()
    await seed_ai_roles()
    await sync_seeded_roles()  # Item 37: sync prompt/rules/skills from seed
    await seed_settings()
    await seed_scheduled_tasks()

    # Start background scheduler (runs maintenance tasks on intervals)
    scheduler_task = asyncio.create_task(scheduler_loop())

    yield

    scheduler_task.cancel()
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

    # Rate limiting (per-IP, in-memory) — see farmafacil.api.limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.include_router(router)
    app.include_router(webhook_router)

    # Mount SQLAdmin dashboard at /admin
    setup_admin(app, engine)

    return app


app = create_app()
