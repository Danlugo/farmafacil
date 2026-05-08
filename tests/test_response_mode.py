"""Tests for response mode filters (global + per-user override)."""

import pytest

from farmafacil.services.settings import resolve_response_mode


class TestResolveResponseMode:
    """Test the response mode resolution logic."""

    def test_user_ai_only_overrides_global_hybrid(self):
        """User set to ai_only should override global hybrid."""
        assert resolve_response_mode("ai_only", "hybrid") == "ai_only"

    def test_user_hybrid_overrides_global_ai_only(self):
        """User set to hybrid should override global ai_only."""
        assert resolve_response_mode("hybrid", "ai_only") == "hybrid"

    def test_user_none_uses_global_hybrid(self):
        """User with no override should use global hybrid."""
        assert resolve_response_mode(None, "hybrid") == "hybrid"

    def test_user_none_uses_global_ai_only(self):
        """User with no override should use global ai_only."""
        assert resolve_response_mode(None, "ai_only") == "ai_only"

    def test_user_invalid_uses_global(self):
        """User with invalid mode should fall back to global."""
        assert resolve_response_mode("invalid", "hybrid") == "hybrid"

    def test_user_empty_string_uses_global(self):
        """User with empty string should fall back to global."""
        assert resolve_response_mode("", "ai_only") == "ai_only"

    def test_invalid_global_mode_defaults_to_hybrid(self):
        """Invalid global mode should fall back to hybrid."""
        assert resolve_response_mode(None, "typo") == "hybrid"

    def test_user_override_beats_invalid_global(self):
        """Valid user mode should work even if global is invalid."""
        assert resolve_response_mode("ai_only", "typo") == "ai_only"


class TestResponseModeSettings:
    """Test response_mode in app settings."""

    @pytest.mark.asyncio
    async def test_default_response_mode_is_hybrid(self):
        """Default response mode should be hybrid."""
        from farmafacil.services.settings import get_setting

        mode = await get_setting("response_mode")
        assert mode == "hybrid"


class TestUserResponseModeColumn:
    """Test the response_mode column on User model."""

    @pytest.mark.asyncio
    async def test_new_user_has_null_response_mode(self):
        """New users should have NULL response_mode (uses global)."""
        from farmafacil.services.users import get_or_create_user

        user = await get_or_create_user("584121111111")
        assert user.response_mode is None

    @pytest.mark.asyncio
    async def test_set_user_response_mode(self):
        """Setting response_mode on a user should persist."""
        from sqlalchemy import select

        from farmafacil.db.session import async_session
        from farmafacil.models.database import User
        from farmafacil.services.users import get_or_create_user

        user = await get_or_create_user("584122222222")
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == "584122222222")
            )
            db_user = result.scalar_one()
            db_user.response_mode = "ai_only"
            await session.commit()

        # Re-fetch and verify
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == "584122222222")
            )
            db_user = result.scalar_one()
            assert db_user.response_mode == "ai_only"
