"""Drug search service — orchestrates scraping with caching and store enrichment."""

import logging

from farmafacil.models.schemas import DrugResult, NearbyStore, SearchResponse
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.farmatodo import FarmatodoScraper
from farmafacil.scrapers.saas import SAASScraper
from farmafacil.services.product_cache import get_cached_results, save_search_results
from farmafacil.services.stores import Store, filter_stores_with_stock, get_nearby_stores

logger = logging.getLogger(__name__)

ACTIVE_SCRAPERS: list[BaseScraper] = [
    FarmatodoScraper(),
    SAASScraper(),
]


async def search_drug(
    query: str,
    city: str | None = None,
    city_code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    zone_name: str | None = None,
) -> SearchResponse:
    """Search all active pharmacies for a drug, with caching.

    Checks DB cache first. On miss, queries Algolia and caches the results.
    """
    all_results: list[DrugResult] = []
    searched: list[str] = []

    # Check cache first
    cached = await get_cached_results(query, city_code)
    if cached is not None:
        all_results = cached
        searched = [f"{s.pharmacy_name} (cache)" for s in ACTIVE_SCRAPERS]
        logger.info("Serving cached results for '%s': %d items", query, len(cached))
    else:
        # Cache miss — hit scrapers
        for scraper in ACTIVE_SCRAPERS:
            logger.info("Searching %s for '%s'", scraper.pharmacy_name, query)
            searched.append(scraper.pharmacy_name)
            try:
                results = await scraper.search(query, city=city)
                all_results.extend(results)
            except Exception:
                logger.error(
                    "Scraper %s failed for query '%s'",
                    scraper.pharmacy_name,
                    query,
                    exc_info=True,
                )

        # Save to product catalog (upsert — never deletes)
        if all_results:
            await save_search_results(query, city_code, all_results)

    # Enrich with nearby store data if we have location
    if city_code and latitude and longitude:
        all_results = await _enrich_with_nearby_stores(
            all_results, city_code, latitude, longitude
        )

    return SearchResponse(
        query=query,
        city=city,
        zone=zone_name,
        results=all_results,
        total=len(all_results),
        searched_pharmacies=searched,
    )


async def _enrich_with_nearby_stores(
    results: list[DrugResult],
    city_code: str,
    latitude: float,
    longitude: float,
) -> list[DrugResult]:
    """Add nearby store info to each drug result."""
    nearby = await get_nearby_stores(city_code, latitude, longitude)
    if not nearby:
        return results

    for result in results:
        stores_near = filter_stores_with_stock(nearby, result.stores_with_stock_ids)
        result.nearby_stores = [
            NearbyStore(
                store_name=s.name,
                address=s.address,
                distance_km=s.distance_km,
                price_bs=result.price_bs,
            )
            for s in stores_near[:5]
        ]

    return results
