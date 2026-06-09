"""Farmarket drug search via their PHP stock checker.

Farmarket (sitio.farmarket.com.ve) is a Venezuelan pharmacy chain with 10+
locations in Caracas.  Their stock search is a simple PHP form that returns
per-store inventory counts (product name, active ingredient, stock quantity)
grouped by store location.

**Key limitation:** Farmarket does NOT expose prices — only stock availability.
Products returned from this scraper have ``price_bs=None``.  This is still
valuable: users can confirm a drug is in stock nearby before visiting.

Venezuela-only: All Farmarket locations are in Caracas, Venezuela.
"""

import logging
import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

FARMARKET_SEARCH_URL = "https://sitio.farmarket.com.ve/busca.php"


class FarmarketScraper(BaseScraper):
    """Search Farmarket's stock inventory via their PHP form.

    Returns per-store stock counts.  Results are aggregated: each unique
    product appears once with ``stores_in_stock`` set to the number of
    Farmarket locations that carry it.  No pricing data is available.
    """

    @property
    def pharmacy_name(self) -> str:
        return "Farmarket"

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search Farmarket for drug stock availability.

        Args:
            query: Drug name to search.
            city: Optional city (unused — all stores are in Caracas).
            max_results: Maximum results to return.

        Returns:
            List of matching drug results (no pricing, stock counts only).
        """
        try:
            async with httpx.AsyncClient(
                timeout=float(SCRAPER_TIMEOUT), follow_redirects=True
            ) as client:
                response = await client.post(
                    FARMARKET_SEARCH_URL,
                    data={"txtProducto": query},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("Farmarket search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Farmarket returned HTTP %s for query: %s",
                exc.response.status_code,
                query,
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Farmarket request failed for query %s: %s", query, exc)
            return []

        try:
            results = self._parse_html(response.text)
        except Exception as exc:
            logger.error("Farmarket HTML parsing failed for query %s: %s", query, exc)
            return []

        logger.info(
            "Farmarket: found %d results for '%s'",
            len(results),
            query,
        )
        return results[:max_results]

    def _parse_html(self, html: str) -> list[DrugResult]:
        """Parse the Farmarket stock table into aggregated DrugResult objects.

        The HTML contains a single ``<table>`` with rows grouped by store.
        Each store section starts with a header row (store name + phone),
        followed by product rows with: name, active ingredient, stock count.

        Products appearing in multiple stores are aggregated into a single
        DrugResult with ``stores_in_stock`` reflecting how many locations
        carry it.

        Args:
            html: Raw HTML from the Farmarket search response.

        Returns:
            List of aggregated DrugResult items, sorted by number of stores.
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if not table:
            return []

        # Aggregate: product_key → {name, ingredient, total_stock, store_count}
        aggregated: dict[str, dict] = {}
        rows = table.find_all("tr")

        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            # Store header rows have a single cell (or a cell with "Sede:")
            # Product rows have 3 cells: name, active ingredient, stock count
            if len(cells) < 3:
                continue

            texts = [c.get_text(strip=True) for c in cells]
            # Skip header-like rows
            if texts[0] in ("Nombre del Producto", "") or "Sede:" in texts[0]:
                continue

            product_name = texts[0].strip()
            active_ingredient = texts[1].strip()
            stock_text = texts[2].strip()

            if not product_name:
                continue

            # Parse stock count
            stock = 0
            stock_match = re.match(r"(\d+)", stock_text)
            if stock_match:
                stock = int(stock_match.group(1))

            # Aggregate by normalized product name (collapse extra whitespace)
            key = " ".join(product_name.lower().split())
            if key in aggregated:
                aggregated[key]["total_stock"] += stock
                aggregated[key]["store_count"] += 1
            else:
                aggregated[key] = {
                    "name": product_name,
                    "ingredient": active_ingredient,
                    "total_stock": stock,
                    "store_count": 1,
                }

        # Convert to DrugResult, sorted by most stores first
        results: list[DrugResult] = []
        for entry in sorted(
            aggregated.values(), key=lambda e: e["store_count"], reverse=True
        ):
            results.append(
                DrugResult(
                    drug_name=entry["name"],
                    pharmacy_name=self.pharmacy_name,
                    price_bs=None,  # Farmarket does not expose prices
                    available=entry["total_stock"] > 0,
                    url=None,  # No product pages
                    last_checked=datetime.now(tz=UTC),
                    requires_prescription=False,
                    image_url=None,
                    brand=None,
                    drug_class=entry["ingredient"] or None,
                    description=f"Stock: {entry['total_stock']} unidades en {entry['store_count']} tiendas",
                    stores_in_stock=entry["store_count"],
                    stores_with_stock_ids=[],
                )
            )

        return results
