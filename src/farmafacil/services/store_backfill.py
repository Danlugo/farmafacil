"""Backfill pharmacy store locations from their APIs into our database."""

import hashlib
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, select

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation

logger = logging.getLogger(__name__)

FARMATODO_STORES_API = "https://api-transactional.farmatodo.com/route/r/VE/v1/stores/nearby"
SAAS_PICKUP_API = "https://www.farmaciasaas.com/api/checkout/pub/pickup-points"
LOCATEL_PICKUP_API = "https://www.locatel.com.ve/api/checkout/pub/pickup-points"
FARMABIEN_STORES_URL = "https://www.farmabien.com/tiendas"
FARMARKET_STORES_URL = "https://sitio.farmarket.com.ve/UbicacionesFarmarket.html"

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

    Fetches from Farmatodo, Farmacias SAAS, Locatel, FarmaBien, and
    Farmarket store APIs. FarmaGO is delivery-only and has no physical
    stores, so it is intentionally excluded.

    Returns:
        Number of new stores inserted.
    """
    total_inserted = 0
    total_inserted += await _backfill_farmatodo_stores()
    total_inserted += await _backfill_saas_stores()
    total_inserted += await _backfill_locatel_stores()
    total_inserted += await _backfill_farmabien_stores()
    total_inserted += await _backfill_farmarket_stores()
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


async def _backfill_farmabien_stores() -> int:
    """Fetch FarmaBien store locations from their Next.js tiendas page.

    FarmaBien exposes store data via a Next.js RSC payload at /tiendas.
    The ``defaultStores`` prop contains a JSON array of all ~114 Venezuelan
    stores with full GPS coordinates, addresses, and contact info.

    Returns:
        Number of new stores inserted.
    """
    stores: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                FARMABIEN_STORES_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch FarmaBien stores page: %s", exc)
        return 0

    # Extract defaultStores JSON array from Next.js RSC payload.
    # The stores appear as: "defaultStores":[{...},{...}]
    # re.DOTALL needed because the JSON array may span multiple lines.
    match = re.search(r'"defaultStores"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
    if not match:
        logger.warning("Could not find defaultStores in FarmaBien HTML")
        return 0

    try:
        stores = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse FarmaBien store JSON: %s", exc)
        return 0

    # Filter to Venezuelan stores only (country == "VE")
    ve_stores = [s for s in stores if s.get("country") == "VE"]
    logger.info("Fetched %d FarmaBien stores from page (%d VE)", len(stores), len(ve_stores))

    inserted = 0
    async with async_session() as session:
        for store_data in ve_stores:
            store_id = store_data.get("id")
            if not store_id:
                continue
            ext_id = str(store_id)

            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.external_id == ext_id,
                    PharmacyLocation.pharmacy_chain == "FarmaBien",
                )
            )
            existing = result.scalar_one_or_none()

            name = (store_data.get("nickname") or "").strip()
            if not name:
                name = (store_data.get("store") or "Unknown").strip()

            address = (store_data.get("address") or "").strip() or None
            lat = store_data.get("latitude")
            lng = store_data.get("longitude")

            # Build phone from phone + mobile fields
            phone = (store_data.get("phone") or "").strip()
            mobile = (store_data.get("mobile") or "").strip()
            contact = phone or mobile or None

            # Map Venezuelan state to city code
            state = store_data.get("state", "")
            locality = store_data.get("locality", "")
            city_code = _map_ve_state_to_city(state, locality)

            if existing is None:
                session.add(PharmacyLocation(
                    external_id=ext_id,
                    pharmacy_chain="FarmaBien",
                    name=name,
                    name_lower=name.lower(),
                    city_code=city_code,
                    address=address,
                    latitude=lat,
                    longitude=lng,
                    phone=contact,
                ))
                inserted += 1
            else:
                if address is not None:
                    existing.address = address
                if lat is not None:
                    existing.latitude = lat
                if lng is not None:
                    existing.longitude = lng
                if contact is not None:
                    existing.phone = contact

        await session.commit()

    logger.info("Inserted %d new FarmaBien locations", inserted)
    return inserted


async def _backfill_farmarket_stores() -> int:
    """Fetch Farmarket store locations from their ubicaciones page.

    Farmarket publishes store addresses and Google Maps links on a static
    HTML page. GPS coordinates are extracted from ``@lat,lng`` patterns
    in the Google Maps direction URLs. All stores are in Caracas.

    Returns:
        Number of new stores inserted.
    """
    stores: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                FARMARKET_STORES_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch Farmarket stores page: %s", exc)
        return 0

    soup = BeautifulSoup(html, "lxml")
    # Farmarket stores are listed in anchor tags with Google Maps direction URLs
    # containing @lat,lng coordinates. Each store block has the name in an
    # adjacent heading or strong tag, and address in paragraph text.
    gps_re = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")

    for link in soup.find_all("a", href=gps_re):
        href = link.get("href", "")
        m = gps_re.search(href)
        if not m:
            continue

        lat = float(m.group(1))
        lng = float(m.group(2))

        # Walk up to find the parent block containing name and address
        parent = link.find_parent(["div", "li", "td", "section"])
        if not parent:
            continue

        # Extract store name — first bold/strong element in the parent
        name_el = parent.find(["strong", "b", "h3", "h4"])
        name = name_el.get_text(strip=True) if name_el else ""

        # Skip if no name or it looks like a generic header
        if not name or len(name) < 3:
            continue

        # Remove "Farmarket " prefix if present in the name
        if name.lower().startswith("farmarket"):
            name = name[len("farmarket"):].strip(" -–")
            if not name:
                name = name_el.get_text(strip=True) if name_el else "Unknown"

        # Extract address — look for <p> or text following the name
        address_parts = []
        for p in parent.find_all("p"):
            text = p.get_text(strip=True)
            if text and text != name and "google" not in text.lower():
                address_parts.append(text)
        address = ", ".join(address_parts[:2]) if address_parts else None

        # Extract phone — look for phone patterns
        phone = None
        phone_re = re.compile(r"0\d{3}[\-\s]?\d{3}[\-\s]?\d{2}[\-\s]?\d{2}")
        for text_el in parent.find_all(string=phone_re):
            pm = phone_re.search(text_el)
            if pm:
                phone = pm.group(0)
                break

        # Use deterministic coordinate-based ID since Farmarket has no IDs.
        # Coordinates uniquely identify Farmarket stores and are stable
        # across Python interpreter restarts (unlike hash()).
        ext_id = f"fm-{hashlib.md5(f'{name}{lat}{lng}'.encode()).hexdigest()[:8]}"

        stores.append({
            "ext_id": ext_id,
            "name": name,
            "address": address,
            "phone": phone,
            "latitude": lat,
            "longitude": lng,
        })

    # Deduplicate by coordinates (same store may appear in multiple links)
    seen_coords: set[tuple[float, float]] = set()
    deduped: list[dict] = []
    for s in stores:
        coord = (s["latitude"], s["longitude"])
        if coord not in seen_coords:
            seen_coords.add(coord)
            deduped.append(s)
    stores = deduped

    logger.info("Fetched %d Farmarket stores from page", len(stores))

    inserted = 0
    async with async_session() as session:
        for store_data in stores:
            ext_id = store_data["ext_id"]

            result = await session.execute(
                select(PharmacyLocation).where(
                    PharmacyLocation.external_id == ext_id,
                    PharmacyLocation.pharmacy_chain == "Farmarket",
                )
            )
            existing = result.scalar_one_or_none()

            name = store_data["name"]
            if existing is None:
                session.add(PharmacyLocation(
                    external_id=ext_id,
                    pharmacy_chain="Farmarket",
                    name=name,
                    name_lower=name.lower(),
                    city_code="CCS",  # All Farmarket stores are in Caracas
                    address=store_data.get("address"),
                    latitude=store_data["latitude"],
                    longitude=store_data["longitude"],
                    phone=store_data.get("phone"),
                ))
                inserted += 1
            else:
                addr = store_data.get("address")
                phone = store_data.get("phone")
                if addr is not None:
                    existing.address = addr
                existing.latitude = store_data["latitude"]
                existing.longitude = store_data["longitude"]
                if phone is not None:
                    existing.phone = phone

        await session.commit()

    logger.info("Inserted %d new Farmarket locations", inserted)
    return inserted


def _map_ve_state_to_city(state: str, locality: str = "") -> str:
    """Map Venezuelan state and locality names to Farmatodo-compatible city codes.

    FarmaBien stores span 11 Venezuelan states. This maps them to the same
    city code system used by all other scrapers for consistent distance
    lookups.

    Args:
        state: Venezuelan state name (e.g., "Mérida", "Distrito Capital").
        locality: Optional locality within the state.

    Returns:
        City code (e.g., "CCS", "MCBO"). Defaults to "CCS" for unknown.
    """
    state_lower = state.lower().strip()
    locality_lower = locality.lower().strip()

    # Direct state → city code mapping
    state_map: dict[str, str] = {
        "distrito capital": "CCS",
        "miranda": "CCS",
        "mérida": "MER",
        "merida": "MER",
        "táchira": "SAC",
        "tachira": "SAC",
        "zulia": "MCBO",
        "lara": "BAR",
        "barinas": "BAR",
        "anzoátegui": "PDM",
        "anzoategui": "PDM",
        "trujillo": "MER",
        "portuguesa": "BAR",
        "yaracuy": "BAR",
    }

    # Check locality-level overrides first
    if locality_lower in {"maracaibo", "ciudad ojeda", "santa bárbara de zulia",
                          "santa bárbara del zulia", "la concepción",
                          "cuatro esquinas", "san francisco"}:
        return "MCBO"

    return state_map.get(state_lower, "CCS")


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


async def backfill_zone_names(batch_size: int = 50) -> dict[str, int]:
    """Reverse-geocode pharmacy_locations rows that lack a ``zone_name``.

    Implementation note — Nominatim's free tier asks for ≤1 req/sec.
    We process up to ``batch_size`` rows per invocation and sleep 1.1s
    between requests to stay safely under the limit. The scheduled task
    runs daily; spreading 800 rows across daily batches of 50 means a
    full backfill completes in ~16 days, well under any urgency bar.

    Args:
        batch_size: Maximum number of rows to process this run.

    Returns:
        Dict with ``processed`` (rows attempted), ``updated`` (rows that
        got a zone_name), ``failed`` (Nominatim returned nothing useful).
    """
    import asyncio

    from farmafacil.services.location import reverse_geocode_zone

    async with async_session() as session:
        result = await session.execute(
            select(PharmacyLocation)
            .where(
                PharmacyLocation.zone_name.is_(None),
                PharmacyLocation.latitude.isnot(None),
                PharmacyLocation.longitude.isnot(None),
                PharmacyLocation.is_active.is_(True),
            )
            .limit(batch_size)
        )
        rows = result.scalars().all()

    if not rows:
        logger.info("No pharmacy_locations rows need zone backfill")
        return {"processed": 0, "updated": 0, "failed": 0}

    logger.info("Zone backfill: processing %d rows", len(rows))
    updated = 0
    failed = 0
    for i, row in enumerate(rows):
        # Re-open the session per row so a single bad commit can't poison
        # the rest of the batch. Cheap because the row count is small.
        zone = await reverse_geocode_zone(row.latitude, row.longitude)
        if zone:
            async with async_session() as session:
                obj = await session.get(PharmacyLocation, row.id)
                if obj is not None:
                    obj.zone_name = zone
                    await session.commit()
                    updated += 1
        else:
            failed += 1
        # Don't rate-limit the very last call — saves up to 1.1s per cycle.
        if i < len(rows) - 1:
            await asyncio.sleep(1.1)

    summary = {"processed": len(rows), "updated": updated, "failed": failed}
    logger.info("Zone backfill complete: %s", summary)
    return summary


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
            # Case-insensitive chain match — AI may produce "farmatodo"
            # while DB stores "Farmatodo".
            query = query.where(
                func.lower(PharmacyLocation.pharmacy_chain) == chain.lower()
            )
        result = await session.execute(query)
        return result.scalar_one_or_none()


def format_store_info(store: PharmacyLocation) -> str:
    """Format store info as a WhatsApp-friendly message.

    Shows all available details: address, zone, hours, phone, website,
    and a Google Maps link. Follows the same field display pattern as
    ``formatter.format_nearby_stores()`` but for a single store.
    (Enriched in Item 121, v0.42.0.)
    """
    # Title — skip chain prefix if the store name already contains it
    chain = (store.pharmacy_chain or "").strip()
    name = (store.name or "").strip()
    if name.lower().startswith(chain.lower()):
        title = name
    else:
        title = f"{chain} {name}" if chain else name
    lines = [f"\U0001f3e5 *{title}*"]

    if store.address:
        lines.append(f"\U0001f4cd {store.address}")
    if store.zone_name:
        lines.append(f"Zona: {store.zone_name}")
    if store.city_code:
        lines.append(f"Ciudad: {store.city_code}")

    # Opening hours
    if store.is_24h:
        lines.append("\U0001f319 24 horas")
    elif store.opening_hours:
        hours = store.opening_hours.strip()
        # Compact verbose OSM strings (same pattern as formatter._short_hours)
        if len(hours) > 40:
            hours = hours.split(";", 1)[0].strip()
            if len(hours) > 40:
                hours = hours[:37] + "..."
            else:
                hours += " ..."
        lines.append(f"\U0001f550 {hours}")

    if store.phone:
        lines.append(f"\U0001f4de {store.phone}")
    if store.website:
        lines.append(f"\U0001f310 {store.website}")
    if store.latitude and store.longitude:
        maps_url = f"https://maps.google.com/?q={store.latitude},{store.longitude}"
        lines.append(f"\U0001f5fa Ver en mapa: {maps_url}")
    return "\n".join(lines)
