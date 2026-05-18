"""Tests for best-price modifier feature (v0.21.3).

When user says "mejor precio de X", the AI sets MODIFIER: best_price,
and the handler filters search results to show only the cheapest
in-stock option.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from farmafacil.bot.handler import _handle_drug_search
from farmafacil.services.ai_responder import _parse_structured_response, CLASSIFY_INSTRUCTIONS


# ── Parser tests ──────────────────────────────────────────────────────


class TestModifierParsing:
    """Verify _parse_structured_response extracts MODIFIER field."""

    def test_parse_best_price_modifier(self):
        """MODIFIER: best_price is extracted."""
        reply = "ACTION: drug_search\nDRUG: losartan\nMODIFIER: best_price"
        result = _parse_structured_response(reply)
        assert result.action == "drug_search"
        assert result.drug_query == "losartan"
        assert result.modifier == "best_price"

    def test_parse_no_modifier(self):
        """Without MODIFIER line, modifier is None."""
        reply = "ACTION: drug_search\nDRUG: losartan"
        result = _parse_structured_response(reply)
        assert result.modifier is None

    def test_parse_unknown_modifier(self):
        """Unknown modifier value is passed through (not validated)."""
        reply = "ACTION: drug_search\nDRUG: losartan\nMODIFIER: some_other"
        result = _parse_structured_response(reply)
        assert result.modifier == "some_other"


class TestClassifyInstructions:
    """Verify CLASSIFY_INSTRUCTIONS contains the best-price rule."""

    def test_modifier_field_in_format(self):
        """MODIFIER appears in the response format section."""
        assert "MODIFIER:" in CLASSIFY_INSTRUCTIONS

    def test_best_price_rule_exists(self):
        """The best-price rule is documented in instructions."""
        assert "best_price" in CLASSIFY_INSTRUCTIONS
        assert "mejor precio" in CLASSIFY_INSTRUCTIONS.lower()


# ── Best-price filter in _handle_drug_search ──────────────────────────


class TestBestPriceFilter:
    """Verify _handle_drug_search filters to cheapest when best_price=True."""

    @pytest.fixture
    def mock_user(self):
        user = MagicMock()
        user.id = 1
        user.name = "Daniel"
        user.latitude = 10.4378
        user.longitude = -66.8354
        user.zone_name = "La Tahona"
        user.city_code = "CCS"
        return user

    def _make_result(self, name, pharmacy, price, available=True):
        """Create a mock DrugResult with the given fields."""
        r = MagicMock()
        r.drug_name = name
        r.pharmacy_name = pharmacy
        r.price_bs = Decimal(str(price)) if price is not None else None
        r.available = available
        r.image_url = None
        return r

    @pytest.mark.asyncio
    async def test_best_price_keeps_cheapest_only(self, mock_user):
        """With best_price=True, only the cheapest in-stock result remains."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", 15.00),
            self._make_result("Losartan 50mg", "Locatel", 12.50),
            self._make_result("Losartan 100mg", "SAAS", 22.00),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            # format_search_results should receive only 1 result (the cheapest)
            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 1
            assert formatted_response.results[0].price_bs == Decimal("12.50")
            assert formatted_response.results[0].pharmacy_name == "Locatel"

    @pytest.mark.asyncio
    async def test_best_price_skips_out_of_stock(self, mock_user):
        """Out-of-stock items are excluded from best-price selection."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", 5.00, available=False),
            self._make_result("Losartan 50mg", "Locatel", 15.00, available=True),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 1
            # Should pick the in-stock one at 15.00, not the OOS at 5.00
            assert formatted_response.results[0].price_bs == Decimal("15.00")

    @pytest.mark.asyncio
    async def test_best_price_false_keeps_all(self, mock_user):
        """Without best_price, all results pass through."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", 15.00),
            self._make_result("Losartan 50mg", "Locatel", 12.50),
            self._make_result("Losartan 100mg", "SAAS", 22.00),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=False,
            )

            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 3

    @pytest.mark.asyncio
    async def test_best_price_sends_hint_message(self, mock_user):
        """When best_price is active, reply includes a hint about full results."""
        results = [
            self._make_result("Losartan 50mg", "Locatel", 12.50),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="Losartan results"),
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            # The reply message should contain the best-price hint
            sent_msgs = [call.args[1] for call in mock_send.call_args_list]
            combined = " ".join(sent_msgs)
            assert "más económica" in combined

    @pytest.mark.asyncio
    async def test_best_price_no_in_stock_keeps_all(self, mock_user):
        """If all results are OOS, best_price filter doesn't remove anything."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", 15.00, available=False),
            self._make_result("Losartan 50mg", "Locatel", 12.50, available=False),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            # No in-stock results → filter doesn't fire → all results kept
            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 2

    @pytest.mark.asyncio
    async def test_best_price_all_prices_none_keeps_all(self, mock_user):
        """In-stock results with no price → filter skips them → all kept."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", None, available=True),
            self._make_result("Losartan 50mg", "Locatel", None, available=True),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock) as mock_send,
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            # No priced in-stock results → filter doesn't fire → all kept
            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 2
            # Hint should NOT appear since filter didn't actually fire
            sent_msgs = [call.args[1] for call in mock_send.call_args_list]
            combined = " ".join(sent_msgs)
            assert "más económica" not in combined

    @pytest.mark.asyncio
    async def test_best_price_tie_keeps_first(self, mock_user):
        """When two results tie on price, the first by insertion order wins."""
        results = [
            self._make_result("Losartan 50mg", "Farmatodo", 12.50),
            self._make_result("Losartan 50mg", "Locatel", 12.50),
            self._make_result("Losartan 100mg", "SAAS", 25.00),
        ]

        with (
            patch("farmafacil.bot.handler.search_drug", new_callable=AsyncMock) as mock_search,
            patch("farmafacil.bot.handler.send_text_message", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.get_memory", new_callable=AsyncMock, return_value=""),
            patch("farmafacil.bot.handler.log_search", new_callable=AsyncMock, return_value=1),
            patch("farmafacil.bot.handler.update_last_search", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._update_memory_safe", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.set_onboarding_step", new_callable=AsyncMock),
            patch("farmafacil.bot.handler.format_search_results", return_value="mock reply") as mock_fmt,
            patch("farmafacil.bot.handler._send_detail_images", new_callable=AsyncMock),
            patch("farmafacil.bot.handler._should_ask_feedback", return_value=False),
        ):
            mock_response = MagicMock()
            mock_response.results = results
            mock_response.failed_pharmacies = []
            mock_search.return_value = mock_response

            await _handle_drug_search(
                "584121234567", mock_user, "Losartan", "Daniel",
                best_price=True,
            )

            formatted_response = mock_fmt.call_args.args[0]
            assert len(formatted_response.results) == 1
            # min() with equal values returns the first one
            assert formatted_response.results[0].pharmacy_name == "Farmatodo"
