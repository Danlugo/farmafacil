"""Drug search service — orchestrates scraping with caching and store enrichment.

Supports specific product filtering: when a user searches for an exact product
(with dosage, count, brand), results are filtered to that exact product. Similar
products are counted and offered via "ver similares".
"""

import logging
import re

from farmafacil.models.schemas import DrugResult, NearbyStore, SearchResponse
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.farmatodo import FarmatodoScraper
from farmafacil.scrapers.saas import SAASScraper
from farmafacil.services.product_cache import (
    find_cached_products,
    get_cached_results,
    save_search_results,
)
from farmafacil.services.stores import Store, filter_stores_with_stock, get_nearby_stores

logger = logging.getLogger(__name__)

ACTIVE_SCRAPERS: list[BaseScraper] = [
    FarmatodoScraper(),
    SAASScraper(),
]

# Patterns that indicate a specific product query (dosage, form, count, brand)
_SPECIFIC_PATTERNS = [
    r"\d+\s*mg\b",       # 125mg, 500 mg
    r"\d+\s*ml\b",       # 100ml
    r"\d+\s*g\b",        # 10g
    r"\bx\s*\d+",        # x60, x 30
    r"\bcap\b",          # capsulas
    r"\btab\b",          # tabletas
    r"\bcomp\b",         # comprimidos
    r"\bsol\b",          # solucion
    r"\bjbe\b",          # jarabe
    r"\bcaja\b",         # caja
    r"\bfrasco\b",       # frasco
    r"\bsobre\b",       # sobre
]


def is_specific_query(query: str) -> bool:
    """Detect if a query targets a specific product (with dosage, form, count).

    A specific query contains indicators like dosage (125mg), unit count (x60),
    or pharmaceutical form (cap, tab, comp). Generic queries like "losartan"
    or "acetaminofen" do not match.

    Args:
        query: Drug search query text.

    Returns:
        True if the query appears to target a specific product variant.
    """
    q = query.lower()
    return any(re.search(p, q) for p in _SPECIFIC_PATTERNS)


def is_product_match(query: str, drug_name: str) -> bool:
    """Check if a drug_name matches a specific product query.

    Uses strict case-insensitive string equality. When a user types an exact
    product name like "RESVERATROL NAD+VID CAP 125MG X60 HERB", only products
    with that exact name match. Different pharmacy chains that list the same
    product under a different name are treated as similar products.

    Args:
        query: The user's search query.
        drug_name: The product name from the pharmacy.

    Returns:
        True if the drug_name matches the query exactly (case-insensitive).
    """
    return query.lower().strip() == drug_name.lower().strip()


def filter_exact_results(
    results: list[DrugResult], query: str
) -> tuple[list[DrugResult], list[DrugResult]]:
    """Split results into exact matches and similar products.

    Uses strict case-insensitive string matching. Only products whose
    drug_name exactly matches the query are considered exact matches.
    Everything else is a similar product shown via "ver similares".

    Args:
        results: All drug search results.
        query: The user's search query.

    Returns:
        Tuple of (exact_matches, similar_products).
    """
    exact: list[DrugResult] = []
    similar: list[DrugResult] = []

    for r in results:
        if is_product_match(query, r.drug_name):
            exact.append(r)
        else:
            similar.append(r)

    return exact, similar


async def search_drug(
    query: str,
    city: str | None = None,
    city_code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    zone_name: str | None = None,
    show_all: bool = False,
) -> SearchResponse:
    """Search all active pharmacies for a drug, with caching.

    For specific queries (containing dosage, form, count), filters results
    to the exact product and reports how many similar products exist.
    Use show_all=True to skip filtering (for "ver similares").

    Args:
        query: Drug name or product name to search for.
        city: City name (optional).
        city_code: Farmatodo city code for localized pricing.
        latitude: User's latitude for store distance calculation.
        longitude: User's longitude for store distance calculation.
        zone_name: User's zone name for display.
        show_all: If True, skip exact-match filtering (show all results).

    Returns:
        SearchResponse with results, total count, and similar_count.
    """
    all_results: list[DrugResult] = []
    searched: list[str] = []
    specific = is_specific_query(query) and not show_all

    # 1. Check search_queries cache (exact query string match)
    cached = await get_cached_results(query, city_code)
    if cached is not None:
        all_results = cached
        searched = [f"{s.pharmacy_name} (cache)" for s in ACTIVE_SCRAPERS]
        logger.info("Serving cached results for '%s': %d items", query, len(cached))
    else:
        # 2. For specific queries, try the product catalog (DB-first)
        if specific:
            catalog_results = await find_cached_products(query, city_code)
            if catalog_results:
                all_results = catalog_results
                searched = [f"{s.pharmacy_name} (catalogo)" for s in ACTIVE_SCRAPERS]
                logger.info(
                    "Serving catalog products for '%s': %d items", query, len(catalog_results)
                )

        # 3. Cache miss + no catalog hit — hit scrapers
        if not all_results:
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

    # 4. For specific queries, filter to exact matches
    similar_count = 0
    if specific and all_results:
        exact, similar = filter_exact_results(all_results, query)
        if exact:
            similar_count = len(similar)
            all_results = exact
            logger.info(
                "Filtered to %d exact matches for '%s' (%d similar)",
                len(exact), query, similar_count,
            )
        else:
            # No exact match — keep all results so user still sees something
            logger.info(
                "No exact match for '%s' — showing all %d results",
                query, len(all_results),
            )

    return SearchResponse(
        query=query,
        city=city,
        zone=zone_name,
        results=all_results,
        total=len(all_results),
        searched_pharmacies=searched,
        similar_count=similar_count,
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
