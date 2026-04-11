"""Tests for admin user stats page."""

import pytest
from httpx import ASGITransport, AsyncClient

from farmafacil.api.app import app
from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, SearchLog, User


async def _create_test_user(phone: str = "5551234567", name: str = "TestUser") -> User:
    """Insert a test user into the DB and return it."""
    async with async_session() as session:
        user = User(
            phone_number=phone,
            name=name,
            zone_name="Test Zone",
            city_code="CCS",
            display_preference="grid",
            total_tokens_in=1000,
            total_tokens_out=200,
            tokens_in_haiku=1000,
            tokens_out_haiku=200,
            calls_haiku=3,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _add_conversation_logs(phone: str, count: int = 5) -> None:
    """Add inbound conversation log entries for a phone number."""
    async with async_session() as session:
        for i in range(count):
            session.add(ConversationLog(
                phone_number=phone,
                direction="inbound",
                message_text=f"Test message {i}",
                message_type="text",
            ))
        await session.commit()


async def _add_search_logs(user_id: int, total: int = 10, positive: int = 4) -> None:
    """Add search log entries for a user."""
    async with async_session() as session:
        for i in range(total):
            session.add(SearchLog(
                user_id=user_id,
                query=f"test drug {i}",
                results_count=3,
                feedback="yes" if i < positive else ("no" if i < positive + 2 else None),
                source="bot",
            ))
        await session.commit()


class TestAdminUserStatsPage:
    """Test the /admin/user-stats/{user_id} HTML endpoint."""

    @pytest.mark.asyncio
    async def test_returns_html_with_user_stats(self):
        """Stats page returns 200 with user name and key metrics."""
        user = await _create_test_user(phone="5559910001", name="StatsUser")
        await _add_conversation_logs("5559910001", count=8)
        await _add_search_logs(user.id, total=5, positive=3)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        body = response.text
        assert "StatsUser" in body
        assert "Questions" in body
        assert "Searches" in body
        assert "Token Usage" in body

    @pytest.mark.asyncio
    async def test_returns_404_for_missing_user(self):
        """Stats page returns 404 when user does not exist."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/admin/user-stats/99999")

        assert response.status_code == 404
        assert "not found" in response.text.lower()

    @pytest.mark.asyncio
    async def test_shows_cost_estimates(self):
        """Stats page includes estimated cost values."""
        user = await _create_test_user(phone="5559910002", name="CostUser")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert "Estimated Cost" in body
        assert "$" in body

    @pytest.mark.asyncio
    async def test_shows_success_rate(self):
        """Stats page calculates and displays success rate."""
        user = await _create_test_user(phone="5559910003", name="RateUser")
        await _add_search_logs(user.id, total=10, positive=5)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert "Success Rate" in body
        assert "50%" in body  # 5/10 = 50%

    @pytest.mark.asyncio
    async def test_shows_recent_searches(self):
        """Stats page lists recent search queries."""
        user = await _create_test_user(phone="5559910004", name="SearchUser")
        await _add_search_logs(user.id, total=3, positive=1)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert "Recent Searches" in body
        assert "test drug" in body

    @pytest.mark.asyncio
    async def test_json_api_link_present(self):
        """Stats page includes a link to the JSON API endpoint."""
        user = await _create_test_user(phone="5559910005", name="LinkUser")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert f"/api/v1/stats?phone={user.phone_number}" in body
        assert "JSON API" in body

    @pytest.mark.asyncio
    async def test_escapes_user_name_to_prevent_xss(self):
        """User name containing a <script> tag must be HTML-escaped."""
        user = await _create_test_user(
            phone="5559910006", name="<script>alert(1)</script>"
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body

    @pytest.mark.asyncio
    async def test_escapes_search_query_to_prevent_xss(self):
        """A malicious search query must be HTML-escaped in the recent list."""
        user = await _create_test_user(phone="5559910007", name="XssQueryUser")
        async with async_session() as session:
            session.add(
                SearchLog(
                    user_id=user.id,
                    query="<img src=x onerror=alert(1)>",
                    results_count=0,
                    feedback=None,
                    source="bot",
                )
            )
            await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/admin/user-stats/{user.id}")

        body = response.text
        assert "<img src=x onerror=alert(1)>" not in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body
