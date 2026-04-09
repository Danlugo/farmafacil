"""Tests for the Locatel VTEX-based scraper."""

from decimal import Decimal

import pytest

from farmafacil.scrapers.locatel import LocatelScraper
from farmafacil.scrapers.vtex import VTEXScraper


@pytest.fixture
def scraper():
    return LocatelScraper()


@pytest.fixture
def sample_locatel_product():
    """A realistic VTEX product from the Locatel Intelligent Search API."""
    return {
        "productId": "2048836",
        "productName": "BAYER ASPIRINA 100MG X 28 TABLETAS",
        "brand": "BAYER",
        "brandId": 2000575,
        "link": "/bayer_aspirina_100mg_x_28_tabletas/p",
        "description": "Detener o Reducir La Inflamación",
        "categories": ["/Farmacia/MEDICAMENTOS/MEDICAMENTOS/", "/Farmacia/MEDICAMENTOS/", "/Farmacia/"],
        "items": [
            {
                "itemId": "2048836",
                "name": "BAYER ASPIRINA 100MG X 28 TABLETAS",
                "images": [
                    {
                        "imageUrl": "https://locatel.vtexassets.com/arquivos/ids/123456/aspirina.jpg",
                        "imageLabel": "main",
                    }
                ],
                "sellers": [
                    {
                        "sellerId": "1",
                        "sellerName": "Locatel",
                        "commertialOffer": {
                            "Price": 2622.06,
                            "ListPrice": 3277.58,
                            "IsAvailable": True,
                            "AvailableQuantity": 10000,
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_locatel_product_no_discount():
    """A Locatel product where Price == ListPrice (no discount)."""
    return {
        "productId": "9999",
        "productName": "ACETAMINOFEN 500MG X20 GENERICO",
        "brand": "Generico",
        "link": "/acetaminofen-500mg-x20/p",
        "description": "",
        "categories": ["/Farmacia/MEDICAMENTOS/"],
        "items": [
            {
                "itemId": "9999",
                "images": [
                    {
                        "imageUrl": "https://locatel.vtexassets.com/arquivos/ids/789/aceta.jpg",
                    }
                ],
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 1500.00,
                            "ListPrice": 1500.00,
                            "IsAvailable": True,
                            "AvailableQuantity": 500,
                        },
                    }
                ],
            }
        ],
    }


class TestLocatelScraper:
    """Test the Locatel VTEX product parsing logic."""

    def test_pharmacy_name(self, scraper):
        assert scraper.pharmacy_name == "Locatel"

    def test_base_url(self, scraper):
        assert scraper.base_url == "https://www.locatel.com.ve"

    def test_is_vtex_scraper(self, scraper):
        assert isinstance(scraper, VTEXScraper)

    def test_product_to_result_basic(self, scraper, sample_locatel_product):
        """Converts a VTEX product to DrugResult with correct fields."""
        result = scraper._product_to_result(sample_locatel_product)
        assert result.drug_name == "BAYER ASPIRINA 100MG X 28 TABLETAS"
        assert result.pharmacy_name == "Locatel"
        assert result.price_bs == Decimal("2622.06")
        assert result.full_price_bs == Decimal("3277.58")
        assert result.available is True
        assert result.brand == "BAYER"
        assert result.stores_in_stock == 1

    def test_product_to_result_url(self, scraper, sample_locatel_product):
        """Builds correct Locatel product URL."""
        result = scraper._product_to_result(sample_locatel_product)
        assert result.url == "https://www.locatel.com.ve/bayer_aspirina_100mg_x_28_tabletas/p"

    def test_product_to_result_image(self, scraper, sample_locatel_product):
        """Extracts image URL from VTEX items structure."""
        result = scraper._product_to_result(sample_locatel_product)
        assert "locatel.vtexassets.com" in result.image_url

    def test_product_to_result_discount(self, scraper, sample_locatel_product):
        """Calculates discount percentage from ListPrice vs Price."""
        result = scraper._product_to_result(sample_locatel_product)
        assert result.discount_pct == "20%"

    def test_product_to_result_no_discount(self, scraper, sample_locatel_product_no_discount):
        """No discount when Price equals ListPrice."""
        result = scraper._product_to_result(sample_locatel_product_no_discount)
        assert result.price_bs == Decimal("1500.00")
        assert result.full_price_bs is None
        assert result.discount_pct is None

    def test_product_to_result_no_description(self, scraper, sample_locatel_product_no_discount):
        """Empty description becomes None."""
        result = scraper._product_to_result(sample_locatel_product_no_discount)
        assert result.description is None

    def test_product_to_result_categories(self, scraper, sample_locatel_product):
        """Extracts last category segment as drug_class."""
        result = scraper._product_to_result(sample_locatel_product)
        # VTEX base takes categories[-1] → "/Farmacia/" → "Farmacia"
        assert result.drug_class == "Farmacia"

    def test_product_to_result_unavailable(self, scraper):
        """Marks as unavailable when IsAvailable is False."""
        product = {
            "productId": "1",
            "productName": "Out of Stock Drug",
            "brand": "Test",
            "link": "/oos/p",
            "categories": [],
            "items": [
                {
                    "images": [],
                    "sellers": [
                        {
                            "commertialOffer": {
                                "Price": 5.00,
                                "ListPrice": 5.00,
                                "IsAvailable": False,
                                "AvailableQuantity": 0,
                            },
                        }
                    ],
                }
            ],
        }
        result = scraper._product_to_result(product)
        assert result.available is False
        assert result.stores_in_stock == 0

    def test_product_to_result_no_items(self, scraper):
        """Handles product with empty items list gracefully."""
        product = {
            "productId": "2",
            "productName": "Empty Drug",
            "brand": "Test",
            "link": "/empty/p",
            "categories": [],
            "items": [],
        }
        result = scraper._product_to_result(product)
        assert result.drug_name == "Empty Drug"
        assert result.price_bs is None
        assert result.image_url is None
        assert result.available is False


class TestLocatelRegistered:
    """Test that Locatel is registered in ACTIVE_SCRAPERS."""

    def test_locatel_in_active_scrapers(self):
        from farmafacil.services.search import ACTIVE_SCRAPERS
        names = [s.pharmacy_name for s in ACTIVE_SCRAPERS]
        assert "Locatel" in names

    def test_three_scrapers_active(self):
        from farmafacil.services.search import ACTIVE_SCRAPERS
        assert len(ACTIVE_SCRAPERS) == 3


@pytest.mark.integration
class TestLocatelLive:
    """Integration tests that hit the live VTEX API."""

    async def test_search_aspirina(self, scraper):
        """Live search for aspirina returns results."""
        results = await scraper.search("aspirina", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "Locatel"
        assert results[0].price_bs is not None
        assert results[0].drug_name

    async def test_search_no_results(self, scraper):
        """Nonsense query returns empty results."""
        results = await scraper.search("xyznonexistentdrug12345")
        assert len(results) == 0
