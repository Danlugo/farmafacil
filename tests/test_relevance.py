"""Tests for search relevance scoring and filtering (Item 38).

Tests cover:
- normalize() — accent stripping, punctuation removal, lowercasing
- classify_pharmaceutical() — pharma / non-pharma / unknown
- compute_relevance() — scoring with real-world examples from user feedback
- is_relevant() — threshold gating
- Integration: save_search_results filters out junk product IDs
- Integration: find_cached_products excludes is_pharmaceutical=False
- Integration: search_drug() filters all results including cached ones
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import Product, ProductKeyword, ProductPrice, SearchQuery
from farmafacil.models.schemas import DrugResult
from farmafacil.services.relevance import (
    NON_PHARMA_CATEGORIES,
    classify_pharmaceutical,
    compute_relevance,
    is_relevant,
    normalize,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_drug_result(**overrides) -> DrugResult:
    defaults = {
        "drug_name": "Losartan 50mg",
        "pharmacy_name": "Farmatodo",
        "price_bs": Decimal("15.50"),
        "full_price_bs": None,
        "discount_pct": None,
        "available": True,
        "url": "https://www.farmatodo.com.ve/losartan-50mg-123",
        "last_checked": datetime.now(tz=UTC),
        "requires_prescription": False,
        "image_url": "https://images.example.com/losartan.jpg",
        "brand": "Glenmark",
        "drug_class": "ANALGESICOS/ANTIPIRETICOS OTC",
        "unit_label": "Tabletas",
        "unit_count": 30,
        "description": "Losartan potassium 50mg tablets",
        "stores_in_stock": 5,
        "stores_with_stock_ids": [101, 102, 103, 104, 105],
    }
    defaults.update(overrides)
    return DrugResult(**defaults)


# ── Unit Tests: normalize ───────────────────────────────────────────────


class TestNormalize:
    def test_lowercase(self):
        assert normalize("ACETAMINOFEN") == "acetaminofen"

    def test_strip_accents(self):
        assert normalize("Acetaminofén") == "acetaminofen"

    def test_strip_punctuation(self):
        assert normalize("PASTILLAS LIMP/BAÑ 4 PACK") == "pastillas limp ban 4 pack"

    def test_preserve_plus(self):
        assert normalize("NAD+VID") == "nad+vid"

    def test_collapse_whitespace(self):
        assert normalize("  lots   of   spaces  ") == "lots of spaces"

    def test_empty(self):
        assert normalize("") == ""

    def test_mixed_accents_and_punctuation(self):
        assert normalize("Ibuprofeno — 400mg/cáps") == "ibuprofeno 400mg caps"


# ── Unit Tests: classify_pharmaceutical ─────────────────────────────────


class TestClassifyPharmaceutical:
    def test_pharma_category(self):
        assert classify_pharmaceutical("ANALGESICOS/ANTIPIRETICOS OTC") is True

    def test_non_pharma_category(self):
        assert classify_pharmaceutical("PANALES") is False

    def test_non_pharma_case_insensitive(self):
        # "Roblox" is Farmatodo's internal code for throat lozenges — pharma, not the game
        assert classify_pharmaceutical("Roblox") is True

    def test_unknown_none(self):
        assert classify_pharmaceutical(None) is None

    def test_unknown_empty_string(self):
        assert classify_pharmaceutical("") is None

    def test_all_non_pharma_in_set(self):
        """Every entry in NON_PHARMA_CATEGORIES classifies as False."""
        for cat in NON_PHARMA_CATEGORIES:
            assert classify_pharmaceutical(cat) is False, f"{cat} should be non-pharma"

    def test_pharma_categories_from_production(self):
        pharma_cats = [
            "ANALGESICOS/ANTIPIRETICOS OTC",
            "ANTIHIPERTENSIVOS",
            "SUPLEMENTOS ALIMENTICIOS",
            "ANTIHISTAMINICOS",
            "VITAMINA C OTC",
            "TERAPIA TIROIDEA",
            "ANTIDIABETICOS",
        ]
        for cat in pharma_cats:
            assert classify_pharmaceutical(cat) is True, f"{cat} should be pharma"


# ── Unit Tests: compute_relevance ───────────────────────────────────────


class TestComputeRelevance:
    """Real-world examples from the user feedback screenshot."""

    def test_acetaminofen_matches_acetaminofen_tablet(self):
        """Acetaminofén search should match an Acetaminofen product."""
        score = compute_relevance(
            "Acetaminofén",
            "Acetaminofen 650mg Genven Caja x 30 Tabletas",
            drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
        )
        assert score >= 0.5, f"Expected >= 0.5, got {score}"

    def test_acetaminofen_rejects_shoe_insoles(self):
        """Acetaminofén search should NOT match shoe insoles."""
        score = compute_relevance(
            "Acetaminofén",
            "PLANTILLA D/PIES MEMORY VISCOELASTICA X2",
            drug_class=None,
        )
        assert score < 0.3, f"Expected < 0.3, got {score}"

    def test_paracetamol_pastillas_rejects_cleaning_tablets(self):
        """'paracetamol pastillas' should NOT match cleaning tablets."""
        score = compute_relevance(
            "paracetamol pastillas",
            "PASTILLAS LIMP/BAÑ 4 PACK",
            drug_class="LIMPIADORES/DESINFECT",
        )
        assert score < 0.3, f"Expected < 0.3, got {score}"

    def test_paracetamol_pastillas_matches_paracetamol_tab(self):
        """'paracetamol pastillas' should match a Paracetamol tablet."""
        score = compute_relevance(
            "paracetamol pastillas",
            "Paracetamol 1g/100ml Jl Pharma Solucion Inyectable",
            drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
        )
        assert score >= 0.5, f"Expected >= 0.5, got {score}"

    def test_ibuprofeno_matches_ibuprofeno(self):
        score = compute_relevance(
            "Ibuprofeno",
            "IBUPROFENO IBUPRA TAB REC 800MG X10 ADN",
            drug_class="RX ANALGESICOS ANTIINFLAMT SISTEM",
        )
        assert score >= 0.5, f"Expected >= 0.5, got {score}"

    def test_losartan_matches_losartan(self):
        score = compute_relevance(
            "losartan",
            "Losartan 50mg MK Caja x 30 Tabletas",
            drug_class="ANTIHIPERTENSIVOS",
        )
        assert score >= 0.5, f"Expected >= 0.5, got {score}"

    def test_zero_overlap_non_pharma(self):
        """Completely unrelated product should score near zero."""
        score = compute_relevance(
            "vitamina C",
            "GALLETAS MARIA 300G",
            drug_class="GALLETAS",
        )
        assert score < 0.3, f"Expected < 0.3, got {score}"

    def test_empty_query(self):
        score = compute_relevance("", "Some Product", drug_class="ANALGESICOS")
        assert score == 0.0

    def test_empty_drug_name(self):
        score = compute_relevance("losartan", "", drug_class="ANALGESICOS")
        assert score == 0.0

    def test_partial_ingredient_overlap(self):
        """Query with multiple tokens where only the ingredient matches."""
        score = compute_relevance(
            "acetaminofen jarabe",
            "Acetaminofen 180mg/5ml Apiret Oftalmi Jarabe x 120 ml",
            drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
        )
        assert score >= 0.5, f"Expected >= 0.5, got {score}"

    def test_panales_for_drug_search(self):
        """Diapers should not match any drug query."""
        score = compute_relevance(
            "amoxicilina",
            "PANALES HUGGIES ACTIVE SEC XXGX16",
            drug_class="PANALES",
        )
        assert score < 0.3, f"Expected < 0.3, got {score}"


# ── Unit Tests: is_relevant ─────────────────────────────────────────────


class TestIsRelevant:
    def test_relevant_above_threshold(self):
        assert is_relevant(
            "losartan",
            "Losartan 50mg MK",
            drug_class="ANTIHIPERTENSIVOS",
            threshold=0.3,
        )

    def test_irrelevant_below_threshold(self):
        assert not is_relevant(
            "losartan",
            "GALLETAS MARIA 300G",
            drug_class="GALLETAS",
            threshold=0.3,
        )

    def test_custom_threshold(self):
        """With a very high threshold, low-signal matches fail."""
        # "vitamina" appears in name but drug_class is unknown (None)
        # Score: 0.5 * (1/1) + 0.15 (unknown) + 0.2 (ingredient) = 0.85
        assert not is_relevant(
            "vitamina",
            "Vitamina C 500mg",
            drug_class=None,
            threshold=0.9,
        )


# ── Integration: save_search_results filters junk ──────────────────────


from sqlalchemy import delete, select


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Clean tables before each test."""
    async with async_session() as session:
        await session.execute(delete(ProductPrice))
        await session.execute(delete(ProductKeyword))
        await session.execute(delete(SearchQuery))
        await session.execute(delete(Product))
        await session.commit()


class TestSaveSearchResultsFiltering:
    """save_search_results should only cache relevant product IDs."""

    @pytest.mark.asyncio
    async def test_junk_products_excluded_from_search_query(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="Acetaminofen 500mg Tab",
                drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
                url="https://example.com/acetaminofen-500mg",
            ),
            _make_drug_result(
                drug_name="PLANTILLA D/PIES MEMORY VISCOELASTICA X2",
                drug_class=None,
                url="https://example.com/plantilla-pies",
            ),
        ]

        await save_search_results("acetaminofen", "CCS", results)

        # Both products should exist in the products table
        async with async_session() as session:
            all_products = (await session.execute(select(Product))).scalars().all()
            assert len(all_products) == 2

            # But only the relevant one should be in the search query
            sq = (await session.execute(
                select(SearchQuery).where(SearchQuery.query == "acetaminofen")
            )).scalar_one()
            assert len(sq.product_ids) == 1

            # The cached product should be the acetaminofen, not the insole
            cached_product = (await session.execute(
                select(Product).where(Product.id == sq.product_ids[0])
            )).scalar_one()
            assert "acetaminofen" in cached_product.drug_name.lower()

    @pytest.mark.asyncio
    async def test_all_relevant_products_kept(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="Acetaminofen 500mg Tab",
                drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
                url="https://example.com/acetaminofen-500",
            ),
            _make_drug_result(
                drug_name="Acetaminofen 650mg Genven",
                drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
                url="https://example.com/acetaminofen-650",
            ),
        ]

        await save_search_results("acetaminofen", "CCS", results)

        async with async_session() as session:
            sq = (await session.execute(
                select(SearchQuery).where(SearchQuery.query == "acetaminofen")
            )).scalar_one()
            assert len(sq.product_ids) == 2

    @pytest.mark.asyncio
    async def test_all_junk_leaves_empty_query(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="GALLETAS MARIA 300G",
                drug_class="GALLETAS",
                url="https://example.com/galletas",
            ),
        ]

        await save_search_results("ibuprofeno", "CCS", results)

        async with async_session() as session:
            sq = (await session.execute(
                select(SearchQuery).where(SearchQuery.query == "ibuprofeno")
            )).scalar_one()
            assert len(sq.product_ids) == 0


class TestIsPharmaceuticalBackfill:
    """_upsert_product should set is_pharmaceutical on products."""

    @pytest.mark.asyncio
    async def test_pharma_product_flagged_true(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="Losartan 50mg",
                drug_class="ANTIHIPERTENSIVOS",
                url="https://example.com/losartan",
            ),
        ]

        await save_search_results("losartan", "CCS", results)

        async with async_session() as session:
            product = (await session.execute(
                select(Product).where(Product.drug_name == "Losartan 50mg")
            )).scalar_one()
            assert product.is_pharmaceutical is True

    @pytest.mark.asyncio
    async def test_non_pharma_product_flagged_false(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="GALLETAS MARIA 300G",
                drug_class="GALLETAS",
                url="https://example.com/galletas",
            ),
        ]

        await save_search_results("galletas", "CCS", results)

        async with async_session() as session:
            product = (await session.execute(
                select(Product).where(Product.drug_name == "GALLETAS MARIA 300G")
            )).scalar_one()
            assert product.is_pharmaceutical is False

    @pytest.mark.asyncio
    async def test_unknown_class_flagged_none(self):
        from farmafacil.services.product_cache import save_search_results

        results = [
            _make_drug_result(
                drug_name="Mystery Product",
                drug_class=None,
                url="https://example.com/mystery",
            ),
        ]

        await save_search_results("mystery", "CCS", results)

        async with async_session() as session:
            product = (await session.execute(
                select(Product).where(Product.drug_name == "Mystery Product")
            )).scalar_one()
            assert product.is_pharmaceutical is None


class TestFindCachedProductsExcludesNonPharma:
    """find_cached_products should not return is_pharmaceutical=False products."""

    @pytest.mark.asyncio
    async def test_non_pharma_excluded(self):
        from farmafacil.services.product_cache import find_cached_products, save_search_results

        # Save a pharma and a non-pharma product both containing "losartan"
        results = [
            _make_drug_result(
                drug_name="Losartan 50mg Tab",
                drug_class="ANTIHIPERTENSIVOS",
                url="https://example.com/losartan-tab",
            ),
        ]
        await save_search_results("losartan", "CCS", results)

        # Manually insert a non-pharma product that also matches "losartan" keyword
        async with async_session() as session:
            fake = Product(
                external_id="fake-losartan-candy",
                pharmacy_chain="Farmatodo",
                drug_name="Losartan Candy Flavor",
                drug_class="GALLETAS",
                is_pharmaceutical=False,
                keywords=["losartan", "candy", "flavor"],
            )
            session.add(fake)
            await session.flush()
            # Add a fresh price so it passes the TTL check
            session.add(ProductPrice(
                product_id=fake.id,
                city_code="CCS",
                full_price_bs=Decimal("5.00"),
                in_stock=True,
                stores_in_stock_count=1,
            ))
            await session.commit()

        # find_cached_products should NOT return the candy
        cached = await find_cached_products("losartan 50mg tab", "CCS")
        assert cached is not None
        names = [r.drug_name for r in cached]
        assert "Losartan 50mg Tab" in names
        assert "Losartan Candy Flavor" not in names


class TestSearchDrugRelevanceFiltering:
    """search_drug() should filter results from all sources."""

    @pytest.mark.asyncio
    async def test_live_scraper_results_filtered(self):
        from farmafacil.services.search import search_drug

        mock_results = [
            _make_drug_result(
                drug_name="Acetaminofen 500mg Tab",
                drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
                url="https://example.com/acetaminofen",
            ),
            _make_drug_result(
                drug_name="PLANTILLA D/PIES MEMORY VISCOELASTICA X2",
                drug_class=None,
                url="https://example.com/plantilla",
            ),
            _make_drug_result(
                drug_name="PASTILLAS LIMP/BAN 4 PACK",
                drug_class="LIMPIADORES/DESINFECT",
                url="https://example.com/pastillas-limp",
            ),
        ]

        # Mock all scrapers to return the mixed results
        with patch(
            "farmafacil.services.search.ACTIVE_SCRAPERS",
            [AsyncMock(pharmacy_name="TestPharm", search=AsyncMock(return_value=mock_results))],
        ):
            response = await search_drug("acetaminofen")

        # Only the real acetaminofen should survive
        assert response.total == 1
        assert response.results[0].drug_name == "Acetaminofen 500mg Tab"

    @pytest.mark.asyncio
    async def test_cached_results_also_filtered(self):
        """Even cache hits pass through the relevance filter."""
        from farmafacil.services.product_cache import save_search_results
        from farmafacil.services.search import search_drug

        # Pre-populate cache with both good and bad results
        # (simulating pre-v0.15.0 data where junk was cached)
        results = [
            _make_drug_result(
                drug_name="Ibuprofeno 400mg Tab",
                drug_class="ANALGESICOS/ANTIPIRETICOS OTC",
                url="https://example.com/ibuprofeno",
            ),
        ]
        await save_search_results("ibuprofeno", None, results)

        # Now search — should hit cache and filter
        response = await search_drug("ibuprofeno")
        assert response.total >= 1
        for r in response.results:
            assert is_relevant("ibuprofeno", r.drug_name, r.drug_class, r.description)
