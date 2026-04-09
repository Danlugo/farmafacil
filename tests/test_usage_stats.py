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
            user = User(phone_number="5559901111", onboarding_step="welcome")
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
            user = User(phone_number="5559902222", onboarding_step="welcome")
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
            user = User(phone_number="5559903333", onboarding_step="welcome")
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

    @pytest.mark.asyncio
    async def test_haiku_model_increments_haiku_counters(self):
        """Haiku model name routes tokens to haiku-specific counters."""
        async with async_session() as session:
            user = User(phone_number="5559911111", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 200, 80, model="claude-haiku-4-5-20251001")

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()
            assert user.total_tokens_in == 200
            assert user.total_tokens_out == 80
            assert user.tokens_in_haiku == 200
            assert user.tokens_out_haiku == 80
            assert user.calls_haiku == 1
            assert user.tokens_in_sonnet == 0
            assert user.calls_sonnet == 0

    @pytest.mark.asyncio
    async def test_sonnet_model_increments_sonnet_counters(self):
        """Sonnet model name routes tokens to sonnet-specific counters."""
        async with async_session() as session:
            user = User(phone_number="5559922222", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 300, 150, model="claude-sonnet-4-20250514")

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()
            assert user.total_tokens_in == 300
            assert user.total_tokens_out == 150
            assert user.tokens_in_sonnet == 300
            assert user.tokens_out_sonnet == 150
            assert user.calls_sonnet == 1
            assert user.tokens_in_haiku == 0
            assert user.calls_haiku == 0

    @pytest.mark.asyncio
    async def test_mixed_model_calls_accumulate_separately(self):
        """Multiple calls with different models accumulate to correct counters."""
        async with async_session() as session:
            user = User(phone_number="5559933333", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 100, 40, model="claude-haiku-4-5-20251001")
        await increment_token_usage(user_id, 300, 150, model="claude-sonnet-4-20250514")
        await increment_token_usage(user_id, 200, 60, model="claude-haiku-4-5-20251001")

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()
            # Aggregates
            assert user.total_tokens_in == 600
            assert user.total_tokens_out == 250
            # Haiku
            assert user.tokens_in_haiku == 300
            assert user.tokens_out_haiku == 100
            assert user.calls_haiku == 2
            # Sonnet
            assert user.tokens_in_sonnet == 300
            assert user.tokens_out_sonnet == 150
            assert user.calls_sonnet == 1

    @pytest.mark.asyncio
    async def test_unknown_model_only_increments_aggregate(self):
        """Unknown model increments aggregate but not per-model counters."""
        async with async_session() as session:
            user = User(phone_number="5559944444", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 100, 50, model="some-unknown-model")

        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()
            assert user.total_tokens_in == 100
            assert user.total_tokens_out == 50
            assert user.tokens_in_haiku == 0
            assert user.tokens_in_sonnet == 0
            assert user.calls_haiku == 0
            assert user.calls_sonnet == 0


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
            user = User(phone_number="5559904444", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        await increment_token_usage(user_id, 500, 200)

        stats = await get_user_stats("5559904444", user_id)
        assert stats["total_tokens_in"] == 500
        assert stats["total_tokens_out"] == 200

    @pytest.mark.asyncio
    async def test_zero_tokens_for_new_user(self):
        from farmafacil.services.chat_debug import get_user_stats

        async with async_session() as session:
            user = User(phone_number="5559905555", onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        stats = await get_user_stats("5559905555", user_id)
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
        assert "user tokens: _1500 in / 600 out_" in footer

    def test_footer_shows_both_per_call_and_total(self):
        from farmafacil.services.chat_debug import build_debug_footer

        footer = build_debug_footer(
            role_used="test", input_tokens=100, output_tokens=50,
            total_questions=5, total_success=2,
            total_tokens_in=1000, total_tokens_out=400,
        )
        assert "tokens: _100 in / 50 out_" in footer
        assert "user tokens: _1000 in / 400 out_" in footer


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
    async def test_global_stats_includes_per_model_breakdown(self):
        """Global stats endpoint includes haiku/sonnet breakdown and costs."""
        from httpx import ASGITransport, AsyncClient

        from farmafacil.api.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "haiku" in data
        assert "sonnet" in data
        assert "est_cost_total_usd" in data
        # Verify haiku sub-object keys
        for key in ("tokens_in", "tokens_out", "calls", "est_cost_usd"):
            assert key in data["haiku"], f"Missing haiku.{key}"
            assert key in data["sonnet"], f"Missing sonnet.{key}"

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
            user = User(phone_number="5559906666", onboarding_step="welcome")
            session.add(user)
            await session.commit()

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stats?phone=5559906666")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "5559906666"
        assert "total_questions" in data
        assert "total_tokens_in" in data
