"""Tests for English→Spanish drug name translation (Item 116, v0.37.0).

Covers:
- translate_drug_query() — AI-powered translation with mocked LLM
- TranslationResult — token accounting from translation calls
- Handler zero-result retry path — translation triggers on empty results
- AI instruction updates — tool_use and hybrid mode include translation rules
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.models.schemas import DrugResult, SearchResponse
from farmafacil.services.drug_translation import TranslationResult


def _make_drug_result(name: str = "TEST DRUG") -> DrugResult:
    """Create a minimal DrugResult for testing."""
    return DrugResult(
        drug_name=name,
        pharmacy_name="Farmatodo",
        available=True,
    )


def _make_mock_llm_response(text: str, in_tokens: int = 30, out_tokens: int = 5):
    """Create a mock Anthropic API response."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(input_tokens=in_tokens, output_tokens=out_tokens)
    return resp


# ── translate_drug_query unit tests (mocked LLM) ─────────────────────────


class TestTranslateDrugQuery:
    """Test the AI translation service with mocked Anthropic calls."""

    @pytest.mark.parametrize(
        "query,ai_response,expected",
        [
            ("amlodipine", "amlodipino", "amlodipino"),
            ("acetaminophen", "acetaminofén", "acetaminofén"),
            ("ibuprofen", "ibuprofeno", "ibuprofeno"),
            ("omeprazole", "omeprazol", "omeprazol"),
            ("metformin", "metformina", "metformina"),
            ("atorvastatin", "atorvastatina", "atorvastatina"),
            ("azithromycin", "azitromicina", "azitromicina"),
            ("simvastatin", "simvastatina", "simvastatina"),
            ("losartan", "losartán", "losartán"),
            ("ciprofloxacin", "ciprofloxacina", "ciprofloxacina"),
        ],
        ids=[
            "amlodipine", "acetaminophen", "ibuprofen", "omeprazole",
            "metformin", "atorvastatin", "azithromycin", "simvastatin",
            "losartan", "ciprofloxacin",
        ],
    )
    @pytest.mark.asyncio
    async def test_translates_english_drug_names(
        self, query, ai_response, expected,
    ):
        """Known English drug names return TranslationResult with correct name."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_llm_response(ai_response),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query(query)

        assert result is not None
        assert result.name == expected
        assert result.input_tokens == 30
        assert result.output_tokens == 5

    @pytest.mark.asyncio
    async def test_returns_none_for_spanish_name(self):
        """Already-Spanish names return None (AI responds 'NO')."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_llm_response("NO"),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query("ibuprofeno")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_no_with_punctuation(self):
        """AI responding 'NO.' (with trailing period) is still treated as no."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_llm_response("NO."),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query("ibuprofeno")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_same_name(self):
        """When AI returns the same name as the query, returns None."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_llm_response("losartan"),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query("losartan")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        """API errors return None (never propagate to caller)."""
        from anthropic import APIConnectionError

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=APIConnectionError(request=MagicMock()),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query("amlodipine")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_content(self):
        """Empty API content list returns None (max_tokens edge case)."""
        mock_client = AsyncMock()
        resp = MagicMock()
        resp.content = []  # empty content
        resp.stop_reason = "max_tokens"
        mock_client.messages.create = AsyncMock(return_value=resp)

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            result = await translate_drug_query("amlodipine")

        assert result is None

    @pytest.mark.parametrize(
        "query",
        ["", "  ", "ab", "x" * 101],
        ids=["empty", "whitespace", "too-short", "too-long"],
    )
    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_length_input(self, query):
        """Very short, empty, or excessively long queries skip the API call."""
        from farmafacil.services.drug_translation import translate_drug_query

        result = await translate_drug_query(query)
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_temperature_zero(self):
        """The API call must use temperature=0 for deterministic output."""
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_mock_llm_response("amlodipino"),
        )

        with patch(
            "farmafacil.services.ai_responder._get_client",
            return_value=mock_client,
        ):
            from farmafacil.services.drug_translation import translate_drug_query

            await translate_drug_query("amlodipine")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0


# ── TranslationResult unit tests ──────────────────────────────────────────


class TestTranslationResult:
    """Verify TranslationResult holds name and token data."""

    def test_stores_name_and_tokens(self):
        tr = TranslationResult("amlodipino", 30, 5)
        assert tr.name == "amlodipino"
        assert tr.input_tokens == 30
        assert tr.output_tokens == 5


# ── Handler zero-result retry integration tests ──────────────────────────


class TestHandlerTranslationRetry:
    """Test that _handle_drug_search retries with translated query on zero results."""

    def _make_mock_translation(self, name="amlodipino"):
        """Create a TranslationResult mock matching handler's attribute access."""
        return TranslationResult(name, input_tokens=30, output_tokens=5)

    @pytest.mark.asyncio
    async def test_retries_with_translation_on_zero_results(self):
        """When search returns 0 results and translation succeeds, retry search."""
        empty_response = SearchResponse(
            query="amlodipine", city=None, zone=None,
            results=[], total=0, searched_pharmacies=["Farmatodo"],
        )
        translated_response = SearchResponse(
            query="amlodipino", city=None, zone=None,
            results=[_make_drug_result("AMLODIPINO 10MG")],
            total=1, searched_pharmacies=["Farmatodo"],
        )

        call_count = 0

        async def mock_search_drug(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return empty_response
            return translated_response

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.latitude = 10.5
        mock_user.longitude = -66.9
        mock_user.zone_name = "Test Zone"
        mock_user.city_code = "CCS"
        mock_user.last_search_query = None

        mock_send = AsyncMock()
        mock_increment = AsyncMock()

        with patch.multiple(
            "farmafacil.bot.handler",
            search_drug=AsyncMock(side_effect=mock_search_drug),
            translate_drug_query=AsyncMock(
                return_value=self._make_mock_translation("amlodipino"),
            ),
            send_text_message=mock_send,
            send_image_message=AsyncMock(),
            increment_token_usage=mock_increment,
            log_search=AsyncMock(return_value=1),
            update_last_search=AsyncMock(),
            get_memory=AsyncMock(return_value=""),
            auto_update_memory=AsyncMock(),
            set_onboarding_step=AsyncMock(),
            _send_detail_images=AsyncMock(),
            format_search_results=MagicMock(return_value="results text"),
        ):
            from farmafacil.bot.handler import _handle_drug_search

            await _handle_drug_search(
                sender="1234567890",
                user=mock_user,
                query="amlodipine",
                display_name="Test User",
            )

        # search_drug called twice: first with English, then with Spanish
        assert call_count == 2
        # Translation notification sent to user
        translation_msgs = [
            c for c in mock_send.call_args_list
            if "Traduje" in str(c)
        ]
        assert len(translation_msgs) == 1
        assert "amlodipine" in str(translation_msgs[0])
        assert "amlodipino" in str(translation_msgs[0])
        # Token usage recorded with actual values from TranslationResult
        token_calls = [
            c for c in mock_increment.call_args_list
            if c.args[1] == 30 and c.args[2] == 5
        ]
        assert len(token_calls) >= 1

    @pytest.mark.asyncio
    async def test_no_translation_notification_when_retry_also_empty(self):
        """When translation is found but retry also returns 0, no 'Traduje' msg."""
        empty_response = SearchResponse(
            query="amlodipine", city=None, zone=None,
            results=[], total=0, searched_pharmacies=["Farmatodo"],
        )

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.latitude = 10.5
        mock_user.longitude = -66.9
        mock_user.zone_name = "Test Zone"
        mock_user.city_code = "CCS"
        mock_user.last_search_query = None

        mock_send = AsyncMock()

        with patch.multiple(
            "farmafacil.bot.handler",
            search_drug=AsyncMock(return_value=empty_response),
            translate_drug_query=AsyncMock(
                return_value=self._make_mock_translation("amlodipino"),
            ),
            send_text_message=mock_send,
            send_image_message=AsyncMock(),
            increment_token_usage=AsyncMock(),
            log_search=AsyncMock(return_value=1),
            update_last_search=AsyncMock(),
            get_memory=AsyncMock(return_value=""),
            auto_update_memory=AsyncMock(),
            set_onboarding_step=AsyncMock(),
            _send_detail_images=AsyncMock(),
            format_search_results=MagicMock(return_value="results text"),
        ):
            from farmafacil.bot.handler import _handle_drug_search

            await _handle_drug_search(
                sender="1234567890",
                user=mock_user,
                query="amlodipine",
                display_name="Test User",
            )

        # "Traduje" message should NOT appear since retry also returned 0
        translation_msgs = [
            c for c in mock_send.call_args_list
            if "Traduje" in str(c)
        ]
        assert len(translation_msgs) == 0

    @pytest.mark.asyncio
    async def test_no_retry_when_results_exist(self):
        """When search returns results, translation is NOT called."""
        good_response = SearchResponse(
            query="losartan", city=None, zone=None,
            results=[_make_drug_result("LOSARTAN 50MG")],
            total=1, searched_pharmacies=["Farmatodo"],
        )

        mock_translate = AsyncMock(return_value=self._make_mock_translation("losartán"))

        with patch.multiple(
            "farmafacil.bot.handler",
            search_drug=AsyncMock(return_value=good_response),
            translate_drug_query=mock_translate,
            send_text_message=AsyncMock(),
            send_image_message=AsyncMock(),
            increment_token_usage=AsyncMock(),
            log_search=AsyncMock(return_value=1),
            update_last_search=AsyncMock(),
            get_memory=AsyncMock(return_value=""),
            auto_update_memory=AsyncMock(),
            validate_search_results=AsyncMock(
                return_value=(good_response.results, 0, 0, ""),
            ),
            set_onboarding_step=AsyncMock(),
            _send_detail_images=AsyncMock(),
            format_search_results=MagicMock(return_value="results text"),
        ):
            from farmafacil.bot.handler import _handle_drug_search

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.latitude = 10.5
            mock_user.longitude = -66.9
            mock_user.zone_name = "Test Zone"
            mock_user.city_code = "CCS"
            mock_user.last_search_query = None

            await _handle_drug_search(
                sender="1234567890",
                user=mock_user,
                query="losartan",
                display_name="Test User",
            )

        # translate_drug_query should NOT have been called
        mock_translate.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_retry_when_translation_returns_none(self):
        """When translation returns None (already Spanish), no retry."""
        empty_response = SearchResponse(
            query="xyz_unknown", city=None, zone=None,
            results=[], total=0, searched_pharmacies=["Farmatodo"],
        )

        mock_search = AsyncMock(return_value=empty_response)

        with patch.multiple(
            "farmafacil.bot.handler",
            search_drug=mock_search,
            translate_drug_query=AsyncMock(return_value=None),
            send_text_message=AsyncMock(),
            send_image_message=AsyncMock(),
            increment_token_usage=AsyncMock(),
            log_search=AsyncMock(return_value=1),
            update_last_search=AsyncMock(),
            get_memory=AsyncMock(return_value=""),
            auto_update_memory=AsyncMock(),
            set_onboarding_step=AsyncMock(),
            _send_detail_images=AsyncMock(),
            format_search_results=MagicMock(return_value="results text"),
        ):
            from farmafacil.bot.handler import _handle_drug_search

            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.latitude = 10.5
            mock_user.longitude = -66.9
            mock_user.zone_name = "Test Zone"
            mock_user.city_code = "CCS"
            mock_user.last_search_query = None

            await _handle_drug_search(
                sender="1234567890",
                user=mock_user,
                query="xyz_unknown",
                display_name="Test User",
            )

        # search_drug called only once (no retry)
        assert mock_search.call_count == 1


# ── AI instruction contract tests ────────────────────────────────────────


class TestAiInstructionsIncludeTranslation:
    """Verify AI instructions tell the model to translate English drug names."""

    def test_tool_use_search_drug_description_mentions_spanish(self):
        """search_drug tool schema tells AI to translate English→Spanish."""
        from farmafacil.services.ai_responder import TOOL_DEFINITIONS

        search_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_drug")
        query_desc = search_tool["input_schema"]["properties"]["query"]["description"]
        assert "español" in query_desc.lower()
        assert "amlodipino" in query_desc

    def test_tool_use_instructions_mention_english_translation(self):
        """TOOL_USE_INSTRUCTIONS include the English→Spanish translation rule."""
        from farmafacil.services.ai_responder import TOOL_USE_INSTRUCTIONS

        assert "NOMBRE EN INGLÉS" in TOOL_USE_INSTRUCTIONS
        assert "amlodipino" in TOOL_USE_INSTRUCTIONS

    def test_classify_instructions_mention_english_translation(self):
        """CLASSIFY_INSTRUCTIONS include the English→Spanish translation rule."""
        from farmafacil.services.ai_responder import CLASSIFY_INSTRUCTIONS

        assert "NOMBRE EN INGLÉS" in CLASSIFY_INSTRUCTIONS
        assert "amlodipino" in CLASSIFY_INSTRUCTIONS
