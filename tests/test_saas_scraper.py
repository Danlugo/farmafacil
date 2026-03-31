"""Tests for the Farmacias SAAS VTEX-based scraper."""

from decimal import Decimal

import pytest

from farmafacil.scrapers.saas import SAASScraper
from farmafacil.scrapers.vtex import VTEXScraper


@pytest.fixture
def scraper():
    return SAASScraper()


@pytest.fixture
def sample_vtex_product():
    """A realistic VTEX product from the Farmacias SAAS Intelligent Search API."""
    return {
        "productId": "21406",
        "productName": "LOSARTAN POTA TAB 50MG X30 ALESS",
        "brand": "Aless",
        "brandId": 2000615,
        "link": "/losartan-pota-tab-50mg-x30-aless/p",
        "description": "Losartan potasico 50mg tabletas caja por 30 unidades",
        "categories": ["/Medicamentos/Cardiovascular/Antihipertensivos/"],
        "items": [
            {
                "itemId": "21406",
                "name": "LOSARTAN POTA TAB 50MG X30 ALESS",
                "images": [
                    {
                        "imageUrl": "https://farmaciasaas.vtexassets.com/arquivos/ids/123456/losartan.jpg",
                        "imageLabel": "main",
                    }
                ],
                "sellers": [
                    {
                        "sellerId": "1",
                        "sellerName": "Farmacias Unidas (Grupo Cobeca) - VE",
                        "commertialOffer": {
                            "Price": 1.42,
                            "ListPrice": 1.77,
                            "IsAvailable": True,
                            "AvailableQuantity": 10000,
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_vtex_product_no_discount():
    """A VTEX product where Price == ListPrice (no discount)."""
    return {
        "productId": "99999",
        "productName": "ACETAMINOFEN 500MG X20",
        "brand": "Generico",
        "link": "/acetaminofen-500mg-x20/p",
        "description": "",
        "categories": ["/Medicamentos/Analgesicos/"],
        "items": [
            {
                "itemId": "99999",
                "images": [
                    {
                        "imageUrl": "https://farmaciasaas.vtexassets.com/arquivos/ids/789/aceta.jpg",
                    }
                ],
                "sellers": [
                    {
                        "commertialOffer": {
                            "Price": 2.50,
                            "ListPrice": 2.50,
                            "IsAvailable": True,
                            "AvailableQuantity": 500,
                        },
                    }
                ],
            }
        ],
    }


class TestSAASScraper:
    """Test the VTEX product parsing logic."""

    def test_pharmacy_name(self, scraper):
        assert scraper.pharmacy_name == "Farmacias SAAS"

    def test_base_url(self, scraper):
        assert scraper.base_url == "https://www.farmaciasaas.com"

    def test_is_vtex_scraper(self, scraper):
        assert isinstance(scraper, VTEXScraper)

    def test_product_to_result_basic(self, scraper, sample_vtex_product):
        """Converts a VTEX product to DrugResult with correct fields."""
        result = scraper._product_to_result(sample_vtex_product)
        assert result.drug_name == "LOSARTAN POTA TAB 50MG X30 ALESS"
        assert result.pharmacy_name == "Farmacias SAAS"
        assert result.price_bs == Decimal("1.42")
        assert result.full_price_bs == Decimal("1.77")
        assert result.available is True
        assert result.brand == "Aless"
        assert result.drug_class == "Antihipertensivos"
        assert result.description == "Losartan potasico 50mg tabletas caja por 30 unidades"
        assert result.stores_in_stock == 1

    def test_product_to_result_url(self, scraper, sample_vtex_product):
        """Builds correct Farmacias SAAS product URL."""
        result = scraper._product_to_result(sample_vtex_product)
        assert result.url == "https://www.farmaciasaas.com/losartan-pota-tab-50mg-x30-aless/p"

    def test_product_to_result_image(self, scraper, sample_vtex_product):
        """Extracts image URL from VTEX items structure."""
        result = scraper._product_to_result(sample_vtex_product)
        assert "farmaciasaas.vtexassets.com" in result.image_url

    def test_product_to_result_discount(self, scraper, sample_vtex_product):
        """Calculates discount percentage from ListPrice vs Price."""
        result = scraper._product_to_result(sample_vtex_product)
        assert result.discount_pct == "20%"

    def test_product_to_result_no_discount(self, scraper, sample_vtex_product_no_discount):
        """No discount when Price equals ListPrice."""
        result = scraper._product_to_result(sample_vtex_product_no_discount)
        assert result.price_bs == Decimal("2.50")
        assert result.full_price_bs is None
        assert result.discount_pct is None

    def test_product_to_result_no_description(self, scraper, sample_vtex_product_no_discount):
        """Empty description becomes None."""
        result = scraper._product_to_result(sample_vtex_product_no_discount)
        assert result.description is None

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

    def test_product_to_result_no_categories(self, scraper):
        """Drug class is None when categories list is empty."""
        product = {
            "productId": "3",
            "productName": "No Cat Drug",
            "brand": "Test",
            "link": "/nocat/p",
            "categories": [],
            "items": [
                {
                    "images": [],
                    "sellers": [
                        {
                            "commertialOffer": {
                                "Price": 1.0,
                                "ListPrice": 1.0,
                                "IsAvailable": True,
                                "AvailableQuantity": 10,
                            },
                        }
                    ],
                }
            ],
        }
        result = scraper._product_to_result(product)
        assert result.drug_class is None


@pytest.mark.integration
class TestSAASLive:
    """Integration tests that hit the live VTEX API."""

    async def test_search_losartan(self, scraper):
        """Live search for losartan returns results."""
        results = await scraper.search("losartan", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "Farmacias SAAS"
        assert results[0].price_bs is not None
        assert results[0].drug_name

    async def test_search_no_results(self, scraper):
        """Nonsense query returns empty results."""
        results = await scraper.search("xyznonexistentdrug12345")
        assert len(results) == 0
