"""Tests for the Farmatodo Algolia-based scraper."""

from decimal import Decimal

import pytest

from farmafacil.scrapers.farmatodo import FarmatodoScraper


@pytest.fixture
def scraper():
    return FarmatodoScraper()


@pytest.fixture
def sample_algolia_hit():
    """A realistic Algolia hit from the products-venezuela index."""
    return {
        "mediaDescription": "Losartán Potásico 50 mg Genven Caja x 30 Comprimidos",
        "brand": "Genven",
        "fullPrice": 920,
        "offerPrice": 782,
        "fullPriceByCity": [
            {"cityCode": "CCS", "fullPrice": 920},
            {"cityCode": "VAL", "fullPrice": 920},
        ],
        "offerPriceByCity": [
            {"cityCode": "VAL", "offerPrice": 644},
        ],
        "stores_with_stock": [100, 101, 102, 113, 125],
        "stores_with_low_stock": [],
        "url": "111408922-losartan-potasico-50mg-30-comprimidos",
        "requirePrescription": "true",
        "mediaImageUrl": "https://lh3.googleusercontent.com/example",
        "rms_class": "ANTIHIPERTENSIVOS",
    }


class TestAlgoliaConfig:
    """Test that Algolia credentials come from config, not hardcoded."""

    def test_credentials_imported_from_config(self):
        """Algolia constants are imported from config module."""
        from farmafacil import config
        from farmafacil.scrapers import farmatodo

        # Verify the scraper module uses config values (not its own hardcoded ones)
        assert farmatodo.ALGOLIA_APP_ID is config.ALGOLIA_APP_ID
        assert farmatodo.ALGOLIA_API_KEY is config.ALGOLIA_API_KEY
        assert farmatodo.ALGOLIA_INDEX is config.ALGOLIA_INDEX

    def test_algolia_url_uses_config_values(self):
        """ALGOLIA_URL is built from config values."""
        from farmafacil.config import ALGOLIA_APP_ID, ALGOLIA_INDEX
        from farmafacil.scrapers.farmatodo import ALGOLIA_URL

        assert ALGOLIA_APP_ID in ALGOLIA_URL
        assert ALGOLIA_INDEX in ALGOLIA_URL
        assert ALGOLIA_URL.startswith(f"https://{ALGOLIA_APP_ID}")

    def test_config_has_defaults(self):
        """Config provides sensible defaults for Algolia credentials."""
        from farmafacil.config import ALGOLIA_API_KEY, ALGOLIA_APP_ID, ALGOLIA_INDEX

        assert ALGOLIA_APP_ID  # not empty
        assert ALGOLIA_API_KEY  # not empty
        assert ALGOLIA_INDEX  # not empty


class TestFarmatodoScraper:
    """Test the Algolia hit parsing logic."""

    def test_pharmacy_name(self, scraper):
        assert scraper.pharmacy_name == "Farmatodo"

    def test_hit_to_result_basic(self, scraper, sample_algolia_hit):
        """Converts an Algolia hit to DrugResult with correct fields."""
        result = scraper._hit_to_result(sample_algolia_hit, city_code=None)
        assert result.drug_name == "Losartán Potásico 50 mg Genven Caja x 30 Comprimidos"
        assert result.pharmacy_name == "Farmatodo"
        assert result.price_bs == Decimal("782")  # offer price takes priority
        assert result.available is True
        assert result.requires_prescription is True
        assert result.brand == "Genven"
        assert result.drug_class == "ANTIHIPERTENSIVOS"
        assert result.stores_in_stock == 5
        assert "111408922" in result.url

    def test_hit_to_result_city_price(self, scraper, sample_algolia_hit):
        """Returns city-specific offer price when city code matches."""
        result = scraper._hit_to_result(sample_algolia_hit, city_code="VAL")
        assert result.price_bs == Decimal("644")  # Valencia offer price

    def test_hit_to_result_city_no_offer(self, scraper, sample_algolia_hit):
        """Falls back to global offer price when no city-specific offer."""
        result = scraper._hit_to_result(sample_algolia_hit, city_code="CCS")
        # CCS has no city-specific offer, but global offerPrice (782) still applies
        assert result.price_bs == Decimal("782")

    def test_hit_to_result_no_stock(self, scraper):
        """Marks as unavailable when no stores have stock."""
        hit = {
            "mediaDescription": "Test Drug",
            "brand": "TestBrand",
            "fullPrice": 100,
            "stores_with_stock": [],
            "url": "test-drug",
        }
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.available is False
        assert result.stores_in_stock == 0

    def test_hit_to_result_no_offer(self, scraper):
        """Uses full price when no offer price exists."""
        hit = {
            "mediaDescription": "Test Drug",
            "brand": "TestBrand",
            "fullPrice": 500,
            "stores_with_stock": [1],
            "url": "test",
        }
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.price_bs == Decimal("500")

    def test_hit_to_result_measurePum_float_no_crash(self, scraper):
        """Regression (v0.12.4): measurePum can come back as a float from
        Algolia (e.g., 60.0 instead of 60) — this used to crash the scraper
        with TypeError: unsupported operand type(s) for /: 'Decimal' and 'float'
        at _hit_to_result line 122, making the whole scrape fail and faking
        a Farmatodo connection error in the user's view.
        """
        hit = {
            "mediaDescription": "Omega 3 Gomitas x 60",
            "brand": "Sundown",
            "fullPrice": 1200,
            "stores_with_stock": [1],
            "url": "omega-3-gomitas",
            "measurePum": 60.0,   # float — the bug trigger
            "labelPum": "c/u",
        }
        # Must not raise
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.price_bs == Decimal("1200")
        assert result.unit_count == 60
        # per-unit price formatted correctly: 1200 / 60 = 20.00
        assert result.unit_label == "c/u 20.00"

    def test_hit_to_result_measurePum_int(self, scraper):
        """measurePum as int (the common case) still works."""
        hit = {
            "mediaDescription": "Aspirin x 30",
            "brand": "Bayer",
            "fullPrice": 600,
            "stores_with_stock": [1],
            "url": "aspirin",
            "measurePum": 30,
            "labelPum": "x tableta",
        }
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.unit_count == 30
        assert result.unit_label == "x tableta 20.00"

    def test_hit_to_result_measurePum_missing(self, scraper):
        """No measurePum field — no per-unit price, no crash."""
        hit = {
            "mediaDescription": "No Unit Info",
            "brand": "TestBrand",
            "fullPrice": 500,
            "stores_with_stock": [1],
            "url": "test",
        }
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.unit_count is None
        assert result.unit_label is None

    def test_hit_to_result_measurePum_zero(self, scraper):
        """measurePum = 0 should not trigger a ZeroDivisionError."""
        hit = {
            "mediaDescription": "Zero Unit",
            "brand": "TestBrand",
            "fullPrice": 500,
            "stores_with_stock": [1],
            "url": "test",
            "measurePum": 0,
            "labelPum": "c/u",
        }
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.unit_count == 0
        assert result.unit_label is None  # skipped because not > 0

    def test_hit_to_result_measurePum_garbage_string(self, scraper):
        """Non-numeric measurePum falls back gracefully."""
        hit = {
            "mediaDescription": "Bad Unit",
            "brand": "TestBrand",
            "fullPrice": 500,
            "stores_with_stock": [1],
            "url": "test",
            "measurePum": "not a number",
            "labelPum": "c/u",
        }
        # Must not raise
        result = scraper._hit_to_result(hit, city_code=None)
        assert result.unit_count is None
        assert result.unit_label is None

    def test_get_price_with_city(self, scraper, sample_algolia_hit):
        """Extracts city-specific price."""
        price = scraper._get_price(sample_algolia_hit, "CCS")
        assert price == Decimal("920")

    def test_get_price_no_city(self, scraper, sample_algolia_hit):
        """Falls back to global price when no city specified."""
        price = scraper._get_price(sample_algolia_hit, None)
        assert price == Decimal("920")

    def test_build_product_url(self, scraper, sample_algolia_hit):
        """Builds correct Farmatodo product URL."""
        url = scraper._build_product_url(sample_algolia_hit)
        assert url == "https://www.farmatodo.com.ve/111408922-losartan-potasico-50mg-30-comprimidos"

    def test_build_product_url_missing(self, scraper):
        """Returns None when hit has no url field."""
        assert scraper._build_product_url({}) is None


@pytest.mark.integration
class TestFarmatodoLive:
    """Integration tests that hit the live Algolia API."""

    async def test_search_losartan(self, scraper):
        """Live search for losartan returns results."""
        results = await scraper.search("losartan", max_results=3)
        assert len(results) > 0
        assert results[0].pharmacy_name == "Farmatodo"
        assert results[0].price_bs is not None
        assert results[0].drug_name  # not empty

    async def test_search_with_city(self, scraper):
        """Live search with city filter works."""
        results = await scraper.search("acetaminofen", city="caracas", max_results=3)
        assert len(results) > 0

    async def test_search_no_results(self, scraper):
        """Nonsense query returns empty results."""
        results = await scraper.search("xyznonexistentdrug12345")
        assert len(results) == 0
