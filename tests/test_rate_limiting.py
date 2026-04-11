"""Tests for API input validation and rate limiting (Item 23)."""

import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import create_app
from farmafacil.api.limiter import limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Clear slowapi's in-memory counters between tests."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Input validation ────────────────────────────────────────────────────


class TestGetSearchValidation:
    async def test_missing_query_returns_422(self, client):
        r = await client.get("/api/v1/search")
        assert r.status_code == 422

    async def test_query_too_short_returns_422(self, client):
        r = await client.get("/api/v1/search?q=a")
        assert r.status_code == 422

    async def test_query_empty_returns_422(self, client):
        r = await client.get("/api/v1/search?q=")
        assert r.status_code == 422

    async def test_query_too_long_returns_422(self, client):
        long_q = "x" * 201
        r = await client.get(f"/api/v1/search?q={long_q}")
        assert r.status_code == 422

    async def test_city_too_long_returns_422(self, client):
        long_city = "y" * 51
        r = await client.get(f"/api/v1/search?q=losartan&city={long_city}")
        assert r.status_code == 422

    async def test_valid_query_returns_200(self, client):
        r = await client.get("/api/v1/search?q=losartan")
        assert r.status_code == 200


class TestPostSearchValidation:
    async def test_query_too_long_returns_422(self, client):
        r = await client.post(
            "/api/v1/search", json={"query": "x" * 201}
        )
        assert r.status_code == 422

    async def test_city_too_long_returns_422(self, client):
        r = await client.post(
            "/api/v1/search",
            json={"query": "losartan", "city": "z" * 51},
        )
        assert r.status_code == 422


class TestIntentValidation:
    async def test_empty_action_returns_422(self, client):
        r = await client.post(
            "/api/v1/intents",
            json={"action": "", "keyword": "hola"},
        )
        assert r.status_code == 422

    async def test_action_too_long_returns_422(self, client):
        r = await client.post(
            "/api/v1/intents",
            json={"action": "a" * 51, "keyword": "hola"},
        )
        assert r.status_code == 422

    async def test_keyword_too_long_returns_422(self, client):
        r = await client.post(
            "/api/v1/intents",
            json={"action": "greeting", "keyword": "k" * 101},
        )
        assert r.status_code == 422


# ── Rate limiting ────────────────────────────────────────────────────────


class TestRateLimiting:
    async def test_search_get_limits_to_30_per_minute(self, client):
        """The 31st GET /api/v1/search within a minute returns 429."""
        last = None
        for _ in range(31):
            last = await client.get("/api/v1/search?q=losartan")
        assert last is not None
        assert last.status_code == 429

    async def test_search_post_limits_to_30_per_minute(self, client):
        """The 31st POST /api/v1/search within a minute returns 429."""
        last = None
        for _ in range(31):
            last = await client.post(
                "/api/v1/search", json={"query": "losartan"}
            )
        assert last is not None
        assert last.status_code == 429

    async def test_search_get_under_limit_all_200(self, client):
        """30 rapid calls should all succeed."""
        for _ in range(30):
            r = await client.get("/api/v1/search?q=losartan")
            assert r.status_code == 200

    async def test_stats_limits_to_60_per_minute(self, client):
        """The 61st GET /api/v1/stats within a minute returns 429."""
        last = None
        for _ in range(61):
            last = await client.get("/api/v1/stats")
        assert last is not None
        assert last.status_code == 429

    async def test_health_is_exempt_from_rate_limit(self, client):
        """Health check can be polled aggressively by monitoring."""
        for _ in range(100):
            r = await client.get("/health")
            assert r.status_code == 200

    async def test_webhook_get_is_exempt(self, client):
        """Meta webhook verification must never be rate limited."""
        for _ in range(100):
            r = await client.get(
                "/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong",
                    "hub.challenge": "xyz",
                },
            )
            # 403 because token is wrong, but NOT 429
            assert r.status_code != 429

    async def test_rate_limit_resets_between_tests(self, client):
        """After the autouse fixture resets, a fresh client gets a full quota."""
        r = await client.get("/api/v1/search?q=losartan")
        assert r.status_code == 200
