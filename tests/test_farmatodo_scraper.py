"""Tests for the Farmatodo scraper parser."""

from decimal import Decimal

from farmafacil.scrapers.farmatodo import FarmatodoScraper


class TestFarmatodoParser:
    """Test the HTML parsing logic without making network requests."""

    def setup_method(self):
        self.scraper = FarmatodoScraper()

    def test_pharmacy_name(self):
        """Scraper reports correct pharmacy name."""
        assert self.scraper.pharmacy_name == "Farmatodo"

    def test_parse_results_empty_html(self):
        """Empty HTML returns no results."""
        results = self.scraper._parse_results("<html><body></body></html>", "test")
        assert results == []

    def test_parse_results_with_product_card(self):
        """Parser extracts drug info from a product card."""
        html = """
        <html><body>
            <div class="product-item">
                <a href="/producto/losartan-50mg">
                    <h2 class="product-name">Losartan 50mg Tabletas</h2>
                </a>
                <span class="price">$5.99</span>
            </div>
        </body></html>
        """
        results = self.scraper._parse_results(html, "losartan")
        assert len(results) == 1
        assert results[0].drug_name == "Losartan 50mg Tabletas"
        assert results[0].available is True
        assert results[0].url == "https://www.farmatodo.com.ve/producto/losartan-50mg"

    def test_parse_results_multiple_cards(self):
        """Parser handles multiple product cards."""
        html = """
        <html><body>
            <div class="product-item">
                <h3 class="product-name">Losartan 50mg</h3>
                <span class="price">$5.99</span>
            </div>
            <div class="product-item">
                <h3 class="product-name">Losartan 100mg</h3>
                <span class="price">$8.50</span>
            </div>
        </body></html>
        """
        results = self.scraper._parse_results(html, "losartan")
        assert len(results) == 2

    def test_parse_price_usd(self):
        """Price parser handles USD format."""
        assert self.scraper._parse_price("$5.99") == Decimal("5.99")

    def test_parse_price_bs(self):
        """Price parser handles Bolivares format."""
        assert self.scraper._parse_price("Bs. 150,00") == Decimal("150.00")

    def test_parse_price_empty(self):
        """Price parser returns None for empty string."""
        assert self.scraper._parse_price("") is None

    def test_parse_price_garbage(self):
        """Price parser returns None for unparsable text."""
        assert self.scraper._parse_price("Consultar") is None
