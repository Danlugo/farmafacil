"""Tests for the catalog rephrase service (Item 128, v0.48.0)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from farmafacil.services.catalog_rephrase import RephraseResult, rephrase_for_catalog


# ── Unit tests: RephraseResult ──────────────────────────────────────────

class TestRephraseResult:
    """Test RephraseResult dataclass-like object."""

    def test_slots(self):
        r = RephraseResult("cinta kinesiológica", 50, 10)
        assert r.name == "cinta kinesiológica"
        assert r.input_tokens == 50
        assert r.output_tokens == 10


# ── Guard tests: input validation ────────────────────────────────────────

class TestRephraseGuards:
    """Test input validation guards."""

    @pytest.mark.parametrize("query", [
        "",
        "  ",
        "ab",      # too short (< 3 chars)
        "x" * 101, # too long (> 100 chars)
    ], ids=["empty", "whitespace", "too_short", "too_long"])
    async def test_rejects_invalid_input(self, query):
        result = await rephrase_for_catalog(query)
        assert result is None


# ── API response handling ────────────────────────────────────────────────

def _make_response(text, in_tokens=50, out_tokens=10):
    """Build a mock Claude API response."""
    content_block = MagicMock()
    content_block.text = text
    resp = MagicMock()
    resp.content = [content_block]
    resp.usage = MagicMock(input_tokens=in_tokens, output_tokens=out_tokens)
    return resp


class TestRephraseApiCalls:
    """Test API response parsing and edge cases."""

    async def test_successful_rephrase(self):
        """AI suggests a different catalog name → return RephraseResult."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("cinta kinesiológica")
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("kinesiotape")
        assert result is not None
        assert result.name == "cinta kinesiológica"
        assert result.input_tokens == 50
        assert result.output_tokens == 10

    async def test_returns_no(self):
        """AI responds 'NO' → no rephrase needed."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_response("NO"))
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("acetaminofen")
        assert result is None

    async def test_returns_same_word(self):
        """AI returns the same term back → no rephrase."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("ibuprofeno")
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("ibuprofeno")
        assert result is None

    async def test_case_insensitive_same_check(self):
        """Case difference is treated as same term."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("Losartan")
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("losartan")
        assert result is None

    async def test_strips_trailing_period(self):
        """Trailing period in AI response is stripped."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("vendas adhesivas.")
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("curitas")
        assert result is not None
        assert result.name == "vendas adhesivas"

    async def test_empty_content_returns_none(self):
        """Empty API response content → None."""
        resp = MagicMock()
        resp.content = []
        resp.stop_reason = "end_turn"
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("kinesiotape")
        assert result is None

    async def test_api_error_returns_none(self):
        """API error is swallowed → None, no crash."""
        from anthropic import APIConnectionError

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock())
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("kinesiotape")
        assert result is None

    async def test_unexpected_error_returns_none(self):
        """Unexpected exception is swallowed → None."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("kinesiotape")
        assert result is None

    async def test_returns_no_with_trailing_period(self):
        """AI returns 'NO.' with period → stripped to 'NO' → None."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_response("NO."))
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("losartan")
        assert result is None

    async def test_returns_lowercase_no(self):
        """AI returns 'no' (lowercase) → None."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=_make_response("no"))
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("aspirina")
        assert result is None

    async def test_multiline_response_uses_first_line(self):
        """Multiline AI response — only first line used."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("cinta kinesiológica\n(para lesiones deportivas)")
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog("kinesiotape")
        assert result is not None
        assert result.name == "cinta kinesiológica"


# ── Parametrized: known rephrase examples ────────────────────────────────

class TestKnownRephrases:
    """Verify the service processes realistic rephrase examples correctly."""

    @pytest.mark.parametrize("query,catalog_name", [
        ("kinesiotape", "cinta kinesiológica"),
        ("tylenol", "acetaminofén"),
        ("curitas", "vendas adhesivas"),
        ("agua oxigenada", "peróxido de hidrógeno"),
        ("vick vaporub", "ungüento mentolado"),
    ], ids=["kinesiotape", "tylenol", "curitas", "agua_oxigenada", "vick"])
    async def test_rephrase_examples(self, query, catalog_name):
        """AI suggests a valid catalog name for a colloquial query."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response(catalog_name)
        )
        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            result = await rephrase_for_catalog(query)
        assert result is not None
        assert result.name == catalog_name
