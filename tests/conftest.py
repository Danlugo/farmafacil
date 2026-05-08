"""Shared test fixtures for FarmaFacil."""

import pytest

from farmafacil.db.seed import seed_intents
from farmafacil.db.session import init_db


@pytest.fixture(autouse=True, scope="session")
def _init_test_db(event_loop_policy):
    """Initialize test database and seed data once per session."""
    import asyncio

    async def setup():
        await init_db()
        await seed_intents()

    asyncio.get_event_loop_policy()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(setup())
    loop.close()


@pytest.fixture
def sample_drug_name() -> str:
    """Common drug name for testing searches."""
    return "losartan"
