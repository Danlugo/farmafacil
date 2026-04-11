"""Tests for the product catalog service (product_cache.py).

Tests cover:
- Saving search results creates products, prices, and search queries
- Cache hit returns results when fresh
- Cache miss returns None when stale
- Upsert updates existing products without duplication
- Price per city_code isolation
- External ID extraction from URLs
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from farmafacil.db.session import async_session
from farmafacil.models.database import Product, ProductKeyword, ProductPrice, SearchQuery
from farmafacil.models.schemas import DrugResult
from farmafacil.services.product_cache import (
    _extract_external_id,
    _product_to_drug_result,
    find_cross_chain_matches,
    get_cached_results,
    save_search_results,
)
from sqlalchemy import delete, select


def _make_drug_result(**overrides) -> DrugResult:
    """Create a DrugResult with sensible defaults for testing."""
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
        "drug_class": "Antihypertensives",
        "unit_label": "Tabletas",
        "unit_count": 30,
        "description": "Losartan potassium 50mg tablets",
        "stores_in_stock": 5,
        "stores_with_stock_ids": [101, 102, 103, 104, 105],
    }
    defaults.update(overrides)
    return DrugResult(**defaults)


@pytest.fixture(autouse=True)
async def _clean_catalog_tables():
    """Clean catalog tables before each test."""
    async with async_session() as session:
        await session.execute(delete(ProductPrice))
        await session.execute(delete(SearchQuery))
        await session.execute(delete(ProductKeyword))
        await session.execute(delete(Product))
        await session.commit()
    yield


class TestExtractExternalId:
    """Tests for the external ID extraction utility."""

    def test_extracts_slug_from_url(self) -> None:
        """URL slug is used as external ID."""
        drug = _make_drug_result(url="https://www.farmatodo.com.ve/losartan-50mg-123")
        assert _extract_external_id(drug) == "losartan-50mg-123"

    def test_fallback_when_no_url(self) -> None:
        """Falls back to pharmacy:drug_name when no URL."""
        drug = _make_drug_result(url=None)
        assert _extract_external_id(drug) == "farmatodo:losartan 50mg"

    def test_strips_trailing_slash(self) -> None:
        """Trailing slashes are stripped before extracting slug."""
        drug = _make_drug_result(url="https://www.farmatodo.com.ve/test-slug/")
        assert _extract_external_id(drug) == "test-slug"


class TestSaveSearchResults:
    """Tests for saving search results to the catalog."""

    @pytest.mark.asyncio
    async def test_save_creates_product_and_price(self) -> None:
        """Saving results creates product and price records."""
        drug = _make_drug_result()
        await save_search_results("losartan", "CCS", [drug])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1
            assert products[0].drug_name == "Losartan 50mg"
            assert products[0].pharmacy_chain == "Farmatodo"

            prices = (await session.execute(select(ProductPrice))).scalars().all()
            assert len(prices) == 1
            assert prices[0].city_code == "CCS"
            assert prices[0].in_stock is True

    @pytest.mark.asyncio
    async def test_save_creates_search_query(self) -> None:
        """Saving results creates a search query mapping."""
        drug = _make_drug_result()
        await save_search_results("losartan", "CCS", [drug])

        async with async_session() as session:
            queries = (await session.execute(select(SearchQuery))).scalars().all()
            assert len(queries) == 1
            assert queries[0].query == "losartan"
            assert queries[0].city_code == "CCS"
            assert len(queries[0].product_ids) == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_product(self) -> None:
        """Saving the same product twice updates it instead of duplicating."""
        drug1 = _make_drug_result(brand="OldBrand")
        await save_search_results("losartan", "CCS", [drug1])

        drug2 = _make_drug_result(brand="NewBrand")
        await save_search_results("losartan", "CCS", [drug2])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1
            assert products[0].brand == "NewBrand"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_price(self) -> None:
        """Saving the same product for same city updates the price."""
        drug1 = _make_drug_result(price_bs=Decimal("10.00"))
        await save_search_results("losartan", "CCS", [drug1])

        drug2 = _make_drug_result(price_bs=Decimal("12.50"))
        await save_search_results("losartan", "CCS", [drug2])

        async with async_session() as session:
            prices = (await session.execute(select(ProductPrice))).scalars().all()
            assert len(prices) == 1
            assert prices[0].full_price_bs == Decimal("12.50")

    @pytest.mark.asyncio
    async def test_different_cities_get_separate_prices(self) -> None:
        """Same product in different cities gets separate price records."""
        drug = _make_drug_result()
        await save_search_results("losartan", "CCS", [drug])
        await save_search_results("losartan", "MCBO", [drug])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 1  # Same product

            prices = (await session.execute(select(ProductPrice))).scalars().all()
            assert len(prices) == 2  # Different city prices
            city_codes = {p.city_code for p in prices}
            assert city_codes == {"CCS", "MCBO"}

    @pytest.mark.asyncio
    async def test_empty_results_does_nothing(self) -> None:
        """Saving empty results is a no-op."""
        await save_search_results("nonexistent", "CCS", [])

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 0

    @pytest.mark.asyncio
    async def test_multiple_products_saved(self) -> None:
        """Multiple products in one save are all persisted."""
        drugs = [
            _make_drug_result(
                drug_name="Losartan 50mg",
                url="https://www.farmatodo.com.ve/losartan-50",
            ),
            _make_drug_result(
                drug_name="Losartan 100mg",
                url="https://www.farmatodo.com.ve/losartan-100",
            ),
        ]
        await save_search_results("losartan", "CCS", drugs)

        async with async_session() as session:
            products = (await session.execute(select(Product))).scalars().all()
            assert len(products) == 2

            sq = (await session.execute(select(SearchQuery))).scalar_one()
            assert len(sq.product_ids) == 2


class TestGetCachedResults:
    """Tests for retrieving cached results."""

    @pytest.mark.asyncio
    async def test_cache_hit_when_fresh(self) -> None:
        """Returns results when search query is within TTL."""
        drug = _make_drug_result()
        await save_search_results("losartan", "CCS", [drug])

        with patch(
            "farmafacil.services.product_cache.get_setting_int",
            new_callable=AsyncMock,
            return_value=30,
        ):
            results = await get_cached_results("losartan", "CCS")

        assert results is not None
        assert len(results) == 1
        assert results[0].drug_name == "Losartan 50mg"

    @pytest.mark.asyncio
    async def test_cache_miss_when_stale(self) -> None:
        """Returns None when search query is older than TTL."""
        drug = _make_drug_result()
        await save_search_results("losartan", "CCS", [drug])

        # Manually set searched_at to past
        async with async_session() as session:
            sq = (await session.execute(select(SearchQuery))).scalar_one()
            sq.searched_at = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(hours=2)
            await session.commit()

        with patch(
            "farmafacil.services.product_cache.get_setting_int",
            new_callable=AsyncMock,
            return_value=30,
        ):
            results = await get_cached_results("losartan", "CCS")

        assert results is None

    @pytest.mark.asyncio
    async def test_cache_miss_unknown_query(self) -> None:
        """Returns None for a query that has never been searched."""
        with patch(
            "farmafacil.services.product_cache.get_setting_int",
            new_callable=AsyncMock,
            return_value=30,
        ):
            results = await get_cached_results("unknown-drug", "CCS")

        assert results is None

    @pytest.mark.asyncio
    async def test_cache_returns_correct_city_price(self) -> None:
        """Cached results include the price for the requested city."""
        drug_ccs = _make_drug_result(price_bs=Decimal("15.50"))
        drug_mcbo = _make_drug_result(price_bs=Decimal("18.00"))
        await save_search_results("losartan", "CCS", [drug_ccs])
        await save_search_results("losartan", "MCBO", [drug_mcbo])

        with patch(
            "farmafacil.services.product_cache.get_setting_int",
            new_callable=AsyncMock,
            return_value=30,
        ):
            results = await get_cached_results("losartan", "CCS")

        assert results is not None
        assert results[0].price_bs == Decimal("15.50")


class TestProductToDrugResult:
    """Tests for the ORM-to-Pydantic conversion."""

    def test_converts_product_with_price(self) -> None:
        """Product with price converts correctly to DrugResult."""
        product = Product(
            id=1,
            external_id="test-slug",
            pharmacy_chain="Farmatodo",
            drug_name="Test Drug",
            brand="TestBrand",
            image_url="https://img.test/drug.jpg",
            unit_count=10,
            unit_label="Tabletas",
            requires_prescription=True,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        price = ProductPrice(
            id=1,
            product_id=1,
            city_code="CCS",
            full_price_bs=Decimal("20.00"),
            offer_price_bs=Decimal("15.00"),
            discount_pct="25%",
            in_stock=True,
            stores_in_stock_count=3,
            stores_with_stock_ids=[1, 2, 3],
            refreshed_at=datetime.now(tz=UTC),
        )

        result = _product_to_drug_result(product, price)

        assert result.drug_name == "Test Drug"
        assert result.pharmacy_name == "Farmatodo"
        assert result.price_bs == Decimal("15.00")  # offer price
        assert result.full_price_bs == Decimal("20.00")
        assert result.discount_pct == "25%"
        assert result.available is True
        assert result.requires_prescription is True

    def test_converts_product_without_price(self) -> None:
        """Product without price returns unavailable DrugResult."""
        product = Product(
            id=1,
            external_id="test-slug",
            pharmacy_chain="Farmatodo",
            drug_name="No Price Drug",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        result = _product_to_drug_result(product, None)

        assert result.drug_name == "No Price Drug"
        assert result.price_bs is None
        assert result.available is False
        assert result.stores_in_stock == 0


class TestProductKeywordSync:
    """Tests for the inverted product_keywords index (Item 30, v0.12.6).

    ``save_search_results`` must mirror every token from ``Product.drug_name``
    into one row per (product_id, keyword) in the ``product_keywords`` table
    so ``find_cross_chain_matches`` can do an indexed lookup instead of a
    full Python scan.
    """

    @pytest.mark.asyncio
    async def test_save_populates_product_keywords(self) -> None:
        """Saving a product inserts one row per unique token."""
        drug = _make_drug_result(
            drug_name="RESVERATROL NAD+VID CAP 125MG X60 HERB",
            url="https://www.farmatodo.com.ve/resveratrol-nad-vid-125mg",
        )
        await save_search_results("resveratrol", "CCS", [drug])

        async with async_session() as session:
            rows = (
                await session.execute(select(ProductKeyword))
            ).scalars().all()

        keywords = {r.keyword for r in rows}
        assert keywords == {
            "resveratrol", "nad+vid", "cap", "125mg", "x60", "herb",
        }

    @pytest.mark.asyncio
    async def test_upsert_replaces_stale_keywords(self) -> None:
        """Updating a product's drug_name replaces its keyword rows."""
        drug_old = _make_drug_result(
            drug_name="Losartan 50mg",
            url="https://www.farmatodo.com.ve/losartan-50",
        )
        await save_search_results("losartan", "CCS", [drug_old])

        drug_new = _make_drug_result(
            drug_name="Losartan Potasico 100mg",
            url="https://www.farmatodo.com.ve/losartan-50",  # same external_id
        )
        await save_search_results("losartan", "CCS", [drug_new])

        async with async_session() as session:
            rows = (
                await session.execute(select(ProductKeyword))
            ).scalars().all()

        keywords = {r.keyword for r in rows}
        # Old tokens "50mg" must be gone; new tokens must be present.
        assert keywords == {"losartan", "potasico", "100mg"}

    @pytest.mark.asyncio
    async def test_dedupes_repeated_tokens(self) -> None:
        """Repeated tokens in drug_name create only one row per unique keyword."""
        drug = _make_drug_result(
            drug_name="Vitamina C C C 500mg",
            url="https://www.farmatodo.com.ve/vitc",
        )
        await save_search_results("vitamina c", "CCS", [drug])

        async with async_session() as session:
            rows = (
                await session.execute(select(ProductKeyword))
            ).scalars().all()

        keywords = sorted(r.keyword for r in rows)
        # "c" should appear exactly once, not three times
        assert keywords.count("c") == 1
        assert "vitamina" in keywords
        assert "500mg" in keywords


class TestFindCrossChainMatchesIndexed:
    """Integration tests for the indexed find_cross_chain_matches (Item 30)."""

    @pytest.mark.asyncio
    async def test_finds_product_when_all_keywords_present(self) -> None:
        """Returns products that contain every query keyword."""
        drug = _make_drug_result(
            drug_name="Losartan Potasico 50mg",
            pharmacy_name="Farmacias SAAS",
            url="https://www.saas.com.ve/losartan-potasico-50",
        )
        await save_search_results("losartan", "CCS", [drug])

        matches = await find_cross_chain_matches(
            query_keywords=["losartan", "50mg"],
            city_code="CCS",
            exclude_names=set(),
        )
        assert len(matches) == 1
        assert matches[0].drug_name == "Losartan Potasico 50mg"
        assert matches[0].pharmacy_name == "Farmacias SAAS"

    @pytest.mark.asyncio
    async def test_skips_product_missing_any_keyword(self) -> None:
        """A product missing even one query keyword is excluded."""
        drug = _make_drug_result(
            drug_name="Losartan 50mg",
            url="https://www.farmatodo.com.ve/losartan-50",
        )
        await save_search_results("losartan", "CCS", [drug])

        # Product has "losartan" + "50mg" but not "100mg" — should not match.
        matches = await find_cross_chain_matches(
            query_keywords=["losartan", "100mg"],
            city_code="CCS",
            exclude_names=set(),
        )
        assert matches == []

    @pytest.mark.asyncio
    async def test_exclude_names_filters_out_duplicates(self) -> None:
        """Products whose drug_name is in exclude_names are filtered."""
        drug = _make_drug_result(
            drug_name="Losartan 50mg",
            url="https://www.farmatodo.com.ve/losartan-50",
        )
        await save_search_results("losartan", "CCS", [drug])

        matches = await find_cross_chain_matches(
            query_keywords=["losartan", "50mg"],
            city_code="CCS",
            exclude_names={"losartan 50mg"},
        )
        assert matches == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self) -> None:
        """Empty or all-blank query keywords return an empty list."""
        assert await find_cross_chain_matches([], "CCS", set()) == []
        assert await find_cross_chain_matches([""], "CCS", set()) == []

    @pytest.mark.asyncio
    async def test_returns_multiple_matches(self) -> None:
        """Multiple products sharing all keywords are all returned."""
        drug1 = _make_drug_result(
            drug_name="Ibuprofeno 400mg GenVen",
            pharmacy_name="Farmatodo",
            url="https://www.farmatodo.com.ve/ibu-400-genven",
        )
        drug2 = _make_drug_result(
            drug_name="Ibuprofeno 400mg Calox",
            pharmacy_name="Farmacias SAAS",
            url="https://www.saas.com.ve/ibu-400-calox",
        )
        await save_search_results("ibuprofeno", "CCS", [drug1, drug2])

        matches = await find_cross_chain_matches(
            query_keywords=["ibuprofeno", "400mg"],
            city_code="CCS",
            exclude_names=set(),
        )
        assert len(matches) == 2
        pharmacies = {m.pharmacy_name for m in matches}
        assert pharmacies == {"Farmatodo", "Farmacias SAAS"}
