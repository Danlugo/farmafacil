"""Store service — fetch nearby Farmatodo stores and cross-reference with stock."""

import logging
import math
from dataclasses import dataclass

import httpx

from farmafacil.config import SCRAPER_TIMEOUT

logger = logging.getLogger(__name__)

FARMATODO_STORES_URL = "https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS coordinates in kilometers."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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

    Farmatodo's API ``distanceInKm`` field is unreliable — it returns
    distances from a default city center, not from the user's actual
    coordinates (e.g., TEPUY always shows 0 km for CCS regardless of
    user location).  We recalculate all distances ourselves using
    haversine and re-sort by actual distance.

    Args:
        city_code: Farmatodo city code (e.g., "CCS").
        latitude: User's GPS latitude.
        longitude: User's GPS longitude.

    Returns:
        List of nearby stores sorted by actual distance from user.
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

    stores = []
    for s in stores_data:
        store_lat = s.get("latitude", 0)
        store_lng = s.get("longitude", 0)

        # Recalculate distance from user's actual coordinates instead
        # of trusting the API's distanceInKm (which is wrong).
        if latitude is not None and longitude is not None and store_lat and store_lng:
            dist = round(_haversine_km(latitude, longitude, store_lat, store_lng), 1)
        else:
            dist = s.get("distanceInKm", 0)

        stores.append(Store(
            id=s["id"],
            name=s["name"],
            city=s.get("city", city_code),
            latitude=store_lat,
            longitude=store_lng,
            address=s.get("address", ""),
            distance_km=dist,
        ))

    # Re-sort by actual distance
    stores.sort(key=lambda x: x.distance_km)
    return stores


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
