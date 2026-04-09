"""Tests for persistent usage stats — token tracking and stats API."""

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.users import increment_token_usage


class TestIncrementTokenUsage:
    """Test atomic token counter increments."""

    @pytest.mark.asyncio
    async def test_increments_from_zero(self):
        """New user starts at 0 tokens, increment adds correctly."""
        async with async_session() as session:
            user = User(phone_number="5550001111", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 100, 50)

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one()
            assert user.total_tokens_in == 100
            assert user.total_tokens_out == 50

    @pytest.mark.asyncio
    async def test_accumulates_across_calls(self):
        """Multiple increments accumulate correctly."""
        async with async_session() as session:
            user = User(phone_number="5550002222", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 100, 50)
        await increment_token_usage(user_id, 200, 80)
        await increment_token_usage(user_id, 50, 20)

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one()
            assert user.total_tokens_in == 350
            assert user.total_tokens_out == 150

    @pytest.mark.asyncio
    async def test_skips_zero_tokens(self):
        """No DB call when both tokens are zero."""
        async with async_session() as session:
            user = User(phone_number="5550003333", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        # Should be a no-op
        await increment_token_usage(user_id, 0, 0)

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one()
            assert user.total_tokens_in == 0
            assert user.total_tokens_out == 0


class TestIntentTokenPropagation:
    """Test that Intent dataclass carries token counts."""

    def test_intent_has_token_fields(self):
        from farmafacil.services.intent import Intent

        intent = Intent(action="drug_search", input_tokens=142, output_tokens=87)
        assert intent.input_tokens == 142
        assert intent.output_tokens == 87

    def test_intent_defaults_zero_tokens(self):
        from farmafacil.services.intent import Intent

        intent = Intent(action="greeting")
        assert intent.input_tokens == 0
        assert intent.output_tokens == 0


class TestGetUserStatsWithTokens:
    """Test that get_user_stats returns cumulative token totals."""

    @pytest.mark.asyncio
    async def test_includes_token_totals(self):
        from farmafacil.services.chat_debug import get_user_stats

        async with async_session() as session:
            user = User(phone_number="5550004444", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 500, 200)

        stats = await get_user_stats("5550004444", user_id)
        assert stats["total_tokens_in"] == 500
        assert stats["total_tokens_out"] == 200

    @pytest.mark.asyncio
    async def test_zero_tokens_for_new_user(self):
        from farmafacil.services.chat_debug import get_user_stats

        async with async_session() as session:
            user = User(phone_number="5550005555", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        stats = await get_user_stats("5550005555", user_id)
        assert stats["total_tokens_in"] == 0
        assert stats["total_tokens_out"] == 0


class TestBuildDebugFooterWithTotals:
    """Test debug footer includes cumulative token line."""

    def test_footer_includes_total_tokens(self):
        from farmafacil.services.chat_debug import build_debug_footer

        footer = build_debug_footer(
            role_used="pharmacy_advisor",
            input_tokens=100, output_tokens=50,
            total_questions=10, total_success=3,
            total_tokens_in=1500, total_tokens_out=600,
        )
        assert "total tokens: _1500 in / 600 out_" in footer

    def test_footer_shows_both_per_call_and_total(self):
        from farmafacil.services.chat_debug import build_debug_footer

        footer = build_debug_footer(
            role_used="test", input_tokens=100, output_tokens=50,
            total_questions=5, total_success=2,
            total_tokens_in=1000, total_tokens_out=400,
        )
        assert "tokens: _100 in / 50 out_" in footer
        assert "total tokens: _1000 in / 400 out_" in footer


class TestStatsEndpoint:
    """Test GET /api/v1/stats endpoint."""

    @pytest.mark.asyncio
    async def test_global_stats_returns_totals(self):
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "total_questions" in data
        assert "total_success" in data
        assert "total_tokens_in" in data
        assert "total_tokens_out" in data

    @pytest.mark.asyncio
    async def test_per_user_stats_not_found(self):
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stats?phone=9999999999")
        assert resp.status_code == 200
        assert resp.json() == {"error": "user not found"}

    @pytest.mark.asyncio
    async def test_per_user_stats_returns_data(self):
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        async with async_session() as session:
            user = User(phone_number="5550006666", onboarding_step="welcome")
            session.add(user)
            await session.commit()

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stats?phone=5550006666")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "5550006666"
        assert "total_questions" in data
        assert "total_tokens_in" in data
