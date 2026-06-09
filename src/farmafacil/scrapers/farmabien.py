"""FarmaBien drug search via their Next.js product catalog.

FarmaBien (farmabien.com) runs on Next.js with React Server Components.
Product data is embedded in RSC script payloads inside the HTML response.
No authentication is required — this uses the same publicly accessible
search that any browser visitor would use.

Venezuela-only: Although FarmaBien operates in Venezuela and Colombia,
this scraper only returns products from their Venezuelan catalog
(farmabien.com serves the VE market; Colombia uses a separate domain).
"""

import logging
import re
from datetime import UTC, datetime

import httpx

from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.utils import extract_brand, parse_ve_price

logger = logging.getLogger(__name__)

FARMABIEN_BASE_URL = "https://www.farmabien.com"
FARMABIEN_SEARCH_URL = f"{FARMABIEN_BASE_URL}/productos"

# Patterns to extract product data from Next.js RSC script payloads.
# Product names appear as alt attributes on image elements.
_NAME_RE = re.compile(r'"alt"\s*:\s*"([^"]{10,150})"')
# Prices appear as "Bs.S X,XXX.XX" — Venezuelan Bolívar Soberano
_PRICE_RE = re.compile(r"Bs\.S\s+([\d.,]+)")
# Product IDs appear in URLs like /productos/8010195
_ID_RE = re.compile(r"/productos/(\d{7,8})")
# Product images from FarmaBien CDN (media directory only, skip UI assets)
_IMAGE_RE = re.compile(r'"src"\s*:\s*"(https://cdn\.farmabien\.com/web/media/[^"]+)"')

# Alt texts to filter out (UI elements, not products)
_NOISE_ALTS = frozenset({"Flying cart"})


class FarmaBienScraper(BaseScraper):
    """Search FarmaBien's product catalog via their Next.js site."""

    @property
    def pharmacy_name(self) -> str:
        return "FarmaBien"

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search FarmaBien by scraping their Next.js product pages.

        Args:
            query: Drug name to search.
            city: Optional city (unused — FarmaBien VE catalog is national).
            max_results: Maximum results to return.

        Returns:
            List of matching drug results.
        """
        params = {"term": query}

        try:
            async with httpx.AsyncClient(
                timeout=float(SCRAPER_TIMEOUT), follow_redirects=True
            ) as client:
                response = await client.get(
                    FARMABIEN_SEARCH_URL,
                    params=params,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("FarmaBien search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "FarmaBien returned HTTP %s for query: %s",
                exc.response.status_code,
                query,
            )
            return []
        except httpx.RequestError as exc:
            logger.error("FarmaBien request failed for query %s: %s", query, exc)
            return []

        try:
            results = self._parse_rsc(response.text)
        except Exception as exc:
            logger.error("FarmaBien RSC parsing failed for query %s: %s", query, exc)
            return []

        logger.info(
            "FarmaBien: found %d results for '%s'",
            len(results),
            query,
        )
        return results[:max_results]

    def _parse_rsc(self, html: str) -> list[DrugResult]:
        """Parse Next.js RSC payloads embedded in the HTML.

        The RSC format embeds product data in ``self.__next_f.push(...)``
        script blocks.  Product names appear as ``alt`` attributes, prices
        as ``Bs.S X,XXX.XX``, and IDs in ``/productos/NNNNNNN`` URLs.

        Args:
            html: Raw HTML from the FarmaBien search page.

        Returns:
            List of parsed DrugResult items.
        """
        # Unescape the double-escaped JSON in RSC payloads
        text = html.replace('\\"', '"')

        # Extract parallel arrays of product data
        raw_names = _NAME_RE.findall(text)
        raw_prices = _PRICE_RE.findall(text)
        raw_ids = list(dict.fromkeys(_ID_RE.findall(text)))  # dedup, keep order
        raw_images = _IMAGE_RE.findall(text)

        # Filter out UI noise from alt texts
        names = [n for n in raw_names if n not in _NOISE_ALTS and "farmabien" not in n.lower()]

        # FarmaBien renders 2 price elements per product (original + display)
        # Take every other price starting from the first for the display price
        prices = raw_prices[::2] if len(raw_prices) >= 2 * len(raw_ids) else raw_prices

        results: list[DrugResult] = []
        for i, product_id in enumerate(raw_ids):
            name = names[i] if i < len(names) else None
            if not name:
                continue

            price_bs = parse_ve_price(prices[i]) if i < len(prices) else None
            image_url = raw_images[i] if i < len(raw_images) else None
            url = f"{FARMABIEN_BASE_URL}/productos/{product_id}"

            # Extract brand from name pattern: "PRODUCT (BRAND)" or last word
            brand = extract_brand(name)

            results.append(
                DrugResult(
                    drug_name=name,
                    pharmacy_name=self.pharmacy_name,
                    price_bs=price_bs,
                    available=True,  # Listed products are assumed available
                    url=url,
                    last_checked=datetime.now(tz=UTC),
                    requires_prescription=False,
                    image_url=image_url,
                    brand=brand,
                    drug_class=None,
                    stores_in_stock=0,
                    stores_with_stock_ids=[],
                )
            )

        return results

    # Price parsing and brand extraction use shared helpers:
    # parse_ve_price() and extract_brand() from scrapers.utils
