"""Backfill pharmacy store locations from their APIs into our database."""

import logging

import httpx
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation

logger = logging.getLogger(__name__)

FARMATODO_STORES_API = "https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby"

# Farmatodo city codes with center coordinates for store discovery
FARMATODO_CITIES: dict[str, tuple[float, float]] = {
    "CCS": (10.48, -66.86), "MCBO": (10.64, -71.61), "VAL": (10.16, -67.99),
    "BAR": (10.07, -69.35), "MAT": (10.24, -67.59), "MER": (8.59, -71.16),
    "PTO": (8.29, -62.71), "SAC": (7.77, -72.23), "PDM": (10.21, -64.63),
    "POR": (11.00, -63.85), "PTC": (11.69, -70.21), "GUAC": (10.47, -66.62),
    "LEC": (10.35, -66.98), "COR": (8.63, -70.21), "CUA": (10.45, -64.17),
    "PAM": (11.00, -63.85), "HIG": (10.07, -66.10), "UPA": (8.00, -62.40),
}


async def backfill_stores() -> int:
    """Fetch all Farmatodo store locations and upsert into pharmacy_locations.

    Returns:
        Number of new stores inserted.
    """
    all_stores: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        for city_code, (lat, lng) in FARMATODO_CITIES.items():
            try:
                resp = await client.get(
                    FARMATODO_STORES_API,
                    params={"cityId": city_code, "latitude": str(lat), "longitude": str(lng)},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()
                data = resp.json()
                for store in data.get("nearbyStores", []):
                    all_stores[store["id"]] = store
            except Exception:
                logger.warning("Failed to fetch Farmatodo stores for %s", city_code)

    logger.info("Fetched %d Farmatodo stores from API", len(all_stores))

    inserted = 0
    async with async_session() as session:
        for store_id, store_data in all_stores.items():
            ext_id = str(store_id)
            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.external_id == ext_id,
                    PharmacyLocation.pharmacy_chain == "Farmatodo",
                )
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                session.add(PharmacyLocation(
                    external_id=ext_id,
                    pharmacy_chain="Farmatodo",
                    name=store_data["name"],
                    name_lower=store_data["name"].lower(),
                    city_code=store_data.get("city", ""),
                    address=store_data.get("address"),
                    latitude=store_data.get("latitude"),
                    longitude=store_data.get("longitude"),
                ))
                inserted += 1
            else:
                existing.address = store_data.get("address") or existing.address
                existing.latitude = store_data.get("latitude") or existing.latitude
                existing.longitude = store_data.get("longitude") or existing.longitude

        await session.commit()

    logger.info("Inserted %d new pharmacy locations", inserted)
    return inserted


async def lookup_store(name: str, chain: str | None = None) -> PharmacyLocation | None:
    """Look up a pharmacy location by store name (case-insensitive).

    Args:
        name: Store name (e.g., "TEPUY").
        chain: Optional chain filter (e.g., "Farmatodo").

    Returns:
        PharmacyLocation or None.
    """
    async with async_session() as session:
        query = select(PharmacyLocation).where(
            PharmacyLocation.name_lower == name.strip().lower(),
            PharmacyLocation.is_active.is_(True),
        )
        if chain:
            query = query.where(PharmacyLocation.pharmacy_chain == chain)
        result = await session.execute(query)
        return result.scalar_one_or_none()


def format_store_info(store: PharmacyLocation) -> str:
    """Format store info as a WhatsApp-friendly message."""
    lines = [f"\U0001f3e5 *{store.pharmacy_chain} {store.name}*"]
    if store.address:
        lines.append(f"\U0001f4cd {store.address}")
    if store.city_code:
        lines.append(f"Ciudad: {store.city_code}")
    if store.latitude and store.longitude:
        maps_url = f"https://maps.google.com/?q={store.latitude},{store.longitude}"
        lines.append(f"\U0001f5fa Ver en mapa: {maps_url}")
    return "\n".join(lines)
