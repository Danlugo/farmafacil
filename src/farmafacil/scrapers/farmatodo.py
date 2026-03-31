"""Farmatodo drug search via their Algolia product index."""

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from farmafacil.config import SCRAPER_TIMEOUT
from farmafacil.models.schemas import DrugResult
from farmafacil.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

ALGOLIA_APP_ID = "VCOJEYD2PO"
ALGOLIA_API_KEY = "869a91e98550dd668b8b1dc04bca9011"
ALGOLIA_INDEX = "products-venezuela"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"

# Farmatodo city codes for geographic filtering
CITY_CODES = {
    "caracas": "CCS",
    "maracaibo": "MCBO",
    "valencia": "VAL",
    "barquisimeto": "BAR",
    "maracay": "MAT",
    "merida": "MER",
    "puerto ordaz": "PTO",
    "porlamar": "POR",
    "san cristobal": "SAC",
    "cumana": "CUA",
    "punto fijo": "PTC",
    "los teques": "LEC",
    "guarenas": "GUAC",
    "higuerote": "HIG",
    "pamatar": "PAM",
    "upata": "UPA",
    "puerto la cruz": "PDM",
    "barinas": "COR",
}


class FarmatodoScraper(BaseScraper):
    """Search Farmatodo's product catalog via their Algolia index."""

    @property
    def pharmacy_name(self) -> str:
        return "Farmatodo"

    async def search(
        self, query: str, city: str | None = None, max_results: int = 10
    ) -> list[DrugResult]:
        """Search Farmatodo for a drug via Algolia API.

        Args:
            query: Drug name to search.
            city: Optional city name for price/stock filtering.
            max_results: Maximum number of results to return.

        Returns:
            List of matching drug results.
        """
        headers = {
            "x-algolia-application-id": ALGOLIA_APP_ID,
            "x-algolia-api-key": ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "hitsPerPage": max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
                response = await client.post(
                    ALGOLIA_URL, headers=headers, json=payload
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("Farmatodo Algolia search timed out for query: %s", query)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Farmatodo Algolia returned HTTP %s for query: %s",
                exc.response.status_code,
                query,
            )
            return []
        except httpx.RequestError as exc:
            logger.error("Farmatodo Algolia request failed for query %s: %s", query, exc)
            return []

        data = response.json()
        hits = data.get("hits", [])
        logger.info(
            "Farmatodo: found %d results for '%s' (total: %d)",
            len(hits),
            query,
            data.get("nbHits", 0),
        )

        city_code = CITY_CODES.get(city.lower()) if city else None
        return [self._hit_to_result(hit, city_code) for hit in hits]

    def _hit_to_result(self, hit: dict, city_code: str | None) -> DrugResult:
        """Convert an Algolia hit to a DrugResult.

        Args:
            hit: Raw Algolia hit dictionary.
            city_code: Optional Farmatodo city code for localized pricing.

        Returns:
            Parsed DrugResult.
        """
        price_bs = self._get_price(hit, city_code)
        offer_price_bs = self._get_offer_price(hit, city_code)
        in_stock = len(hit.get("stores_with_stock", [])) > 0

        # Calculate per-unit price
        unit_count = hit.get("measurePum")
        unit_label = hit.get("labelPum")
        best_price = offer_price_bs or price_bs
        unit_price_str = None
        if unit_count and unit_count > 0 and best_price:
            unit_price = best_price / unit_count
            unit_price_str = f"{unit_label} {unit_price:.2f}" if unit_label else None

        return DrugResult(
            drug_name=hit.get("mediaDescription", hit.get("brand", "Unknown")),
            pharmacy_name=self.pharmacy_name,
            price_bs=best_price,
            full_price_bs=price_bs if offer_price_bs else None,
            discount_pct=hit.get("offerText"),
            available=in_stock,
            url=self._build_product_url(hit),
            last_checked=datetime.now(tz=UTC),
            requires_prescription=hit.get("requirePrescription") == "true",
            image_url=hit.get("mediaImageUrl"),
            brand=hit.get("marca") or hit.get("brand"),
            drug_class=hit.get("rms_class"),
            unit_label=unit_price_str,
            unit_count=unit_count,
            description=(hit.get("largeDescription") or "").strip() or None,
            stores_in_stock=len(hit.get("stores_with_stock", [])),
            stores_with_stock_ids=hit.get("stores_with_stock", []),
        )

    def _get_price(self, hit: dict, city_code: str | None) -> Decimal | None:
        """Get the full price, optionally for a specific city.

        Args:
            hit: Algolia hit dictionary.
            city_code: Optional city code for localized price.

        Returns:
            Price in Bolivares or None.
        """
        if city_code:
            for entry in hit.get("fullPriceByCity", []):
                if entry.get("cityCode") == city_code:
                    return Decimal(str(entry["fullPrice"]))
        full_price = hit.get("fullPrice")
        return Decimal(str(full_price)) if full_price else None

    def _get_offer_price(self, hit: dict, city_code: str | None) -> Decimal | None:
        """Get the offer/discount price if available.

        Args:
            hit: Algolia hit dictionary.
            city_code: Optional city code for localized offer price.

        Returns:
            Offer price in Bolivares or None.
        """
        if city_code:
            for entry in hit.get("offerPriceByCity", []):
                if entry.get("cityCode") == city_code:
                    return Decimal(str(entry["offerPrice"]))
        offer_price = hit.get("offerPrice")
        return Decimal(str(offer_price)) if offer_price else None

    def _build_product_url(self, hit: dict) -> str | None:
        """Build the product page URL from the hit's url slug.

        Args:
            hit: Algolia hit dictionary.

        Returns:
            Full product URL or None.
        """
        slug = hit.get("url")
        if slug:
            return f"https://www.farmatodo.com.ve/{slug}"
        return None
