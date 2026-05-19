"""Shared test fixtures for FarmaFacil."""

import base64

import pytest

from farmafacil.db.seed import seed_intents
from farmafacil.db.session import init_db

# ── Admin auth helpers for endpoint tests ────────────────────────────────
# These match the env vars set in the test environment.  When no env vars
# are configured (empty ADMIN_USERNAME/ADMIN_PASSWORD), _require_admin in
# routes.py uses secrets.compare_digest which will fail on empty — so we
# patch the config values for tests that need auth.
TEST_ADMIN_USER = "testadmin"
TEST_ADMIN_PASS = "testpass"


def admin_auth_headers() -> dict[str, str]:
    """Return HTTP Basic auth headers for admin-protected endpoints."""
    creds = base64.b64encode(f"{TEST_ADMIN_USER}:{TEST_ADMIN_PASS}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


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
