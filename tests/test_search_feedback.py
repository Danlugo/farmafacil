"""Tests for search feedback — parsing, logging, and recording."""

import pytest

from farmafacil.services.search_feedback import (
    log_search,
    parse_feedback,
    record_feedback,
    record_feedback_detail,
)


class TestParseFeedback:
    """Test feedback text parsing."""

    def test_positive_si(self):
        assert parse_feedback("sí") == "yes"

    def test_positive_si_no_accent(self):
        assert parse_feedback("si") == "yes"

    def test_positive_yes(self):
        assert parse_feedback("yes") == "yes"

    def test_positive_thumbs_up(self):
        assert parse_feedback("👍") == "yes"

    def test_ambiguous_gracias_not_positive(self):
        """'gracias' is a farewell for many users — must NOT auto-record as yes.

        Regression for Item 28: user Jose Lugo got the feedback-thanks message
        immediately because 'gracias' was being parsed as positive feedback.
        """
        assert parse_feedback("gracias") is None

    def test_ambiguous_ok_not_positive(self):
        """'ok' is too ambiguous — must NOT auto-record as yes."""
        assert parse_feedback("ok") is None

    def test_ambiguous_bien_not_positive(self):
        """'bien' is ambiguous — must NOT auto-record as yes."""
        assert parse_feedback("bien") is None

    def test_ambiguous_perfecto_not_positive(self):
        """'perfecto' is ambiguous — must NOT auto-record as yes."""
        assert parse_feedback("perfecto") is None

    def test_negative_no(self):
        assert parse_feedback("no") == "no"

    def test_negative_thumbs_down(self):
        assert parse_feedback("👎") == "no"

    def test_ambiguous_nada_not_negative(self):
        """'nada' is ambiguous ('nada' in 'nada más') — no longer auto-record."""
        assert parse_feedback("nada") is None

    def test_unrecognized_returns_none(self):
        assert parse_feedback("losartan") is None

    def test_unrecognized_sentence(self):
        assert parse_feedback("busco acetaminofen") is None

    def test_strips_punctuation(self):
        assert parse_feedback("sí!") == "yes"

    def test_case_insensitive(self):
        assert parse_feedback("SI") == "yes"

    def test_strips_whitespace(self):
        assert parse_feedback("  no  ") == "no"


class TestLogSearch:
    """Test search log creation."""

    @pytest.mark.asyncio
    async def test_log_search_returns_id(self):
        """log_search should create an entry and return its ID."""
        search_id = await log_search(user_id=1, query="losartan", results_count=5)
        assert isinstance(search_id, int)
        assert search_id > 0

    @pytest.mark.asyncio
    async def test_log_search_multiple(self):
        """Multiple logs should get different IDs."""
        id1 = await log_search(user_id=1, query="losartan", results_count=5)
        id2 = await log_search(user_id=1, query="acetaminofen", results_count=3)
        assert id2 > id1


class TestRecordFeedback:
    """Test feedback recording on search logs."""

    @pytest.mark.asyncio
    async def test_record_positive_feedback(self):
        """Should record 'yes' feedback on a search log."""
        from sqlalchemy import select

        from farmafacil.db.session import async_session
        from farmafacil.models.database import SearchLog

        search_id = await log_search(user_id=1, query="ibuprofeno", results_count=2)
        await record_feedback(search_id, "yes")

        async with async_session() as session:
            result = await session.execute(
                select(SearchLog).where(SearchLog.id == search_id)
            )
            entry = result.scalar_one()
            assert entry.feedback == "yes"

    @pytest.mark.asyncio
    async def test_record_negative_feedback_with_detail(self):
        """Should record 'no' feedback and detail text."""
        from sqlalchemy import select

        from farmafacil.db.session import async_session
        from farmafacil.models.database import SearchLog

        search_id = await log_search(user_id=1, query="metformina", results_count=0)
        await record_feedback(search_id, "no")
        await record_feedback_detail(search_id, "Buscaba metformina 850mg, no la genérica")

        async with async_session() as session:
            result = await session.execute(
                select(SearchLog).where(SearchLog.id == search_id)
            )
            entry = result.scalar_one()
            assert entry.feedback == "no"
            assert "850mg" in entry.feedback_detail
