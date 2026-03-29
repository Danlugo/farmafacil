"""Farmatodo.com.ve scraper for drug availability and pricing."""

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from farmafacil.config import SCRAPER_TIMEOUT, SCRAPER_USER_AGENT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

FARMATODO_SEARCH_URL = "https://www.farmatodo.com.ve/buscar?q={query}"


class FarmatodoScraper(BaseScraper):
    """Scraper for Farmatodo Venezuela website."""

    @property
    def pharmacy_name(self) -> str:
        return "Farmatodo"

    async def search(self, query: str) -> list[DrugResult]:
        """Search Farmatodo for a drug.

        Args:
            query: Drug name to search.

        Returns:
            List of matching drug results.
        """
        url = FARMATODO_SEARCH_URL.format(query=query)
        headers = {"User-Agent": SCRAPER_USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
                response = await client.get(url, headers=headers, follow_redirects=True)
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("Farmatodo search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Farmatodo returned HTTP %s for query: %s", exc.response.status_code, query
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Farmatodo request failed for query %s: %s", query, exc)
            return []

        return self._parse_results(response.text, query)

    def _parse_results(self, html: str, query: str) -> list[DrugResult]:
        """Parse Farmatodo search results HTML into DrugResult objects.

        Args:
            html: Raw HTML from the search page.
            query: Original search query for logging.

        Returns:
            Parsed drug results.
        """
        soup = BeautifulSoup(html, "lxml")
        results: list[DrugResult] = []

        # Farmatodo uses product cards — selectors may need updating
        # as the site changes. These are initial best-guess selectors.
        product_cards = soup.select(".product-item, .product-card, [data-product]")

        if not product_cards:
            logger.info("No product cards found for query: %s", query)
            # Try broader search for any product-like structure
            product_cards = soup.select("[class*='product'], [class*='Product']")

        for card in product_cards:
            try:
                name_el = card.select_one(
                    ".product-name, .product-title, h2, h3, [class*='name'], [class*='title']"
                )
                price_el = card.select_one(
                    ".price, .product-price, [class*='price'], [class*='Price']"
                )

                if not name_el:
                    continue

                drug_name = name_el.get_text(strip=True)
                price = self._parse_price(price_el.get_text(strip=True)) if price_el else None

                # Try to get product URL
                link_el = card.select_one("a[href]")
                product_url = None
                if link_el and link_el.get("href"):
                    href = link_el["href"]
                    if href.startswith("/"):
                        product_url = f"https://www.farmatodo.com.ve{href}"
                    elif href.startswith("http"):
                        product_url = href

                results.append(
                    DrugResult(
                        drug_name=drug_name,
                        pharmacy_name=self.pharmacy_name,
                        price=price,
                        available=True,
                        url=product_url,
                        last_checked=datetime.now(tz=UTC),
                    )
                )
            except Exception:
                logger.warning("Failed to parse a product card for query: %s", query, exc_info=True)
                continue

        logger.info("Farmatodo: found %d results for '%s'", len(results), query)
        return results

    def _parse_price(self, price_text: str) -> Decimal | None:
        """Extract a numeric price from text like '$5.99' or 'Bs. 150,00'.

        Args:
            price_text: Raw price string from the page.

        Returns:
            Decimal price or None if unparsable.
        """
        if not price_text:
            return None

        cleaned = price_text.replace("$", "").replace("Bs.", "").replace("Bs", "")
        cleaned = cleaned.replace(",", ".").strip()
        # Take only digits and dots
        cleaned = "".join(c for c in cleaned if c.isdigit() or c == ".")

        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None
