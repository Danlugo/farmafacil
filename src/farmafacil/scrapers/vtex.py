"""Base scraper for VTEX-powered pharmacy websites.

VTEX Intelligent Search API provides a common product search interface used
by multiple Venezuelan pharmacies (Farmacias SAAS, Locatel, etc.).  This base
class encapsulates the shared HTTP + parsing logic so that each pharmacy only
needs to provide its base URL and optional customizations.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# VTEX Intelligent Search endpoint (appended to the store's base URL)
VTEX_SEARCH_PATH = "/api/io/_v/api/intelligent-search/product_search/"


class VTEXScraper(BaseScraper):
    """Shared scraper for pharmacies running on the VTEX e-commerce platform.

    Subclasses must set ``base_url`` and ``pharmacy_name``.  Override
    ``_product_to_result`` if the store's VTEX payload has non-standard fields.
    """

    base_url: str = ""  # e.g. "https://www.farmaciasaas.com"

    @property
    def pharmacy_name(self) -> str:
        raise NotImplementedError("Subclasses must define pharmacy_name")

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search the VTEX Intelligent Search API for products.

        Args:
            query: Drug name to search.
            city: Optional city (unused by VTEX — kept for interface compat).
            max_results: Maximum results to return.

        Returns:
            List of matching DrugResult items.
        """
        if not self.base_url:
            raise NotImplementedError("Subclasses must define base_url")

        url = f"{self.base_url}{VTEX_SEARCH_PATH}"
        params = {
            "query": query,
            "_from": 0,
            "_to": max_results - 1,
        }

        try:
            async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            logger.warning(
                "%s VTEX search timed out for query: %s", self.pharmacy_name, query
            )
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "%s VTEX returned HTTP %s for query: %s",
                self.pharmacy_name,
                exc.response.status_code,
                query,
            )
            return []
        except httpx.RequestError as exc:
            logger.error(
                "%s VTEX request failed for query %s: %s",
                self.pharmacy_name,
                query,
                exc,
            )
            return []
        except (ValueError, KeyError):
            logger.warning(
                "%s VTEX returned invalid JSON for query: %s",
                self.pharmacy_name,
                query,
            )
            return []

        products = data.get("products", [])
        total = data.get("recordsFiltered", len(products))
        logger.info(
            "%s: found %d results for '%s' (total: %d)",
            self.pharmacy_name,
            len(products),
            query,
            total,
        )

        results: list[DrugResult] = []
        for product in products:
            try:
                results.append(self._product_to_result(product))
            except Exception:
                logger.warning(
                    "%s: failed to parse product %s",
                    self.pharmacy_name,
                    product.get("productId", "?"),
                    exc_info=True,
                )
        return results

    def _product_to_result(self, product: dict) -> DrugResult:
        """Convert a VTEX product dict to a DrugResult.

        Args:
            product: Raw product object from the VTEX search response.

        Returns:
            Parsed DrugResult.
        """
        # Navigate the VTEX product structure
        items = product.get("items", [])
        first_item = items[0] if items else {}

        # Image
        images = first_item.get("images", [])
        image_url = images[0].get("imageUrl") if images else None

        # Seller / pricing
        sellers = first_item.get("sellers", [])
        first_seller = sellers[0] if sellers else {}
        offer = first_seller.get("commertialOffer", {})

        price = offer.get("Price")
        list_price = offer.get("ListPrice")
        is_available = offer.get("IsAvailable", False)
        available_qty = offer.get("AvailableQuantity", 0)

        price_bs = Decimal(str(price)) if price is not None else None
        full_price_bs = (
            Decimal(str(list_price))
            if list_price is not None and list_price != price
            else None
        )

        # Discount percentage
        discount_pct = None
        if full_price_bs and price_bs and full_price_bs > price_bs:
            pct = ((full_price_bs - price_bs) / full_price_bs * 100).quantize(Decimal("1"))
            discount_pct = f"{pct}%"

        # Product URL
        link = product.get("link", "")
        product_url = f"{self.base_url}{link}" if link else None

        # Categories — take the most specific (last segment of the path)
        categories = product.get("categories", [])
        drug_class = None
        if categories:
            parts = [p for p in categories[-1].split("/") if p]
            drug_class = parts[-1] if parts else None

        return DrugResult(
            drug_name=product.get("productName", "Unknown"),
            pharmacy_name=self.pharmacy_name,
            price_bs=price_bs,
            full_price_bs=full_price_bs,
            discount_pct=discount_pct,
            available=is_available and available_qty > 0,
            url=product_url,
            last_checked=datetime.now(tz=UTC),
            requires_prescription=False,
            image_url=image_url,
            brand=product.get("brand"),
            drug_class=drug_class,
            description=(product.get("description") or "").strip() or None,
            stores_in_stock=1 if is_available else 0,
            stores_with_stock_ids=[],
        )
