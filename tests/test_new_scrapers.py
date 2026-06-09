"""Tests for the three new pharmacy scrapers (Item 129, v0.49.0).

FarmaGO (Odoo HTML), FarmaBien (Next.js RSC), Farmarket (PHP table).
"""

from decimal import Decimal

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from farmafacil.scrapers.farmago import FarmaGOScraper
from farmafacil.scrapers.farmabien import FarmaBienScraper
from farmafacil.scrapers.farmarket import FarmarketScraper
from farmafacil.scrapers.utils import extract_brand, parse_ve_price


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def farmago():
    return FarmaGOScraper()


@pytest.fixture
def farmabien():
    return FarmaBienScraper()


@pytest.fixture
def farmarket():
    return FarmarketScraper()


# ── Sample HTML payloads ────────────────────────────────────────────────

FARMAGO_HTML = """
<div class="o_wsale_products_grid_table_wrapper">
  <div class="oe_product_cart">
    <a href="/shop/7591062902553-brugesic-forte-ibuprofeno-800mg-54076?search=ibuprofeno">
      <img src="/web/image/product.template/54076/image_512/test.png"
           alt="[7591062902553] BRUGESIC FORTE (IBUPROFENO) 800MG X 4 COMPRIMIDOS (ELMOR)"/>
    </a>
    <span class="oe_currency_value">30,08</span>
  </div>
  <div class="oe_product_cart">
    <a href="/shop/7591062000150-brugesic-forte-ibuprofeno-800-mg-x-10-54075?search=ibuprofeno">
      <img src="/web/image/product.template/54075/image_512/test2.png"
           alt="[7591062000150] BRUGESIC FORTE (IBUPROFENO) 800 MG X 10 COMPRIMIDO (ELMOR)"/>
    </a>
    <span class="oe_currency_value">5.114,82</span>
  </div>
  <div class="oe_product_cart">
    <a href="/shop/no-barcode-product-99999?search=ibuprofeno">
      <img src="/web/image/product.template/99999/image_512/test3.png"
           alt="IBUPROFENO GENERICO 400MG X 10"/>
    </a>
    <span class="oe_currency_value">1.200,50</span>
  </div>
</div>
"""

FARMABIEN_RSC_HTML = """<!DOCTYPE html><html><head><title>test</title></head><body>
<script>self.__next_f.push([1,"17:[\\"$\\",\\"div\\",null,{\\"className\\":\\"max-w-7xl\\",\\"children\\":[[\\"$\\",\\"div\\",null,{\\"className\\":\\"grid grid-cols-2\\",\\"children\\":[[\\"$\\",\\"div\\",\\"8010200\\",{\\"className\\":\\"rounded-lg\\",\\"children\\":[[\\"$\\",\\"div\\",null,{\\"children\\":[[\\"$\\",\\"$L28\\",null,{\\"href\\":\\"/productos/8010200\\",\\"children\\":[\\"$\\",\\"div\\",null,{\\"children\\":[\\"$\\",\\"$L49\\",null,{\\"src\\":\\"https://cdn.farmabien.com/web/media/202403/file_test1.png\\",\\"alt\\":\\"IBUPROFENO 600MG X 10COMP (GENVEN)\\"}]}]}]]}],[\\"$\\",\\"div\\",null,{\\"children\\":[[\\"$\\",\\"span\\",null,{\\"children\\":[\\"Bs.S 2.179,90\\"]}],[\\"$\\",\\"$L28\\",null,{\\"className\\":\\"mt-auto\\",\\"href\\":\\"https://wa.me/Bs.S 2.179,90\\"}]]}]]}],[\\"$\\",\\"div\\",\\"8011625\\",{\\"className\\":\\"rounded-lg\\",\\"children\\":[[\\"$\\",\\"div\\",null,{\\"children\\":[[\\"$\\",\\"$L28\\",null,{\\"href\\":\\"/productos/8011625\\",\\"children\\":[\\"$\\",\\"div\\",null,{\\"children\\":[\\"$\\",\\"$L49\\",null,{\\"src\\":\\"https://cdn.farmabien.com/web/media/202403/file_test2.png\\",\\"alt\\":\\"IBUPROFENO 400MG X 10 COMP GENVEN\\"}]}]}]]}],[\\"$\\",\\"div\\",null,{\\"children\\":[[\\"$\\",\\"span\\",null,{\\"children\\":[\\"Bs.S 1.407,85\\"]}],[\\"$\\",\\"$L28\\",null,{\\"className\\":\\"mt-auto\\",\\"href\\":\\"https://wa.me/Bs.S 1.407,85\\"}]]}]]}]]}]]}]"])</script>
</body></html>"""

FARMARKET_HTML = """
<html><body>
<table>
<tr><td colspan="3">Se consiguieron 4 productos coincidentes</td></tr>
<tr><td colspan="3">ADVERTENCIA: Estos resultados NO GARANTIZAN existencia</td></tr>
<tr>
  <td>Sede: Altamira | Fecha: 09-06-2026 10:28AM<br/>Telefono: 0212-555-1234</td>
  <td>Nombre del Producto</td>
  <td>Principio Activo</td>
  <td>Existencia</td>
</tr>
<tr><td>IBUPROFENO GENVEN 600MG 10COMP</td><td>IBUPROFENO</td><td>2</td></tr>
<tr><td>BRUGESIC FORTE 800MG 10COMP</td><td>IBUPROFENO</td><td>3</td></tr>
<tr>
  <td>Sede: Chacaito | Fecha: 09-06-2026 10:28AM<br/>Telefono: 0212-555-5678</td>
  <td>Nombre del Producto</td>
  <td>Principio Activo</td>
  <td>Existencia</td>
</tr>
<tr><td>IBUPROFENO GENVEN 600MG 10COMP</td><td>IBUPROFENO</td><td>5</td></tr>
<tr><td>ANAPIR 400MG 10CAP</td><td>IBUPROFENO</td><td>1</td></tr>
</table>
</body></html>
"""


# ══════════════════════════════════════════════════════════════════════════
# FarmaGO Tests
# ══════════════════════════════════════════════════════════════════════════

class TestFarmaGOScraper:
    """Test FarmaGO Odoo HTML parsing."""

    def test_pharmacy_name(self, farmago):
        assert farmago.pharmacy_name == "FarmaGO"

    def test_parse_html_basic(self, farmago):
        """Parses product cards with names, prices, URLs, and images."""
        results = farmago._parse_html(FARMAGO_HTML)
        assert len(results) == 3

        r = results[0]
        assert r.drug_name == "BRUGESIC FORTE (IBUPROFENO) 800MG X 4 COMPRIMIDOS (ELMOR)"
        assert r.pharmacy_name == "FarmaGO"
        assert r.price_bs == Decimal("30.08")
        assert r.brand == "ELMOR"
        assert r.available is True
        assert "/shop/" in r.url
        assert r.image_url is not None

    def test_parse_html_large_price(self, farmago):
        """Parses Venezuelan-format price with dot thousands separator."""
        results = farmago._parse_html(FARMAGO_HTML)
        assert results[1].price_bs == Decimal("5114.82")

    def test_barcode_prefix_stripped(self, farmago):
        """Strips [BARCODE] prefix from product names."""
        results = farmago._parse_html(FARMAGO_HTML)
        assert not results[0].drug_name.startswith("[")
        assert "BRUGESIC" in results[0].drug_name

    def test_no_barcode_prefix(self, farmago):
        """Products without barcode prefix parse correctly."""
        results = farmago._parse_html(FARMAGO_HTML)
        assert results[2].drug_name == "IBUPROFENO GENERICO 400MG X 10"
        assert results[2].brand is None  # no (BRAND) suffix

    def test_empty_html(self, farmago):
        """Empty HTML returns empty list."""
        assert farmago._parse_html("<html></html>") == []

    def test_no_product_cards(self, farmago):
        """HTML without product cards returns empty list."""
        assert farmago._parse_html("<div>No results</div>") == []

    @pytest.mark.parametrize("price_text,expected", [
        ("30,08", Decimal("30.08")),
        ("5.114,82", Decimal("5114.82")),
        ("1.200,50", Decimal("1200.50")),
        ("100", Decimal("100")),
        ("", None),
        ("abc", None),
    ], ids=["small", "thousands", "medium", "integer", "empty", "garbage"])
    def test_parse_price(self, price_text, expected):
        """Venezuelan price format parsing."""
        assert parse_ve_price(price_text) == expected

    @pytest.mark.parametrize("name,expected_brand", [
        ("IBUPROFENO 400MG X 10 (ELMOR)", "ELMOR"),
        ("IBUPROFENO 400MG X 10 (FC PHARMA)", "FC PHARMA"),
        ("IBUPROFENO GENERICO 400MG X 10", None),
        ("(BRAND ONLY)", "BRAND ONLY"),
    ], ids=["single_word", "two_words", "no_brand", "brand_only"])
    def test_extract_brand(self, name, expected_brand):
        """Brand extraction from parenthesized suffix."""
        assert extract_brand(name) == expected_brand

    async def test_search_timeout(self, farmago):
        """Timeout returns empty list, no crash."""

        with patch("farmafacil.scrapers.farmago.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await farmago.search("ibuprofeno")
            assert results == []

    async def test_search_http_error(self, farmago):
        """HTTP error returns empty list, no crash."""

        with patch("farmafacil.scrapers.farmago.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status_code = 500
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=AsyncMock(), response=mock_response
            )
            instance.get = AsyncMock(return_value=mock_response)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await farmago.search("ibuprofeno")
            assert results == []


# ══════════════════════════════════════════════════════════════════════════
# FarmaBien Tests
# ══════════════════════════════════════════════════════════════════════════

class TestFarmaBienScraper:
    """Test FarmaBien Next.js RSC parsing."""

    def test_pharmacy_name(self, farmabien):
        assert farmabien.pharmacy_name == "FarmaBien"

    def test_parse_rsc_basic(self, farmabien):
        """Parses RSC payloads into products with names, prices, URLs."""
        results = farmabien._parse_rsc(FARMABIEN_RSC_HTML)
        assert len(results) == 2

        r = results[0]
        assert r.drug_name == "IBUPROFENO 600MG X 10COMP (GENVEN)"
        assert r.pharmacy_name == "FarmaBien"
        assert r.price_bs == Decimal("2179.90")
        assert r.brand == "GENVEN"
        assert r.url == "https://www.farmabien.com/productos/8010200"
        assert r.available is True

    def test_parse_rsc_second_product(self, farmabien):
        """Second product in RSC parses correctly."""
        results = farmabien._parse_rsc(FARMABIEN_RSC_HTML)
        r = results[1]
        assert r.drug_name == "IBUPROFENO 400MG X 10 COMP GENVEN"
        assert r.price_bs == Decimal("1407.85")
        assert r.brand is None  # no parenthesized suffix
        assert r.url == "https://www.farmabien.com/productos/8011625"

    def test_parse_rsc_images(self, farmabien):
        """Product images from CDN are extracted."""
        results = farmabien._parse_rsc(FARMABIEN_RSC_HTML)
        assert results[0].image_url == "https://cdn.farmabien.com/web/media/202403/file_test1.png"
        assert results[1].image_url == "https://cdn.farmabien.com/web/media/202403/file_test2.png"

    def test_parse_rsc_empty(self, farmabien):
        """Empty HTML returns empty list."""
        assert farmabien._parse_rsc("<html></html>") == []

    def test_parse_rsc_no_products(self, farmabien):
        """HTML without RSC product data returns empty list."""
        html = '<script>self.__next_f.push([1,"some other data"])</script>'
        assert farmabien._parse_rsc(html) == []

    def test_filters_flying_cart(self, farmabien):
        """Filters out 'Flying cart' UI alt text noise."""
        html = """<script>self.__next_f.push([1,"X:[\\"alt\\":\\"Flying cart\\"]"])</script>"""
        results = farmabien._parse_rsc(html)
        assert all(r.drug_name != "Flying cart" for r in results)

    @pytest.mark.parametrize("price_text,expected", [
        ("3.474,22", Decimal("3474.22")),
        ("471,18", Decimal("471.18")),
        ("1.407,85", Decimal("1407.85")),
        ("", None),
        ("abc", None),
    ], ids=["thousands", "hundreds", "medium", "empty", "garbage"])
    def test_parse_price(self, price_text, expected):
        """Venezuelan Bs.S price parsing."""
        assert parse_ve_price(price_text) == expected

    async def test_search_timeout(self, farmabien):
        """Timeout returns empty list."""

        with patch("farmafacil.scrapers.farmabien.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await farmabien.search("ibuprofeno")
            assert results == []


# ══════════════════════════════════════════════════════════════════════════
# Farmarket Tests
# ══════════════════════════════════════════════════════════════════════════

class TestFarmarketScraper:
    """Test Farmarket PHP stock table parsing."""

    def test_pharmacy_name(self, farmarket):
        assert farmarket.pharmacy_name == "Farmarket"

    def test_parse_html_aggregation(self, farmarket):
        """Products across stores are aggregated by name."""
        results = farmarket._parse_html(FARMARKET_HTML)
        # IBUPROFENO GENVEN appears in Altamira (2) and Chacaito (5) = 2 stores
        # BRUGESIC FORTE appears in Altamira only = 1 store
        # ANAPIR appears in Chacaito only = 1 store
        assert len(results) == 3

    def test_parse_html_multi_store_product(self, farmarket):
        """Product in multiple stores has correct aggregated stock."""
        results = farmarket._parse_html(FARMARKET_HTML)
        # Find IBUPROFENO GENVEN — should be first (most stores)
        ibu = next(r for r in results if "IBUPROFENO GENVEN" in r.drug_name)
        assert ibu.stores_in_stock == 2
        assert ibu.available is True
        assert ibu.price_bs is None  # Farmarket has no prices
        assert "7 unidades" in ibu.description
        assert "2 tiendas" in ibu.description

    def test_parse_html_single_store_product(self, farmarket):
        """Product in one store has correct data."""
        results = farmarket._parse_html(FARMARKET_HTML)
        anapir = next(r for r in results if "ANAPIR" in r.drug_name)
        assert anapir.stores_in_stock == 1
        assert anapir.drug_class == "IBUPROFENO"  # active ingredient
        assert anapir.pharmacy_name == "Farmarket"

    def test_parse_html_sorted_by_stores(self, farmarket):
        """Results are sorted by number of stores (most first)."""
        results = farmarket._parse_html(FARMARKET_HTML)
        store_counts = [r.stores_in_stock for r in results]
        assert store_counts == sorted(store_counts, reverse=True)

    def test_parse_html_no_prices(self, farmarket):
        """All results have price_bs=None (Farmarket doesn't show prices)."""
        results = farmarket._parse_html(FARMARKET_HTML)
        assert all(r.price_bs is None for r in results)

    def test_parse_html_empty(self, farmarket):
        """Empty HTML returns empty list."""
        assert farmarket._parse_html("<html></html>") == []

    def test_parse_html_no_table(self, farmarket):
        """HTML without table returns empty list."""
        assert farmarket._parse_html("<div>No results</div>") == []

    def test_parse_html_skips_header_rows(self, farmarket):
        """Header rows (Nombre del Producto, Sede:, etc.) are skipped."""
        results = farmarket._parse_html(FARMARKET_HTML)
        names = [r.drug_name for r in results]
        assert "Nombre del Producto" not in names
        assert not any("Sede:" in n for n in names)

    async def test_search_timeout(self, farmarket):
        """Timeout returns empty list."""

        with patch("farmafacil.scrapers.farmarket.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            results = await farmarket.search("ibuprofeno")
            assert results == []


# ══════════════════════════════════════════════════════════════════════════
# Search Service Registration Tests
# ══════════════════════════════════════════════════════════════════════════

class TestSearchServiceRegistration:
    """Verify all 6 scrapers are registered in the search service."""

    def test_all_scrapers_registered(self):
        """ACTIVE_SCRAPERS contains all 6 pharmacy scrapers."""
        from farmafacil.services.search import ACTIVE_SCRAPERS

        names = {s.pharmacy_name for s in ACTIVE_SCRAPERS}
        assert names == {
            "Farmatodo",
            "Farmacias SAAS",
            "Locatel",
            "FarmaGO",
            "FarmaBien",
            "Farmarket",
        }

    def test_scraper_count(self):
        """6 scrapers are active."""
        from farmafacil.services.search import ACTIVE_SCRAPERS

        assert len(ACTIVE_SCRAPERS) == 6


# ══════════════════════════════════════════════════════════════════════════
# Integration Tests (require network)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestNewScrapersLive:
    """Integration tests that hit the live pharmacy APIs."""

    async def test_farmago_live_search(self, farmago):
        """FarmaGO live search returns results with prices."""
        results = await farmago.search("ibuprofeno", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "FarmaGO"
        assert results[0].price_bs is not None

    async def test_farmabien_live_search(self, farmabien):
        """FarmaBien live search returns results with prices."""
        results = await farmabien.search("ibuprofeno", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "FarmaBien"
        assert results[0].price_bs is not None

    async def test_farmarket_live_search(self, farmarket):
        """Farmarket live search returns stock data (no prices)."""
        results = await farmarket.search("ibuprofeno", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "Farmarket"
        assert results[0].price_bs is None  # Farmarket has no prices
        assert results[0].stores_in_stock > 0
