"""Tests for the FastAPI endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import create_app


@pytest.fixture
def app():
    """Create a fresh app instance for testing."""
    return create_app()


@pytest.fixture
async def client(app):
    """Async HTTP client for testing the API."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_health_check(client):
    """Health endpoint returns 200 with version."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


async def test_search_get(client):
    """GET search endpoint accepts query parameter."""
    response = await client.get("/api/v1/search?q=losartan")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "losartan"
    assert "results" in data
    assert "searched_pharmacies" in data


async def test_search_post(client):
    """POST search endpoint accepts JSON body."""
    response = await client.post("/api/v1/search", json={"query": "losartan"})
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "losartan"


async def test_search_post_validation(client):
    """POST search rejects query shorter than 2 chars."""
    response = await client.post("/api/v1/search", json={"query": "a"})
    assert response.status_code == 422
