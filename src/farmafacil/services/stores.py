"""Store service — fetch nearby Farmatodo stores and cross-reference with stock."""

import logging
from dataclasses import dataclass

import httpx

from farmafacil.config import SCRAPER_TIMEOUT

logger = logging.getLogger(__name__)

FARMATODO_STORES_URL = "https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby"


@dataclass
class Store:
    """A Farmatodo store with location info."""

    id: int
    name: str
    city: str
    latitude: float
    longitude: float
    address: str
    distance_km: float


async def get_nearby_stores(
    city_code: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> list[Store]:
    """Fetch Farmatodo stores near a location.

    Args:
        city_code: Farmatodo city code (e.g., "CCS").
        latitude: Optional GPS latitude for distance sorting.
        longitude: Optional GPS longitude for distance sorting.

    Returns:
        List of nearby stores sorted by distance.
    """
    params: dict[str, str] = {"cityId": city_code}
    if latitude is not None and longitude is not None:
        params["latitude"] = str(latitude)
        params["longitude"] = str(longitude)

    try:
        async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT) as client:
            response = await client.get(
                FARMATODO_STORES_URL,
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
    except httpx.RequestError as exc:
        logger.error("Failed to fetch stores: %s", exc)
        return []

    data = response.json()
    stores_data = data.get("nearbyStores", [])

    return [
        Store(
            id=s["id"],
            name=s["name"],
            city=s.get("city", city_code),
            latitude=s["latitude"],
            longitude=s["longitude"],
            address=s.get("address", ""),
            distance_km=s.get("distanceInKm", 0),
        )
        for s in stores_data
    ]


def filter_stores_with_stock(
    nearby_stores: list[Store], stores_with_stock: list[int]
) -> list[Store]:
    """Filter nearby stores to only those that have stock for a drug.

    Args:
        nearby_stores: All stores near the user.
        stores_with_stock: Store IDs that have the drug in stock (from Algolia).

    Returns:
        Nearby stores that have the drug, sorted by distance.
    """
    stock_set = set(stores_with_stock)
    return [s for s in nearby_stores if s.id in stock_set]
