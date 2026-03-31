"""Product cache — store Algolia search results in DB with admin-editable TTL."""

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import ProductCache
from farmafacil.models.schemas import DrugResult
from farmafacil.services.settings import get_setting_int

logger = logging.getLogger(__name__)


async def get_cached_results(query: str, city_code: str | None) -> list[DrugResult] | None:
    """Check if we have fresh cached results for this query.

    Args:
        query: Drug search query.
        city_code: Optional city code.

    Returns:
        List of DrugResult if cache hit, None if miss or expired.
    """
    ttl_minutes = await get_setting_int("cache_ttl_minutes")
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=ttl_minutes)

    async with async_session() as session:
        result = await session.execute(
            select(ProductCache).where(
                ProductCache.query == query.lower().strip(),
                ProductCache.city_code == city_code,
                ProductCache.cached_at >= cutoff.replace(tzinfo=None),
            ).order_by(ProductCache.cached_at.desc()).limit(1)
        )
        cached = result.scalar_one_or_none()

        if cached is None:
            return None

        logger.info("Cache hit for '%s' (city=%s)", query, city_code)

        return [DrugResult(**item) for item in json.loads(cached.results_json)]


async def save_cached_results(
    query: str, city_code: str | None, results: list[DrugResult]
) -> None:
    """Save search results to cache.

    Args:
        query: Drug search query.
        city_code: Optional city code.
        results: Drug results to cache.
    """
    results_json = json.dumps(
        [r.model_dump(mode="json") for r in results],
        default=str,
    )

    async with async_session() as session:
        session.add(ProductCache(
            query=query.lower().strip(),
            city_code=city_code,
            results_json=results_json,
            result_count=len(results),
        ))
        await session.commit()

    logger.info("Cached %d results for '%s' (city=%s)", len(results), query, city_code)
