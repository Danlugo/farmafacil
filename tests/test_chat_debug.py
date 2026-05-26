"""Tests for chat debug — settings resolution, stats queries, footer building."""

import pytest

from farmafacil.services.chat_debug import (
    build_debug_footer,
    estimate_cost,
    estimate_cost_breakdown,
    get_user_stats,
)
from farmafacil.services.settings import resolve_chat_debug


class TestResolveChatDebug:
    """Test chat debug resolution logic."""

    @pytest.mark.parametrize("user_val,global_val,expected", [
        ("enabled", "disabled", True),
        ("disabled", "enabled", False),
        (None, "enabled", True),
        (None, "disabled", False),
        ("enabled", "enabled", True),
        ("disabled", "disabled", False),
        ("bogus", "enabled", True),
        (None, "bogus", False),
        ("bogus", "bogus", False),
        ("", "enabled", True),
    ])
    def test_resolve_chat_debug(self, user_val, global_val, expected):
        assert resolve_chat_debug(user_val, global_val) is expected


class TestBuildDebugFooter:
    """Test debug footer string building."""

    def test_footer_contains_role_model_tokens_and_counts(self):
        footer = build_debug_footer("pharmacy_advisor", 142, 87, 23, 8)
        assert "claude-haiku" in footer
        assert "pharmacy_advisor" in footer
        assert "142 in" in footer
        assert "87 out" in footer
        assert "23" in footer
        assert "8" in footer

    def test_footer_starts_with_separator_and_contains_debug_header(self):
        footer = build_debug_footer("test_role", 0, 0, 0, 0)
        assert footer.startswith("\n\n---\n")
        assert "DEBUG" in footer

    def test_zero_tokens(self):
        footer = build_debug_footer("fallback", 0, 0, 0, 0)
        assert "0 in / 0 out" in footer

    def test_footer_contains_app_version(self):
        from farmafacil import __version__
        footer = build_debug_footer("test_role", 0, 0, 0, 0)
        assert f"app version: _{__version__}_" in footer

    def test_footer_contains_global_tokens(self):
        footer = build_debug_footer(
            "test_role", 10, 20, 5, 1,
            total_tokens_in=100, total_tokens_out=200,
            global_tokens_in=5000, global_tokens_out=8000,
        )
        assert "global tokens: _5000 in / 8000 out_" in footer

    def test_footer_user_tokens_labeled(self):
        footer = build_debug_footer(
            "test_role", 10, 20, 5, 1,
            total_tokens_in=100, total_tokens_out=200,
        )
        assert "user tokens: _100 in / 200 out_" in footer

    def test_footer_contains_est_cost(self):
        footer = build_debug_footer(
            "test_role", 1000, 200, 5, 1,
        )
        assert "est cost: _$" in footer

    def test_footer_contains_global_est_cost(self):
        footer = build_debug_footer(
            "test_role", 10, 20, 5, 1,
            global_tokens_in=500000, global_tokens_out=100000,
        )
        assert "global est cost: _$" in footer

    def test_footer_contains_call_counts(self):
        footer = build_debug_footer(
            "test_role", 10, 20, 5, 1,
            calls_haiku=12, calls_sonnet=3,
            global_calls_haiku=100, global_calls_sonnet=25,
        )
        # v0.14.0 added admin bucket — footer now renders three call types.
        assert "user calls: _haiku=12 sonnet=3 admin=0_" in footer
        assert "global calls: _haiku=100 sonnet=25 admin=0_" in footer


class TestEstimateCost:
    """Test token cost estimation."""

    @pytest.mark.parametrize("tokens_in,tokens_out,model,expected", [
        (0, 0, "haiku", 0.0),
        (1_000_000, 0, "haiku", 1.00),
        (0, 1_000_000, "haiku", 5.00),
        (500, 100, "haiku", 0.001),
        (500, 200, "haiku", 0.0015),
        (1_000_000, 0, "sonnet", 3.00),
        (0, 1_000_000, "sonnet", 15.00),
        (1_000_000, 1_000_000, "opus", 90.00),
    ])
    def test_pricing(self, tokens_in, tokens_out, model, expected):
        cost = estimate_cost(tokens_in, tokens_out, model)
        assert abs(cost - expected) < 0.001

    def test_full_model_name_resolves_to_family(self):
        # Full model name should resolve to correct pricing
        cost_full = estimate_cost(1_000_000, 0, "claude-haiku-4-5-20251001")
        cost_family = estimate_cost(1_000_000, 0, "haiku")
        assert cost_full == cost_family

    def test_unknown_model_uses_haiku_default(self):
        cost = estimate_cost(1_000_000, 0, "some-unknown-model")
        cost_haiku = estimate_cost(1_000_000, 0, "haiku")
        assert cost == cost_haiku


class TestEstimateCostBreakdown:
    """Test per-model cost breakdown calculation."""

    def test_haiku_only_stats(self):
        stats = {
            "tokens_in_haiku": 1_000_000, "tokens_out_haiku": 500_000,
            "tokens_in_sonnet": 0, "tokens_out_sonnet": 0,
        }
        result = estimate_cost_breakdown(stats)
        assert abs(result["cost_haiku"] - 3.50) < 0.001  # 1*1.0 + 0.5*5.0
        assert result["cost_sonnet"] == 0.0
        assert abs(result["cost_total"] - 3.50) < 0.001

    def test_mixed_model_stats(self):
        stats = {
            "tokens_in_haiku": 1_000_000, "tokens_out_haiku": 200_000,
            "tokens_in_sonnet": 500_000, "tokens_out_sonnet": 100_000,
        }
        result = estimate_cost_breakdown(stats)
        # Haiku: 1.0 + 1.0 = 2.0
        assert abs(result["cost_haiku"] - 2.00) < 0.001
        # Sonnet: 1.5 + 1.5 = 3.0
        assert abs(result["cost_sonnet"] - 3.00) < 0.001
        assert abs(result["cost_total"] - 5.00) < 0.001

    def test_empty_stats_zero_cost(self):
        result = estimate_cost_breakdown({})
        assert result["cost_total"] == 0.0


class TestClassifyModel:
    """Test model family classification."""

    @pytest.mark.parametrize("model_str,expected", [
        ("claude-haiku-4-5-20251001", "haiku"),
        ("claude-sonnet-4-20250514", "sonnet"),
        ("gpt-4o", "unknown"),
        ("Claude-HAIKU-4", "haiku"),
        ("CLAUDE-SONNET-4", "sonnet"),
        ("", "unknown"),
    ])
    def test_classify_model(self, model_str, expected):
        from farmafacil.services.users import _classify_model
        assert _classify_model(model_str) == expected


class TestGetUserStats:
    """Test user stats queries."""

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        """Stats dict contains all required top-level and per-model keys."""
        stats = await get_user_stats("5550000000", 999)
        for key in [
            "total_questions", "total_success",
            "global_tokens_in", "global_tokens_out",
            "tokens_in_haiku", "tokens_out_haiku", "calls_haiku",
            "tokens_in_sonnet", "tokens_out_sonnet", "calls_sonnet",
            "global_tokens_in_haiku", "global_tokens_out_haiku",
            "global_calls_haiku", "global_tokens_in_sonnet",
            "global_tokens_out_sonnet", "global_calls_sonnet",
        ]:
            assert key in stats, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_new_user_has_zero_stats(self):
        stats = await get_user_stats("5559999999", 999)
        assert stats["total_questions"] == 0
        assert stats["total_success"] == 0

    @pytest.mark.asyncio
    async def test_counts_inbound_messages(self):
        """Verify total_questions counts inbound conversation_logs."""
        import random

        from sqlalchemy import delete

        from farmafacil.db.session import async_session
        from farmafacil.models.database import ConversationLog

        # Use a unique phone to avoid DB pollution from other tests
        phone = f"55588{random.randint(100000, 999999)}"

        # Ensure clean slate for this phone
        async with async_session() as session:
            await session.execute(
                delete(ConversationLog).where(
                    ConversationLog.phone_number == phone
                )
            )
            await session.commit()

        async with async_session() as session:
            session.add(ConversationLog(
                phone_number=phone, direction="inbound",
                message_text="hola", message_type="text",
            ))
            session.add(ConversationLog(
                phone_number=phone, direction="outbound",
                message_text="Hola! Soy FarmaFacil", message_type="text",
            ))
            session.add(ConversationLog(
                phone_number=phone, direction="inbound",
                message_text="losartan", message_type="text",
            ))
            await session.commit()

        stats = await get_user_stats(phone, 999)
        assert stats["total_questions"] == 2

    @pytest.mark.asyncio
    async def test_counts_positive_feedback(self):
        """Verify total_success counts search_logs with feedback='yes'."""
        import random

        from sqlalchemy import delete

        from farmafacil.db.session import async_session
        from farmafacil.models.database import SearchLog, User
        from farmafacil.services.search_feedback import log_search, record_feedback

        # Create a dedicated user so search_logs don't collide with others
        phone = f"55500{random.randint(100000, 999999)}"
        async with async_session() as session:
            await session.execute(delete(User).where(User.phone_number == phone))
            await session.commit()
        async with async_session() as session:
            user = User(phone_number=phone, name="FeedbackUser")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            user_id = user.id

        # Clean any stale search logs for this user
        async with async_session() as session:
            await session.execute(
                delete(SearchLog).where(SearchLog.user_id == user_id)
            )
            await session.commit()

        s1 = await log_search(user_id, "losartan", 5)
        await record_feedback(s1, "yes")
        s2 = await log_search(user_id, "acetaminofen", 3)
        await record_feedback(s2, "no")
        s3 = await log_search(user_id, "ibuprofeno", 2)
        await record_feedback(s3, "yes")

        stats = await get_user_stats(phone, user_id)
        assert stats["total_success"] == 2
