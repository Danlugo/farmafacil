"""Backfill pharmacy store locations from their APIs into our database."""

import logging

import httpx
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation

logger = logging.getLogger(__name__)

FARMATODO_STORES_API = "https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby"
SAAS_PICKUP_API = "https://www.farmaciasaas.com/api/checkout/pub/pickup-points"
LOCATEL_PICKUP_API = "https://www.locatel.com.ve/api/checkout/pub/pickup-points"

# Farmatodo city codes with center coordinates for store discovery
FARMATODO_CITIES: dict[str, tuple[float, float]] = {
    "CCS": (10.48, -66.86), "MCBO": (10.64, -71.61), "VAL": (10.16, -67.99),
    "BAR": (10.07, -69.35), "MAT": (10.24, -67.59), "MER": (8.59, -71.16),
    "PTO": (8.29, -62.71), "SAC": (7.77, -72.23), "PDM": (10.21, -64.63),
    "POR": (11.00, -63.85), "PTC": (11.69, -70.21), "GUAC": (10.47, -66.62),
    "LEC": (10.35, -66.98), "COR": (8.63, -70.21), "CUA": (10.45, -64.17),
    "PAM": (11.00, -63.85), "HIG": (10.07, -66.10), "UPA": (8.00, -62.40),
}

# VTEX center coordinates for pickup point discovery (covers major Venezuelan cities)
VTEX_GEO_CENTERS: list[tuple[float, float]] = [
    (10.48, -66.86),   # Caracas
    (10.64, -71.61),   # Maracaibo
    (10.16, -67.99),   # Valencia
    (10.07, -69.35),   # Barquisimeto
]


async def backfill_stores() -> int:
    """Fetch all pharmacy store locations and upsert into pharmacy_locations.

    Fetches from both Farmatodo (Algolia stores API) and Farmacias SAAS
    (VTEX pickup points API).

    Returns:
        Number of new stores inserted.
    """
    total_inserted = 0
    total_inserted += await _backfill_farmatodo_stores()
    total_inserted += await _backfill_saas_stores()
    total_inserted += await _backfill_locatel_stores()
    return total_inserted


async def _backfill_farmatodo_stores() -> int:
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
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch Farmatodo stores for %s: %s",
                    city_code, exc,
                )
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "Invalid JSON from Farmatodo stores API for %s: %s",
                    city_code, exc,
                )

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

    logger.info("Inserted %d new Farmatodo locations", inserted)
    return inserted


async def _backfill_saas_stores() -> int:
    """Fetch Farmacias SAAS pickup points via VTEX public API.

    SAAS has ~9 pickup/store locations accessible via their VTEX checkout
    pickup-points endpoint. We query from multiple geo centers to discover
    all stores across Venezuela.

    Returns:
        Number of new stores inserted.
    """
    all_stores: dict[str, dict] = {}  # keyed by pickup point ID

    async with httpx.AsyncClient(timeout=15) as client:
        for lat, lng in VTEX_GEO_CENTERS:
            try:
                resp = await client.get(
                    SAAS_PICKUP_API,
                    params={"geoCoordinates": f"{lng};{lat}"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("items", []):
                    pp = item.get("pickupPoint", {})
                    pp_id = pp.get("id")
                    if pp_id:
                        all_stores[pp_id] = pp
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch SAAS pickup points for geo %s,%s: %s",
                    lat, lng, exc,
                )
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "Invalid JSON from SAAS pickup API for geo %s,%s: %s",
                    lat, lng, exc,
                )

    logger.info("Fetched %d Farmacias SAAS stores from API", len(all_stores))

    inserted = 0
    async with async_session() as session:
        for pp_id, pp_data in all_stores.items():
            ext_id = str(pp_id)
            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.external_id == ext_id,
                    PharmacyLocation.pharmacy_chain == "Farmacias SAAS",
                )
            )
            existing = result.scalar_one_or_none()

            addr = pp_data.get("address", {})
            geo = addr.get("geoCoordinates", [])
            store_lng, store_lat = (geo[0], geo[1]) if len(geo) == 2 else (None, None)

            # Extract clean store name (remove "Farmacia SAAS - " prefix)
            raw_name = pp_data.get("friendlyName", "")
            name = raw_name.replace("Farmacia SAAS - ", "").strip() or raw_name

            # Map city to Farmatodo city codes for compatibility
            city = addr.get("city", "")
            city_code = _map_vtex_city(city)

            street = addr.get("street", "")
            complement = addr.get("complement", "")
            full_address = f"{street}, {complement}".strip(", ") if street else None

            if existing is None:
                session.add(PharmacyLocation(
                    external_id=ext_id,
                    pharmacy_chain="Farmacias SAAS",
                    name=name,
                    name_lower=name.lower(),
                    city_code=city_code,
                    address=full_address,
                    latitude=store_lat,
                    longitude=store_lng,
                ))
                inserted += 1
            else:
                existing.address = full_address or existing.address
                existing.latitude = store_lat or existing.latitude
                existing.longitude = store_lng or existing.longitude

        await session.commit()

    logger.info("Inserted %d new Farmacias SAAS locations", inserted)
    return inserted


async def _backfill_locatel_stores() -> int:
    """Fetch Locatel pickup points via VTEX public API.

    Locatel stores are accessible via the same VTEX checkout pickup-points
    endpoint as SAAS. Currently ~8 stores in Caracas + Valencia.

    Returns:
        Number of new stores inserted.
    """
    all_stores: dict[str, dict] = {}  # keyed by pickup point ID

    async with httpx.AsyncClient(timeout=15) as client:
        for lat, lng in VTEX_GEO_CENTERS:
            try:
                resp = await client.get(
                    LOCATEL_PICKUP_API,
                    params={"geoCoordinates": f"{lng};{lat}"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("items", []):
                    pp = item.get("pickupPoint", {})
                    pp_id = pp.get("id")
                    if pp_id:
                        all_stores[pp_id] = pp
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch Locatel pickup points for geo %s,%s: %s",
                    lat, lng, exc,
                )
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning(
                    "Invalid JSON from Locatel pickup API for geo %s,%s: %s",
                    lat, lng, exc,
                )

    logger.info("Fetched %d Locatel stores from API", len(all_stores))

    inserted = 0
    async with async_session() as session:
        for pp_id, pp_data in all_stores.items():
            ext_id = str(pp_id)
            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.external_id == ext_id,
                    PharmacyLocation.pharmacy_chain == "Locatel",
                )
            )
            existing = result.scalar_one_or_none()

            addr = pp_data.get("address", {})
            geo = addr.get("geoCoordinates", [])
            store_lng, store_lat = (geo[0], geo[1]) if len(geo) == 2 else (None, None)

            # Extract clean store name (remove "Locatel " prefix if present)
            raw_name = pp_data.get("friendlyName", "")
            name = raw_name.replace("Locatel ", "").strip() or raw_name

            # Map city to Farmatodo city codes for compatibility
            city = addr.get("city", "")
            city_code = _map_vtex_city(city)

            street = addr.get("street", "")
            complement = addr.get("complement", "")
            full_address = f"{street}, {complement}".strip(", ") if street else None

            if existing is None:
                session.add(PharmacyLocation(
                    external_id=ext_id,
                    pharmacy_chain="Locatel",
                    name=name,
                    name_lower=name.lower(),
                    city_code=city_code,
                    address=full_address,
                    latitude=store_lat,
                    longitude=store_lng,
                ))
                inserted += 1
            else:
                existing.address = full_address or existing.address
                existing.latitude = store_lat or existing.latitude
                existing.longitude = store_lng or existing.longitude

        await session.commit()

    logger.info("Inserted %d new Locatel locations", inserted)
    return inserted


def _map_vtex_city(city_name: str) -> str:
    """Map SAAS city names to Farmatodo-compatible city codes.

    Args:
        city_name: City name from SAAS API (e.g., "Chacao", "Libertador").

    Returns:
        City code (e.g., "CCS"). Defaults to "CCS" for unknown Caracas municipalities.
    """
    city_lower = city_name.lower().strip()
    # Caracas municipalities
    caracas_municipalities = {
        "chacao", "libertador", "baruta", "el hatillo", "sucre",
        "los salias", "carrizal", "urdaneta",
    }
    if city_lower in caracas_municipalities:
        return "CCS"
    # Add more mappings as SAAS expands to other cities
    city_map = {
        "maracaibo": "MCBO",
        "valencia": "VAL",
        "barquisimeto": "BAR",
    }
    return city_map.get(city_lower, "CCS")


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
