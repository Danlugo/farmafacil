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

    @pytest.mark.parametrize("query", [
        "Losartan 50mg",
        "Losartan 50 mg",
        "Jarabe 100ml",
        "RESVERATROL NAD+VID CAP 125MG X60 HERB",
        "Omeprazol cap 20mg",
        "Metformina tab 850mg",
        "Losartan Potasico 50mg Biumak Caja x 30",
        "Ibuprofeno frasco 120ml",
        "Sal de Andrews sobre",
        "LOSARTAN 50MG",
        "losartan 50mg",
        "Crema 30g",
    ])
    def test_specific_queries(self, query):
        """Queries with dosage, form, or count indicators are specific."""
        assert is_specific_query(query) is True

    @pytest.mark.parametrize("query", [
        "losartan",
        "acetaminofen tabletas",
        "para que sirve el losartan",
    ])
    def test_generic_queries(self, query):
        """Queries without dosage or form indicators are not specific."""
        assert is_specific_query(query) is False


class TestIsProductMatch:
    """Test strict product matching (case-insensitive exact string)."""

    @pytest.mark.parametrize("name_a,name_b", [
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
        ),
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "resveratrol nad+vid cap 125mg x60 herb",
        ),
        (
            "  Losartan 50mg  ",
            "Losartan 50mg",
        ),
    ])
    def test_matching_products(self, name_a, name_b):
        """Products that should match (identical, case-insensitive, or trimmed whitespace)."""
        assert is_product_match(name_a, name_b) is True

    @pytest.mark.parametrize("name_a,name_b", [
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "Resveratrol NAD + VID 250mg-75mg-125mg Herbaplant Antioxidante x 60 Capsulas",
        ),
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "RESVERATROL NAD+VID CAP 250MG X60 HERB",
        ),
        (
            "Losartan 50mg",
            "Enalapril 50mg",
        ),
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            "Resveratrol + Selenio Q10 250mg/125mg Lipoico Inmuneplus x 60 Capsulas",
        ),
    ])
    def test_non_matching_products(self, name_a, name_b):
        """Products that should not match (different name, dosage, or base ingredient)."""
        assert is_product_match(name_a, name_b) is False


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

    @pytest.mark.parametrize("drug_name,expected", [
        (
            "RESVERATROL NAD+VID CAP 125MG X60 HERB",
            ["resveratrol", "nad+vid", "cap", "125mg", "x60", "herb"],
        ),
        (
            "NAD+VID CAP",
            ["nad+vid", "cap"],
        ),
        (
            "NAD + VID",
            ["nad", "+", "vid"],
        ),
        (
            "LOSARTAN 50MG GENVEN",
            ["losartan", "50mg", "genven"],
        ),
        (
            "  losartan 50mg  ",
            ["losartan", "50mg"],
        ),
        (
            "",
            [],
        ),
        (
            "RESVERATROL",
            ["resveratrol"],
        ),
        (
            "250mg-75mg-125mg x60",
            ["250mg-75mg-125mg", "x60"],
        ),
    ])
    def test_parse_keywords(self, drug_name, expected):
        """Keyword tokenization produces correct lowercase token lists."""
        assert _parse_keywords(drug_name) == expected


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
