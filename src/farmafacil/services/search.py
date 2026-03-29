"""Drug search service — orchestrates scraping across pharmacies."""

import logging

from farmafacil.models.schemas import DrugResult, SearchResponse
from farmafacil.scrapers.base import BaseScraper
from farmafacil.scrapers.farmatodo import FarmatodoScraper

logger = logging.getLogger(__name__)

# Registry of active scrapers
ACTIVE_SCRAPERS: list[BaseScraper] = [
    FarmatodoScraper(),
]


async def search_drug(query: str) -> SearchResponse:
    """Search all active pharmacies for a drug.

    Args:
        query: Drug name to search for.

    Returns:
        Aggregated search results from all pharmacies.
    """
    all_results: list[DrugResult] = []
    searched: list[str] = []

    for scraper in ACTIVE_SCRAPERS:
        logger.info("Searching %s for '%s'", scraper.pharmacy_name, query)
        searched.append(scraper.pharmacy_name)
        try:
            results = await scraper.search(query)
            all_results.extend(results)
        except Exception:
            logger.error(
                "Scraper %s failed for query '%s'",
                scraper.pharmacy_name,
                query,
                exc_info=True,
            )

    return SearchResponse(
        query=query,
        results=all_results,
        total=len(all_results),
        searched_pharmacies=searched,
    )
