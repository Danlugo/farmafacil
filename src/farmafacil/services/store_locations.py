"""Store location service — query pharmacy_locations DB for nearby stores.

Used for pharmacy chains (like Farmacias SAAS) that don't provide per-product
store stock data. Shows all nearby stores of the chain sorted by distance.

Also provides a combined "all nearby stores" query for the nearest_store
feature — returns stores across ALL chains sorted by distance.
"""

import logging
import math

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation
from farmafacil.models.schemas import NearbyStore

logger = logging.getLogger(__name__)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS coordinates in kilometers.

    Args:
        lat1: Latitude of point 1.
        lon1: Longitude of point 1.
        lat2: Latitude of point 2.
        lon2: Longitude of point 2.

    Returns:
        Distance in kilometers.
    """
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def get_nearby_chain_stores(
    pharmacy_chain: str,
    latitude: float,
    longitude: float,
    max_stores: int = 3,
    max_distance_km: float = 50.0,
) -> list[NearbyStore]:
    """Get nearest stores of a pharmacy chain from the DB.

    Queries pharmacy_locations table for active stores of the given chain,
    calculates distance from the user, and returns the closest ones.

    Args:
        pharmacy_chain: Name of the pharmacy chain (e.g., "Farmacias SAAS").
        latitude: User's GPS latitude.
        longitude: User's GPS longitude.
        max_stores: Maximum number of stores to return.
        max_distance_km: Maximum distance in km to include a store.

    Returns:
        List of NearbyStore sorted by distance.
    """
    async with async_session() as session:
        result = await session.execute(
            select(PharmacyLocation).where(
                PharmacyLocation.pharmacy_chain == pharmacy_chain,
                PharmacyLocation.is_active.is_(True),
                PharmacyLocation.latitude.isnot(None),
                PharmacyLocation.longitude.isnot(None),
            )
        )
        locations = result.scalars().all()

    if not locations:
        return []

    # Calculate distances and sort
    stores_with_distance: list[tuple[PharmacyLocation, float]] = []
    for loc in locations:
        dist = _haversine_km(latitude, longitude, loc.latitude, loc.longitude)
        if dist <= max_distance_km:
            stores_with_distance.append((loc, dist))

    stores_with_distance.sort(key=lambda x: x[1])

    return [
        NearbyStore(
            store_name=loc.name,
            address=loc.address or "",
            distance_km=round(dist, 1),
            price_bs=None,
        )
        for loc, dist in stores_with_distance[:max_stores]
    ]


async def get_all_nearby_stores(
    latitude: float,
    longitude: float,
    max_stores: int = 5,
    max_distance_km: float = 30.0,
) -> list[dict]:
    """Get nearest pharmacy stores across ALL chains, sorted by distance.

    Returns a flat list of stores from any chain, ordered by proximity
    to the user. Used for "nearest pharmacy" queries without a product.

    Args:
        latitude: User's GPS latitude.
        longitude: User's GPS longitude.
        max_stores: Maximum number of stores to return.
        max_distance_km: Maximum distance in km to include a store.

    Returns:
        List of dicts with store_name, address, distance_km, pharmacy_chain.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.is_active.is_(True),
                    PharmacyLocation.latitude.isnot(None),
                    PharmacyLocation.longitude.isnot(None),
                )
            )
            locations = result.scalars().all()
    except SQLAlchemyError:
        logger.error("Failed to query pharmacy locations", exc_info=True)
        return []

    if not locations:
        return []

    stores_with_distance: list[tuple[PharmacyLocation, float]] = []
    for loc in locations:
        dist = _haversine_km(latitude, longitude, loc.latitude, loc.longitude)
        if dist <= max_distance_km:
            stores_with_distance.append((loc, dist))

    stores_with_distance.sort(key=lambda x: x[1])

    return [
        {
            "store_name": loc.name,
            "address": loc.address or "",
            "distance_km": round(dist, 1),
            "pharmacy_chain": loc.pharmacy_chain,
        }
        for loc, dist in stores_with_distance[:max_stores]
    ]
