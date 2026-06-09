"""Tests for delivery-only pharmacy display (Item 131).

Verifies that delivery-only pharmacies (FarmaGO) show a delivery label
and product URL instead of nearby-store info in search results.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from farmafacil.models.schemas import DrugResult, NearbyStore, SearchResponse
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.farmabien import FarmaBienScraper
from farmafacil.scrapers.farmago import FarmaGOScraper
from farmafacil.scrapers.farmarket import FarmarketScraper
from farmafacil.scrapers.farmatodo import FarmatodoScraper
from farmafacil.scrapers.locatel import LocatelScraper
from farmafacil.scrapers.saas import SAASScraper


# ---------------------------------------------------------------------------
# Scraper property tests
# ---------------------------------------------------------------------------
class TestScraperDeliveryFlag:
    """Test is_delivery_only property on scrapers."""

    @pytest.mark.parametrize(
        "scraper_cls,expected",
        [
            (FarmaGOScraper, True),
            (FarmatodoScraper, False),
            (FarmaBienScraper, False),
            (FarmarketScraper, False),
            (LocatelScraper, False),
            (SAASScraper, False),
        ],
        ids=[
            "farmago_delivery",
            "farmatodo_physical",
            "farmabien_physical",
            "farmarket_physical",
            "locatel_physical",
            "saas_physical",
        ],
    )
    def test_is_delivery_only(self, scraper_cls, expected):
        scraper = scraper_cls()
        assert scraper.is_delivery_only is expected

    def test_base_scraper_default_is_false(self):
        """Concrete subclass without override inherits False."""

        class DummyScraper(BaseScraper):
            @property
            def pharmacy_name(self) -> str:
                return "Dummy"

            async def search(self, query, city=None, max_results=10):
                return []

        assert DummyScraper().is_delivery_only is False


# ---------------------------------------------------------------------------
# DrugResult schema test
# ---------------------------------------------------------------------------
class TestDrugResultDeliveryField:
    """Test is_delivery_only field on DrugResult."""

    def test_default_is_false(self):
        r = DrugResult(drug_name="Test", pharmacy_name="X", available=True)
        assert r.is_delivery_only is False

    def test_can_be_set_true(self):
        r = DrugResult(
            drug_name="Test", pharmacy_name="X", available=True, is_delivery_only=True
        )
        assert r.is_delivery_only is True


# ---------------------------------------------------------------------------
# Formatter tests — text message
# ---------------------------------------------------------------------------
class TestFormatSearchResultsDelivery:
    """Test delivery-only display in format_search_results."""

    def _make_result(self, **kwargs) -> DrugResult:
        defaults = dict(
            drug_name="Losartan 100mg",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1558.52"),
            available=True,
            is_delivery_only=True,
            url="https://www.farmago.com.ve/shop/losartan",
        )
        defaults.update(kwargs)
        return DrugResult(**defaults)

    def _make_response(self, results, query="losartan") -> SearchResponse:
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            searched_pharmacies=["FarmaGO"],
        )

    def test_delivery_result_shows_delivery_label(self):
        from farmafacil.bot.formatter import format_search_results

        resp = self._make_response([self._make_result()])
        text = format_search_results(resp)
        assert "\U0001f6f5 Delivery" in text

    def test_delivery_result_shows_url(self):
        from farmafacil.bot.formatter import format_search_results

        resp = self._make_response([self._make_result()])
        text = format_search_results(resp)
        assert "farmago.com.ve/shop/losartan" in text
        assert "\U0001f517" in text

    def test_delivery_result_no_store_line(self):
        from farmafacil.bot.formatter import format_search_results

        resp = self._make_response([self._make_result()])
        text = format_search_results(resp)
        assert "\U0001f4cd" not in text  # No pin emoji
        assert "Cercana" not in text

    def test_delivery_no_url_skips_link_line(self):
        from farmafacil.bot.formatter import format_search_results

        resp = self._make_response([self._make_result(url=None)])
        text = format_search_results(resp)
        assert "\U0001f6f5 Delivery" in text
        assert "\U0001f517" not in text

    def test_physical_pharmacy_not_affected(self):
        """Non-delivery results still show stores, not delivery label."""
        from farmafacil.bot.formatter import format_search_results

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="Farmatodo",
            price_bs=Decimal("2000.00"),
            available=True,
            is_delivery_only=False,
            nearby_stores=[
                NearbyStore(
                    store_name="Farmatodo Altamira",
                    address="Av Principal",
                    distance_km=1.5,
                )
            ],
        )
        resp = self._make_response([result])
        text = format_search_results(resp)
        assert "\U0001f4cd Farmatodo Altamira" in text
        assert "Delivery" not in text

    def test_mixed_delivery_and_physical(self):
        """Same product from delivery + physical pharmacies."""
        from farmafacil.bot.formatter import format_search_results

        delivery = self._make_result()
        physical = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="Farmatodo",
            price_bs=Decimal("2000.00"),
            available=True,
            is_delivery_only=False,
            nearby_stores=[
                NearbyStore(
                    store_name="Farmatodo Centro",
                    address="Av Baralt",
                    distance_km=2.0,
                )
            ],
        )
        resp = self._make_response([delivery, physical])
        text = format_search_results(resp)
        assert "\U0001f6f5 Delivery" in text
        assert "\U0001f4cd Farmatodo Centro" in text


# ---------------------------------------------------------------------------
# Image caption tests
# ---------------------------------------------------------------------------
class TestBuildProductCaptionDelivery:
    """Test delivery-only display in _build_product_caption."""

    def test_delivery_caption_shows_label(self):
        from farmafacil.bot.handler import _build_product_caption

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1558.52"),
            available=True,
            is_delivery_only=True,
            url="https://www.farmago.com.ve/shop/losartan",
        )
        caption = _build_product_caption(result)
        assert "\U0001f6f5 *Delivery*" in caption
        assert "farmago.com.ve/shop/losartan" in caption
        assert "*Ver en:*" in caption

    def test_delivery_caption_no_cercana(self):
        from farmafacil.bot.handler import _build_product_caption

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1558.52"),
            available=True,
            is_delivery_only=True,
            url="https://www.farmago.com.ve/shop/losartan",
        )
        caption = _build_product_caption(result)
        assert "Cercana" not in caption

    def test_delivery_caption_no_url(self):
        from farmafacil.bot.handler import _build_product_caption

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1558.52"),
            available=True,
            is_delivery_only=True,
            url=None,
        )
        caption = _build_product_caption(result)
        assert "*Delivery*" in caption
        assert "Ver en" not in caption

    def test_physical_caption_unchanged(self):
        from farmafacil.bot.handler import _build_product_caption

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="Farmatodo",
            price_bs=Decimal("2000.00"),
            available=True,
            is_delivery_only=False,
            nearby_stores=[
                NearbyStore(
                    store_name="Farmatodo Chacao",
                    address="Av Libertador",
                    distance_km=0.8,
                )
            ],
        )
        caption = _build_product_caption(result)
        assert "Cercana" in caption
        assert "Delivery" not in caption


# ---------------------------------------------------------------------------
# Search enrichment — delivery results skip store lookup
# ---------------------------------------------------------------------------
class TestEnrichSkipsDelivery:
    """Delivery-only results should not trigger store DB queries."""

    @pytest.mark.asyncio
    async def test_delivery_results_skip_store_lookup(self):
        from farmafacil.services.search import _enrich_with_nearby_stores

        delivery_result = DrugResult(
            drug_name="Losartan",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1000"),
            available=True,
            is_delivery_only=True,
        )

        with patch(
            "farmafacil.services.search.get_nearby_stores",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "farmafacil.services.search.get_nearby_chain_stores",
            new_callable=AsyncMock,
        ) as mock_chain:
            results = await _enrich_with_nearby_stores(
                [delivery_result], "CCS", 10.5, -66.9
            )

        # Should NOT have called get_nearby_chain_stores for FarmaGO
        mock_chain.assert_not_called()
        assert results[0].nearby_stores == []

    @pytest.mark.asyncio
    async def test_mixed_results_only_enrich_physical(self):
        from farmafacil.services.search import _enrich_with_nearby_stores

        delivery_result = DrugResult(
            drug_name="Losartan",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("1000"),
            available=True,
            is_delivery_only=True,
        )
        physical_result = DrugResult(
            drug_name="Losartan",
            pharmacy_name="Farmacias SAAS",
            price_bs=Decimal("1500"),
            available=True,
            is_delivery_only=False,
        )
        mock_store = NearbyStore(
            store_name="SAAS Centro",
            address="Av Main",
            distance_km=1.0,
        )

        with patch(
            "farmafacil.services.search.get_nearby_stores",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "farmafacil.services.search.get_nearby_chain_stores",
            new_callable=AsyncMock,
            return_value=[mock_store],
        ) as mock_chain:
            results = await _enrich_with_nearby_stores(
                [delivery_result, physical_result], "CCS", 10.5, -66.9
            )

        # FarmaGO: no stores
        assert results[0].nearby_stores == []
        # SAAS: enriched
        assert len(results[1].nearby_stores) == 1
        assert results[1].nearby_stores[0].store_name == "SAAS Centro"
        # Only called for SAAS, not FarmaGO
        mock_chain.assert_called_once_with("Farmacias SAAS", 10.5, -66.9)


# ---------------------------------------------------------------------------
# Cache round-trip — delivery flag survives product_cache reconstruction
# ---------------------------------------------------------------------------
class TestCacheRoundTripDeliveryFlag:
    """Verify is_delivery_only is set when reconstructing from cache."""

    def test_product_to_drug_result_sets_delivery_for_farmago(self):
        """FarmaGO products get is_delivery_only=True from DELIVERY_ONLY_CHAINS."""
        from farmafacil.services.product_cache import _product_to_drug_result

        product = MagicMock()
        product.drug_name = "Losartan 100mg"
        product.pharmacy_chain = "FarmaGO"
        product.product_url = "https://www.farmago.com.ve/shop/losartan"
        product.requires_prescription = False
        product.image_url = None
        product.brand = "GENVEN"
        product.drug_class = None
        product.unit_count = None
        product.unit_label = None
        product.description = None
        product.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
        product.created_at = datetime(2025, 1, 1, tzinfo=UTC)

        result = _product_to_drug_result(product, None)
        assert result.is_delivery_only is True

    def test_product_to_drug_result_false_for_farmatodo(self):
        """Non-delivery chain gets is_delivery_only=False."""
        from farmafacil.services.product_cache import _product_to_drug_result

        product = MagicMock()
        product.drug_name = "Losartan 100mg"
        product.pharmacy_chain = "Farmatodo"
        product.product_url = "https://farmatodo.com.ve/losartan"
        product.requires_prescription = False
        product.image_url = None
        product.brand = None
        product.drug_class = None
        product.unit_count = None
        product.unit_label = None
        product.description = None
        product.updated_at = datetime(2025, 1, 1, tzinfo=UTC)
        product.created_at = datetime(2025, 1, 1, tzinfo=UTC)

        result = _product_to_drug_result(product, None)
        assert result.is_delivery_only is False


# ---------------------------------------------------------------------------
# Edge case — delivery + zero price should NOT produce duplicate URL
# ---------------------------------------------------------------------------
class TestDeliveryCaptionZeroPriceNoDuplicateUrl:
    """When a delivery-only product has price_bs=0, the URL should appear once."""

    def test_zero_price_delivery_caption_single_url(self):
        from farmafacil.bot.handler import _build_product_caption

        result = DrugResult(
            drug_name="Losartan 100mg",
            pharmacy_name="FarmaGO",
            price_bs=Decimal("0"),
            available=True,
            is_delivery_only=True,
            url="https://www.farmago.com.ve/shop/losartan",
        )
        caption = _build_product_caption(result)
        # URL should appear exactly once (in the delivery block, not the price-zero block)
        url_count = caption.count("farmago.com.ve/shop/losartan")
        assert url_count == 1, f"URL appeared {url_count} times in caption: {caption}"
        assert "*Delivery*" in caption
