"""FarmaGO drug search via their Odoo e-commerce shop.

FarmaGO (farmago.com.ve) runs on the Odoo platform.  Their product catalog
is publicly accessible via server-rendered HTML at ``/shop?search=...``.
No authentication is required — this uses the same publicly accessible
search that any browser visitor would use.

Venezuela-only: FarmaGO operates exclusively in Venezuela.
"""

import logging
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.utils import extract_brand, parse_ve_price

logger = logging.getLogger(__name__)

FARMAGO_BASE_URL = "https://www.farmago.com.ve"
FARMAGO_SEARCH_URL = f"{FARMAGO_BASE_URL}/shop"

# Regex to strip the barcode prefix from alt text: "[7591062013082] PRODUCT NAME"
_BARCODE_PREFIX_RE = re.compile(r"^\[\d+\]\s*")


class FarmaGOScraper(BaseScraper):
    """Search FarmaGO's product catalog via their Odoo shop pages."""

    @property
    def pharmacy_name(self) -> str:
        return "FarmaGO"

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search FarmaGO for a drug by scraping their Odoo shop.

        Args:
            query: Drug name to search.
            city: Optional city (unused — FarmaGO is Venezuela-only).
            max_results: Maximum results to return.

        Returns:
            List of matching drug results.
        """
        params = {
            "search": query,
            "ppg": str(max_results),
            "order": "name asc",
        }

        try:
            async with httpx.AsyncClient(
                timeout=float(SCRAPER_TIMEOUT), follow_redirects=True
            ) as client:
                response = await client.get(FARMAGO_SEARCH_URL, params=params)
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("FarmaGO search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "FarmaGO returned HTTP %s for query: %s",
                exc.response.status_code,
                query,
            )
            return []
        except httpx.RequestError as exc:
            logger.error("FarmaGO request failed for query %s: %s", query, exc)
            return []

        try:
            results = self._parse_html(response.text)
        except Exception as exc:
            logger.error("FarmaGO HTML parsing failed for query %s: %s", query, exc)
            return []

        logger.info(
            "FarmaGO: found %d results for '%s'",
            len(results),
            query,
        )
        return results[:max_results]

    def _parse_html(self, html: str) -> list[DrugResult]:
        """Parse Odoo shop HTML into DrugResult objects.

        Args:
            html: Raw HTML from the FarmaGO shop page.

        Returns:
            List of parsed DrugResult items.
        """
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(".oe_product_cart")
        results: list[DrugResult] = []

        for card in cards:
            try:
                result = self._card_to_result(card)
                if result:
                    results.append(result)
            except (ValueError, TypeError, AttributeError) as exc:
                logger.warning("FarmaGO: failed to parse product card: %s", exc)

        return results

    def _card_to_result(self, card: "BeautifulSoup") -> DrugResult | None:
        """Convert a single Odoo product card to a DrugResult.

        Args:
            card: BeautifulSoup element for one ``.oe_product_cart``.

        Returns:
            Parsed DrugResult or None if essential data is missing.
        """
        # Product name from image alt text (strip barcode prefix)
        img = card.select_one("img[alt]")
        if not img:
            return None
        raw_name = img.get("alt", "").strip()
        drug_name = _BARCODE_PREFIX_RE.sub("", raw_name).strip()
        if not drug_name:
            return None

        # Price from the Odoo currency element
        price_el = card.select_one(".oe_currency_value")
        price_bs = parse_ve_price(price_el.get_text(strip=True)) if price_el else None

        # Product URL
        link = card.select_one("a[href*='/shop/']")
        url = None
        if link:
            href = link.get("href", "")
            url = f"{FARMAGO_BASE_URL}{href}" if href.startswith("/") else href

        # Image URL
        img_src = img.get("src", "")
        image_url = None
        if img_src:
            image_url = (
                f"{FARMAGO_BASE_URL}{img_src}"
                if img_src.startswith("/")
                else img_src
            )

        # Extract brand from name pattern: "PRODUCT (BRAND)" or last word as brand
        brand = extract_brand(drug_name)

        return DrugResult(
            drug_name=drug_name,
            pharmacy_name=self.pharmacy_name,
            price_bs=price_bs,
            available=True,  # Listed products are assumed available
            url=url,
            last_checked=datetime.now(tz=UTC),
            requires_prescription=False,
            image_url=image_url,
            brand=brand,
            drug_class=None,  # Odoo does not expose category in search results
            stores_in_stock=0,
            stores_with_stock_ids=[],
        )

    # Price parsing and brand extraction use shared helpers:
    # parse_ve_price() and extract_brand() from scrapers.utils
