"""Tests for chat debug — settings resolution, stats queries, footer building."""

import pytest

from farmafacil.services.chat_debug import build_debug_footer, get_user_stats
from farmafacil.services.settings import resolve_chat_debug


class TestResolveChatDebug:
    """Test chat debug resolution logic."""

    def test_user_enabled_overrides_global_disabled(self):
        assert resolve_chat_debug("enabled", "disabled") is True

    def test_user_disabled_overrides_global_enabled(self):
        assert resolve_chat_debug("disabled", "enabled") is False

    def test_user_none_uses_global_enabled(self):
        assert resolve_chat_debug(None, "enabled") is True

    def test_user_none_uses_global_disabled(self):
        assert resolve_chat_debug(None, "disabled") is False

    def test_user_enabled_overrides_global_enabled(self):
        assert resolve_chat_debug("enabled", "enabled") is True

    def test_user_disabled_overrides_global_disabled(self):
        assert resolve_chat_debug("disabled", "disabled") is False

    def test_invalid_user_falls_to_global(self):
        assert resolve_chat_debug("bogus", "enabled") is True

    def test_invalid_global_defaults_false(self):
        assert resolve_chat_debug(None, "bogus") is False

    def test_both_invalid_defaults_false(self):
        assert resolve_chat_debug("bogus", "bogus") is False

    def test_empty_string_user_falls_to_global(self):
        assert resolve_chat_debug("", "enabled") is True


class TestBuildDebugFooter:
    """Test debug footer string building."""

    def test_footer_contains_model(self):
        footer = build_debug_footer("pharmacy_advisor", 100, 50, 10, 3)
        assert "claude-haiku" in footer

    def test_footer_contains_role(self):
        footer = build_debug_footer("pharmacy_advisor", 100, 50, 10, 3)
        assert "pharmacy_advisor" in footer

    def test_footer_contains_tokens(self):
        footer = build_debug_footer("pharmacy_advisor", 142, 87, 10, 3)
        assert "142 in" in footer
        assert "87 out" in footer

    def test_footer_contains_questions(self):
        footer = build_debug_footer("pharmacy_advisor", 100, 50, 23, 3)
        assert "23" in footer

    def test_footer_contains_success(self):
        footer = build_debug_footer("pharmacy_advisor", 100, 50, 10, 8)
        assert "8" in footer

    def test_footer_starts_with_separator(self):
        footer = build_debug_footer("test_role", 0, 0, 0, 0)
        assert footer.startswith("\n\n---\n")

    def test_footer_contains_debug_header(self):
        footer = build_debug_footer("test_role", 0, 0, 0, 0)
        assert "DEBUG" in footer

    def test_zero_tokens(self):
        footer = build_debug_footer("fallback", 0, 0, 0, 0)
        assert "0 in / 0 out" in footer


class TestGetUserStats:
    """Test user stats queries."""

    @pytest.mark.asyncio
    async def test_returns_dict_with_keys(self):
        stats = await get_user_stats("5550000000", 999)
        assert "total_questions" in stats
        assert "total_success" in stats

    @pytest.mark.asyncio
    async def test_new_user_has_zero_stats(self):
        stats = await get_user_stats("5559999999", 999)
        assert stats["total_questions"] == 0
        assert stats["total_success"] == 0

    @pytest.mark.asyncio
    async def test_counts_inbound_messages(self):
        """Verify total_questions counts inbound conversation_logs."""
        from farmafacil.db.session import async_session
        from farmafacil.models.database import ConversationLog

        phone = "5551112222"
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
        from farmafacil.services.search_feedback import log_search, record_feedback

        user_id = 888
        s1 = await log_search(user_id, "losartan", 5)
        await record_feedback(s1, "yes")
        s2 = await log_search(user_id, "acetaminofen", 3)
        await record_feedback(s2, "no")
        s3 = await log_search(user_id, "ibuprofeno", 2)
        await record_feedback(s3, "yes")

        stats = await get_user_stats("5550000000", user_id)
        assert stats["total_success"] == 2
