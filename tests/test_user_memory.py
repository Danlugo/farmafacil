"""Tests for the user memory service — context building and memory updates."""

import random

import pytest
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import SearchLog, User, UserMemory
from farmafacil.services.user_memory import (
    MAX_MEMORY_LENGTH,
    _get_user_context,
    get_memory,
    update_memory,
)


@pytest.fixture
async def test_user():
    """Create a test user with a unique phone number for memory tests."""
    phone = f"555{random.randint(1000000, 9999999)}"
    async with async_session() as session:
        user = User(
            phone_number=phone,
            name="TestMemoria",
            zone_name="Chacao",
            city_code="CCS",
            latitude=10.50,
            longitude=-66.85,
            display_preference="grid",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


class TestGetMemory:
    """Test memory retrieval."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_new_user(self, test_user):
        """New user with no memory returns empty string."""
        result = await get_memory(test_user.id)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_text_after_update(self, test_user):
        """After update, get_memory returns the stored text."""
        await update_memory(test_user.id, "- Busca losartan regularmente", "ai")
        result = await get_memory(test_user.id)
        assert "losartan" in result


class TestUpdateMemory:
    """Test memory creation and updates."""

    @pytest.mark.asyncio
    async def test_creates_new_memory(self, test_user):
        """First update creates a new memory row."""
        await update_memory(test_user.id, "- Usuario nuevo", "ai")
        result = await get_memory(test_user.id)
        assert result == "- Usuario nuevo"

    @pytest.mark.asyncio
    async def test_updates_existing_memory(self, test_user):
        """Second update modifies existing row."""
        await update_memory(test_user.id, "- Version 1", "ai")
        await update_memory(test_user.id, "- Version 2", "ai")
        result = await get_memory(test_user.id)
        assert result == "- Version 2"

    @pytest.mark.asyncio
    async def test_truncates_long_memory(self, test_user):
        """Memory exceeding MAX_MEMORY_LENGTH is truncated."""
        long_text = "x" * (MAX_MEMORY_LENGTH + 500)
        await update_memory(test_user.id, long_text, "ai")
        result = await get_memory(test_user.id)
        assert len(result) == MAX_MEMORY_LENGTH

    @pytest.mark.asyncio
    async def test_tracks_updated_by(self, test_user):
        """updated_by field is set correctly."""
        await update_memory(test_user.id, "- Admin edit", "admin")
        async with async_session() as session:
            result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == test_user.id)
            )
            memory = result.scalar_one()
            assert memory.updated_by == "admin"


class TestGetUserContext:
    """Test the user context builder for memory LLM."""

    @pytest.mark.asyncio
    async def test_includes_profile_info(self, test_user):
        """Context includes user name and location."""
        ctx = await _get_user_context(test_user.id)
        assert "TestMemoria" in ctx
        assert "Chacao" in ctx

    @pytest.mark.asyncio
    async def test_includes_display_preference(self, test_user):
        """Context includes display preference."""
        ctx = await _get_user_context(test_user.id)
        assert "grid" in ctx

    @pytest.mark.asyncio
    async def test_includes_search_history(self, test_user):
        """Context includes recent searches when they exist."""
        async with async_session() as session:
            session.add(SearchLog(
                user_id=test_user.id, query="losartan", results_count=5,
            ))
            session.add(SearchLog(
                user_id=test_user.id, query="protector solar", results_count=3,
            ))
            await session.commit()

        ctx = await _get_user_context(test_user.id)
        assert "losartan" in ctx
        assert "protector solar" in ctx

    @pytest.mark.asyncio
    async def test_empty_search_history(self, test_user):
        """Context works with no search history."""
        ctx = await _get_user_context(test_user.id)
        assert "TestMemoria" in ctx
        # No "Recent searches" section when empty
        assert "Recent searches" not in ctx

    @pytest.mark.asyncio
    async def test_nonexistent_user(self):
        """Context returns empty for nonexistent user."""
        ctx = await _get_user_context(999999)
        assert ctx == ""


class TestMemoryPrompt:
    """Verify the memory system prompt covers broad user profiling."""

    def test_prompt_mentions_search_patterns(self):
        """Memory prompt should track search patterns."""
        import inspect
        from farmafacil.services.user_memory import auto_update_memory

        source = inspect.getsource(auto_update_memory)
        assert "search" in source.lower() or "búsqueda" in source.lower()

    def test_prompt_mentions_communication_style(self):
        """Memory prompt should track communication style."""
        import inspect
        from farmafacil.services.user_memory import auto_update_memory

        source = inspect.getsource(auto_update_memory)
        assert "communication style" in source.lower() or "estilo" in source.lower()

    def test_prompt_mentions_family(self):
        """Memory prompt should track family/dependents."""
        import inspect
        from farmafacil.services.user_memory import auto_update_memory

        source = inspect.getsource(auto_update_memory)
        assert "family" in source.lower() or "familia" in source.lower()
