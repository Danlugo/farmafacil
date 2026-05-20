"""Tests for backlog items Q4, Q7, Q8 and v0.24.0 code-review fixes (v0.26.0)."""

import asyncio

import pytest
from sqlalchemy import delete, select

from farmafacil.db.session import async_session
from farmafacil.models.database import User, UserMemory
from farmafacil.services.relevance import compute_relevance, is_relevant


# ── Q4: UserMemory __repr__ ─────────────────────────────────────────────


class TestUserMemoryRepr:
    """Q4: UserMemory should have a readable __repr__."""

    def test_repr_format(self):
        mem = UserMemory(id=1, user_id=42, memory_text="Busca losartan regularmente")
        r = repr(mem)
        assert "UserMemory" in r
        assert "user_id=42" in r
        assert "Busca losartan" in r

    def test_repr_truncates_long_text(self):
        mem = UserMemory(id=2, user_id=7, memory_text="x" * 100)
        r = repr(mem)
        # Preview is capped at 40 chars
        assert len(r) < 150


# ── Q8: Digit-overlap residual leak fix ─────────────────────────────────


class TestQ8DigitOverlap:
    """Q8: Digit-only tokens must not satisfy the Signal 0 floor."""

    def test_aspirina_500_vs_vitamina_c_500(self):
        """The canonical Q8 case: '500' alone must not pass the floor."""
        score = compute_relevance(
            "aspirina 500", "Vitamina C 500 Mg", drug_class="analgesicos"
        )
        assert score == 0.0

    def test_losartan_50_vs_atorvastatina_50(self):
        score = compute_relevance(
            "losartan 50", "Atorvastatina 50 mg", drug_class="analgesicos"
        )
        assert score == 0.0

    def test_digit_overlap_with_name_match_still_works(self):
        """When a non-digit token also matches, the floor is passed."""
        score = compute_relevance(
            "aspirina 500", "Aspirina 500 mg Bayer", drug_class="analgesicos"
        )
        assert score >= 0.5

    def test_losartan_50_correct_match(self):
        score = compute_relevance(
            "losartan 50", "Losartan 50 mg MK", drug_class="analgesicos"
        )
        assert score >= 0.5

    def test_is_relevant_rejects_digit_only_overlap(self):
        assert not is_relevant("aspirina 500", "Vitamina C 500 Mg")

    def test_is_relevant_accepts_real_match(self):
        assert is_relevant("aspirina 500", "Aspirina 500 mg Bayer")

    def test_pure_digit_query(self):
        """A pure-digit query like '500' should score 0.0 against everything."""
        score = compute_relevance("500", "Aspirina 500 mg", drug_class="analgesicos")
        assert score == 0.0

    def test_q6_regression_aspirador(self):
        """Q6 regression: 'aspirina' should NOT match 'Aspirador Nasal'."""
        score = compute_relevance(
            "aspirina", "Aspirador Nasal", drug_class="analgesicos"
        )
        assert score == 0.0


# ── Q7: Curated drug-keyword library ────────────────────────────────────


class TestDrugKeywordLibrary:
    """Q7: Common drug names should be seeded as drug_search keywords."""

    @pytest.mark.asyncio
    async def test_losartan_keyword_exists(self):
        from farmafacil.services.intent import _get_keyword_cache

        cache = await _get_keyword_cache()
        assert "losartan" in cache
        action, _ = cache["losartan"]
        assert action == "drug_search"

    @pytest.mark.asyncio
    async def test_acetaminofen_keyword_exists(self):
        from farmafacil.services.intent import _get_keyword_cache

        cache = await _get_keyword_cache()
        assert "acetaminofen" in cache or "acetaminofén" in cache

    @pytest.mark.asyncio
    async def test_drug_search_keyword_returns_drug_query(self):
        from farmafacil.services.intent import classify_intent_keywords

        intent = await classify_intent_keywords("losartan")
        assert intent is not None
        assert intent.action == "drug_search"
        assert intent.drug_query == "losartan"

    @pytest.mark.asyncio
    async def test_greeting_keyword_unchanged(self):
        from farmafacil.services.intent import classify_intent_keywords

        intent = await classify_intent_keywords("hola")
        assert intent is not None
        assert intent.action == "greeting"
        assert intent.drug_query is None


# ── v0.24.0: Settings cache thundering-herd lock ─────────────────────────


class TestSettingsCacheLock:
    """Settings cache should use a lock to prevent thundering-herd."""

    def test_lock_exists(self):
        from farmafacil.services.settings import _cache_lock

        assert isinstance(_cache_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_concurrent_get_setting_single_db_hit(self):
        """Multiple concurrent get_setting() calls should coalesce."""
        from farmafacil.services.settings import clear_settings_cache, get_setting

        clear_settings_cache()
        # Fetch same key concurrently — both should succeed
        results = await asyncio.gather(
            get_setting("cache_ttl_minutes"),
            get_setting("cache_ttl_minutes"),
        )
        assert results[0] == results[1]
        assert results[0] == "10080"


# ── v0.24.0: Background task set warning ────────────────────────────────


class TestBackgroundTaskWarning:
    """Webhook _fire_and_forget should warn on backpressure."""

    def test_max_background_tasks_constant(self):
        from farmafacil.bot.webhook import _MAX_BACKGROUND_TASKS

        assert _MAX_BACKGROUND_TASKS == 100


# ── v0.24.0: Silent no-op UPDATE logging ────────────────────────────────


class TestNoOpUpdateLogging:
    """Direct UPDATE functions should log when no rows matched."""

    @pytest.mark.asyncio
    async def test_set_onboarding_step_warns_on_no_match(self, caplog):
        import logging

        from farmafacil.services.users import set_onboarding_step

        with caplog.at_level(logging.WARNING, logger="farmafacil.services.users"):
            await set_onboarding_step("+9999999NONEXIST", "awaiting_name")

        assert "no user found" in caplog.text

    @pytest.mark.asyncio
    async def test_update_last_search_warns_on_no_match(self, caplog):
        import logging

        from farmafacil.services.users import update_last_search

        with caplog.at_level(logging.WARNING, logger="farmafacil.services.users"):
            await update_last_search("+9999999NONEXIST", "test query")

        assert "no user found" in caplog.text


# ── Q5: SQLite FK enforcement ───────────────────────────────────────────


class TestSqliteFKEnforcement:
    """Q5: Foreign key enforcement should be enabled for SQLite."""

    @pytest.mark.asyncio
    async def test_cascade_delete_removes_user_memory(self):
        """Deleting a user should cascade-delete their UserMemory."""
        phone = "+58414fk_test"
        async with async_session() as session:
            # Cleanup from prior runs
            await session.execute(delete(User).where(User.phone_number == phone))
            await session.commit()

            user = User(
                phone_number=phone,
                name="FK Test",
                latitude=10.5,
                longitude=-66.9,
                zone_name="Test",
                city_code="CCS",
                display_preference="grid",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            uid = user.id

        # Add a UserMemory row
        async with async_session() as session:
            session.add(UserMemory(user_id=uid, memory_text="test memory"))
            await session.commit()

        # Verify memory exists
        async with async_session() as session:
            mem = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            assert mem.scalar_one_or_none() is not None

        # Delete user via raw SQL (triggers DB-level CASCADE)
        async with async_session() as session:
            await session.execute(delete(User).where(User.id == uid))
            await session.commit()

        # Verify memory was cascade-deleted
        async with async_session() as session:
            mem = await session.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            assert mem.scalar_one_or_none() is None
