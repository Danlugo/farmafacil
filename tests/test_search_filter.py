"""Tests for specific product query detection and exact-match filtering."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.models.schemas import DrugResult, SearchResponse
from farmafacil.services.product_cache import _parse_keywords
from farmafacil.services.search import (
    filter_exact_results,
    filter_exact_results_with_cross_chain,
    is_product_match,
    is_specific_query,
)
from farmafacil.bot.formatter import format_search_results


class TestIsSpecificQuery:
    """Test detection of specific product queries (with dosage, form, count)."""

    def test_generic_query_not_specific(self):
        """A simple drug name without dosage is not specific."""
        assert is_specific_query("losartan") is False

    def test_generic_two_words_not_specific(self):
        """Two-word generic query is not specific."""
        assert is_specific_query("acetaminofen tabletas") is False

    def test_dosage_mg_is_specific(self):
        """Query with milligram dosage is specific."""
        assert is_specific_query("Losartan 50mg") is True

    def test_dosage_mg_with_space_is_specific(self):
        """Query with spaced milligram dosage is specific."""
        assert is_specific_query("Losartan 50 mg") is True

    def test_dosage_ml_is_specific(self):
        """Query with milliliter dosage is specific."""
        assert is_specific_query("Jarabe 100ml") is True

    def test_unit_count_x60_is_specific(self):
        """Query with unit count (X60) is specific."""
        assert is_specific_query("RESVERATROL NAD+VID CAP 125MG X60 HERB") is True

    def test_cap_form_is_specific(self):
        """Query with capsule form indicator is specific."""
        assert is_specific_query("Omeprazol cap 20mg") is True

    def test_tab_form_is_specific(self):
        """Query with tablet form indicator is specific."""
        assert is_specific_query("Metformina tab 850mg") is True

    def test_caja_is_specific(self):
        """Query with 'caja' is specific."""
        assert is_specific_query("Losartan Potasico 50mg Biumak Caja x 30") is True

    def test_frasco_is_specific(self):
        """Query with 'frasco' is specific."""
        assert is_specific_query("Ibuprofeno frasco 120ml") is True

    def test_sobre_is_specific(self):
        """Query with 'sobre' is specific."""
        assert is_specific_query("Sal de Andrews sobre") is True

    def test_case_insensitive(self):
        """Detection is case-insensitive."""
        assert is_specific_query("LOSARTAN 50MG") is True
        assert is_specific_query("losartan 50mg") is True

    def test_question_not_specific(self):
        """A question about a drug is not specific (no dosage indicators)."""
        assert is_specific_query("para que sirve el losartan") is False

    def test_grams_is_specific(self):
        """Query with grams is specific."""
        assert is_specific_query("Crema 30g") is True


class TestIsProductMatch:
    """Test strict product matching (case-insensitive exact string)."""

    def test_exact_same_name(self):
        """Identical names match."""
        assert is_product_match(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
        ) is True

    def test_different_casing(self):
        """Case-insensitive match works."""
        assert is_product_match(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "resveratrol nad+vid cap 125mg x60 herb",
        ) is True

    def test_different_product_no_match(self):
        """Different product name does not match."""
        assert is_product_match(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "Resveratrol NAD + VID 250mg-75mg-125mg Herbaplant Antioxidante x 60 Capsulas",
        ) is False

    def test_different_dosage_no_match(self):
        """Different dosage does not match."""
        assert is_product_match(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "RESVERATROL NAD+VID CAP 250MG X60 HERB",
        ) is False

    def test_different_base_name_no_match(self):
        """Different base drug name does not match."""
        assert is_product_match(
            "Losartan 50mg",
            "Enalapril 50mg",
        ) is False

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace is trimmed."""
        assert is_product_match(
            "  Losartan 50mg  ",
            "Losartan 50mg",
        ) is True

    def test_compound_product_not_matched_by_single_ingredient(self):
        """A compound product (250mg/125mg) is not matched by a 125mg query."""
        assert is_product_match(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "Resveratrol + Selenio Q10 250mg/125mg Lipoico Inmuneplus x 60 Capsulas",
        ) is False


class TestFilterExactResults:
    """Test splitting results into exact matches and similar products."""

    def _make_result(self, name: str, pharmacy: str = "Farmatodo") -> DrugResult:
        return DrugResult(
            drug_name=name,
            pharmacy_name=pharmacy,
            price_bs=Decimal("100"),
            available=True,
        )

    def test_exact_match_found(self):
        """Exact name match is separated from similar products."""
        results = [
            self._make_result("RESVERATROL NAD+VID CAP 125MG X60 HERB"),
            self._make_result("RESVERATROL NAD+VID CAP 250MG X60 HERB"),
            self._make_result("RESVERATROL NAD+VID TAB 500MG X30"),
        ]
        exact, similar = filter_exact_results(
            results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
        )
        assert len(exact) == 1
        assert exact[0].drug_name == "RESVERATROL NAD+VID CAP 125MG X60 HERB"
        assert len(similar) == 2

    def test_same_name_different_chains_both_match(self):
        """Same exact product name from different chains both match."""
        results = [
            self._make_result(
                "RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"
            ),
            self._make_result(
                "RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmatodo"
            ),
            self._make_result(
                "Resveratrol + NAD 400mg/200mg Natural Premium Frasco x 60 Capsulas",
                "Farmatodo",
            ),
        ]
        exact, similar = filter_exact_results(
            results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
        )
        assert len(exact) == 2
        pharmacies = {r.pharmacy_name for r in exact}
        assert pharmacies == {"Farmacias SAAS", "Farmatodo"}
        assert len(similar) == 1

    def test_different_naming_across_chains_goes_to_similar(self):
        """Products with different names (even if same product) are similar."""
        results = [
            self._make_result(
                "RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"
            ),
            self._make_result(
                "Resveratrol NAD + VID 250mg-75mg-125mg Herbaplant Antioxidante x 60 Capsulas",
                "Farmatodo",
            ),
        ]
        exact, similar = filter_exact_results(
            results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
        )
        assert len(exact) == 1
        assert exact[0].pharmacy_name == "Farmacias SAAS"
        assert len(similar) == 1
        assert similar[0].pharmacy_name == "Farmatodo"

    def test_no_match_shows_all_as_similar(self):
        """When no product matches, all results are similar."""
        results = [
            self._make_result("Losartan 50mg GenVen"),
            self._make_result("Losartan 100mg GenVen"),
        ]
        exact, similar = filter_exact_results(results, "Losartan 25mg")
        assert len(exact) == 0
        assert len(similar) == 2

    def test_empty_results(self):
        """Empty results return empty tuples."""
        exact, similar = filter_exact_results([], "test")
        assert exact == []
        assert similar == []


class TestFormatterSimilarCount:
    """Test that formatter shows 'ver similares' when similar_count > 0."""

    def test_similar_count_shows_message(self):
        """When similar_count > 0, shows 'ver similares' prompt."""
        response = SearchResponse(
            query="RESVERATROL NAD+VID CAP 125MG X60 HERB",
            results=[
                DrugResult(
                    drug_name="RESVERATROL NAD+VID CAP 125MG X60 HERB",
                    pharmacy_name="Farmacias SAAS",
                    price_bs=Decimal("10"),
                    available=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmacias SAAS"],
            similar_count=5,
        )
        text = format_search_results(response)
        assert "5" in text
        assert "similares" in text
        assert "ver similares" in text

    def test_no_similar_count_no_message(self):
        """When similar_count is 0, no 'ver similares' message."""
        response = SearchResponse(
            query="losartan",
            results=[
                DrugResult(
                    drug_name="Losartan 50mg",
                    pharmacy_name="Farmatodo",
                    price_bs=Decimal("900"),
                    available=True,
                ),
            ],
            total=1,
            searched_pharmacies=["Farmatodo"],
            similar_count=0,
        )
        text = format_search_results(response)
        assert "ver similares" not in text

    def test_similar_count_default_zero(self):
        """SearchResponse defaults similar_count to 0."""
        response = SearchResponse(
            query="test",
            results=[],
            total=0,
            searched_pharmacies=["Farmatodo"],
        )
        assert response.similar_count == 0


class TestParseKeywords:
    """Test keyword tokenization from drug names."""

    def test_basic_split(self):
        """Split by whitespace, lowercase all tokens."""
        result = _parse_keywords("RESVERATROL NAD+VID CAP 125MG X60 HERB")
        assert result == ["resveratrol", "nad+vid", "cap", "125mg", "x60", "herb"]

    def test_plus_preserved_within_token(self):
        """Plus sign is preserved when adjacent to characters (no space around it)."""
        result = _parse_keywords("NAD+VID CAP")
        assert "nad+vid" in result

    def test_plus_with_spaces_splits_into_separate_tokens(self):
        """Plus sign with spaces around it creates separate tokens."""
        result = _parse_keywords("NAD + VID")
        assert result == ["nad", "+", "vid"]

    def test_lowercase_applied(self):
        """All tokens are lowercased."""
        result = _parse_keywords("LOSARTAN 50MG GENVEN")
        assert result == ["losartan", "50mg", "genven"]

    def test_leading_trailing_whitespace_stripped(self):
        """Leading and trailing whitespace is stripped before splitting."""
        result = _parse_keywords("  losartan 50mg  ")
        assert result == ["losartan", "50mg"]

    def test_empty_string_returns_empty_list(self):
        """Empty drug name returns empty list."""
        result = _parse_keywords("")
        assert result == []

    def test_single_word(self):
        """Single word is returned as single-element list."""
        result = _parse_keywords("RESVERATROL")
        assert result == ["resveratrol"]

    def test_special_characters_in_tokens(self):
        """Special chars like - and / are preserved within tokens."""
        result = _parse_keywords("250mg-75mg-125mg x60")
        assert result == ["250mg-75mg-125mg", "x60"]


class TestFilterExactResultsWithCrossChain:
    """Test cross-chain keyword matching in async filter."""

    def _make_result(self, name: str, pharmacy: str = "Farmatodo") -> DrugResult:
        return DrugResult(
            drug_name=name,
            pharmacy_name=pharmacy,
            price_bs=Decimal("100"),
            available=True,
        )

    @pytest.mark.asyncio
    async def test_cross_chain_results_added_to_exact(self):
        """Cross-chain keyword matches are appended to exact matches."""
        results = [
            self._make_result("RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"),
        ]
        cross_chain_result = self._make_result(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmatodo"
        )

        with patch(
            "farmafacil.services.search.find_cross_chain_matches",
            new=AsyncMock(return_value=[cross_chain_result]),
        ):
            exact, similar = await filter_exact_results_with_cross_chain(
                results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
            )

        assert len(exact) == 2
        assert len(similar) == 0
        pharmacies = {r.pharmacy_name for r in exact}
        assert "Farmatodo" in pharmacies
        assert "Farmacias SAAS" in pharmacies

    @pytest.mark.asyncio
    async def test_cross_chain_not_duplicated_in_similar(self):
        """If a cross-chain match was in similar, it is removed from similar and added to exact."""
        farmatodo_product = self._make_result(
            "Resveratrol NAD + VID 250mg-75mg-125mg Herbaplant Antioxidante x 60 Capsulas",
            "Farmatodo",
        )
        results = [
            self._make_result("RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"),
            farmatodo_product,
        ]

        with patch(
            "farmafacil.services.search.find_cross_chain_matches",
            new=AsyncMock(return_value=[farmatodo_product]),
        ):
            exact, similar = await filter_exact_results_with_cross_chain(
                results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
            )

        # The Farmatodo product should be in exact, not similar
        exact_names = [r.drug_name for r in exact]
        similar_names = [r.drug_name for r in similar]
        assert farmatodo_product.drug_name in exact_names
        assert farmatodo_product.drug_name not in similar_names

    @pytest.mark.asyncio
    async def test_no_cross_chain_results_unchanged(self):
        """When cross-chain finds nothing, original exact/similar split is unchanged."""
        results = [
            self._make_result("RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"),
            self._make_result("RESVERATROL NAD+VID CAP 250MG X60 HERB", "Farmatodo"),
        ]

        with patch(
            "farmafacil.services.search.find_cross_chain_matches",
            new=AsyncMock(return_value=[]),
        ):
            exact, similar = await filter_exact_results_with_cross_chain(
                results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
            )

        assert len(exact) == 1
        assert exact[0].pharmacy_name == "Farmacias SAAS"
        assert len(similar) == 1

    @pytest.mark.asyncio
    async def test_exclude_names_passed_to_cross_chain_lookup(self):
        """The exclude_names set passed to find_cross_chain_matches contains existing exact names."""
        saas_product = self._make_result(
            "RESVERATROL NAD+VID CAP 125MG X60 HERB", "Farmacias SAAS"
        )
        results = [saas_product]
        captured_exclude: list[set] = []

        async def capture_call(query_keywords, city_code, exclude_names):
            captured_exclude.append(set(exclude_names))
            return []

        with patch(
            "farmafacil.services.search.find_cross_chain_matches",
            new=capture_call,
        ):
            await filter_exact_results_with_cross_chain(
                results, "RESVERATROL NAD+VID CAP 125MG X60 HERB"
            )

        # The exact match drug_name (lowercased) must be in exclude_names
        assert len(captured_exclude) == 1
        assert "resveratrol nad+vid cap 125mg x60 herb" in captured_exclude[0]
