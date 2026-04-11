"""Geocoding service — resolve Venezuelan zone/neighborhood names to coordinates.

Uses OpenStreetMap Nominatim for geocoding (free, no API key, knows every
neighborhood in Venezuela). Falls back to a small built-in cache for
common zones to avoid redundant API calls.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"

# Map Venezuelan states/cities to Farmatodo city codes.
# Nominatim returns the state or municipality in the address — we match against this.
STATE_TO_CITY_CODE: dict[str, str] = {
    # Distrito Capital / Miranda (Caracas metro)
    "distrito capital": "CCS",
    "distrito metropolitano de caracas": "CCS",
    "municipio libertador": "CCS",
    "municipio chacao": "CCS",
    "municipio baruta": "CCS",
    "municipio el hatillo": "CCS",
    "municipio sucre": "CCS",
    "miranda": "CCS",
    "caracas": "CCS",
    # Zulia
    "zulia": "MCBO",
    "maracaibo": "MCBO",
    # Carabobo
    "carabobo": "VAL",
    "valencia": "VAL",
    # Lara
    "lara": "BAR",
    "barquisimeto": "BAR",
    # Aragua
    "aragua": "MAT",
    "maracay": "MAT",
    # Merida
    "mérida": "MER",
    "merida": "MER",
    # Bolivar
    "bolívar": "PTO",
    "bolivar": "PTO",
    "puerto ordaz": "PTO",
    # Tachira
    "táchira": "SAC",
    "tachira": "SAC",
    "san cristóbal": "SAC",
    "san cristobal": "SAC",
    # Anzoategui
    "anzoátegui": "PDM",
    "anzoategui": "PDM",
    "puerto la cruz": "PDM",
    "barcelona": "PDM",
    # Nueva Esparta
    "nueva esparta": "POR",
    "porlamar": "POR",
    # Falcon
    "falcón": "PTC",
    "falcon": "PTC",
    "punto fijo": "PTC",
    # Monagas
    "monagas": "MAT",
    # Portuguesa
    "portuguesa": "BAR",
    # Barinas
    "barinas": "COR",
    # Guarenas/Guatire
    "guarenas": "GUAC",
    "guatire": "GUAC",
}


async def geocode_zone(zone_text: str) -> dict | None:
    """Resolve a zone/neighborhood name to coordinates and city code.

    Uses OpenStreetMap Nominatim to geocode any Venezuelan location.

    Args:
        zone_text: User-provided zone name (e.g., "La Boyera", "El Cafetal").

    Returns:
        Dict with lat, lng, city, zone_name — or None if not found.
    """
    query = f"{zone_text}, Venezuela"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "ve",
        "addressdetails": 1,
    }
    headers = {
        "User-Agent": "FarmaFacil/0.1 (farmafacil-pharmacy-finder)",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(NOMINATIM_URL, params=params, headers=headers)
            response.raise_for_status()
            results = response.json()
    except httpx.RequestError as exc:
        logger.error("Nominatim geocode failed for '%s': %s", zone_text, exc)
        return None

    if not results:
        logger.warning("Nominatim returned no results for '%s'", zone_text)
        return None

    hit = results[0]
    lat = float(hit["lat"])
    lng = float(hit["lon"])

    # Extract a human-readable zone name
    zone_name = hit.get("name") or zone_text.strip().title()

    # Determine Farmatodo city code from the address details
    city_code = _extract_city_code(hit)

    logger.info(
        "Geocoded '%s' → %s (%.4f, %.4f) city=%s",
        zone_text, zone_name, lat, lng, city_code,
    )

    return {
        "lat": lat,
        "lng": lng,
        "city": city_code,
        "zone_name": zone_name,
    }


async def reverse_geocode(lat: float, lng: float) -> dict | None:
    """Reverse-geocode a (latitude, longitude) pair into a city + zone name.

    Used when a user shares their WhatsApp location pin during onboarding
    instead of typing a city name (Item 24, v0.13.0). Returns the same
    shape as ``geocode_zone`` so the two code paths can share the
    ``update_user_location`` call.

    Args:
        lat: Latitude in decimal degrees.
        lng: Longitude in decimal degrees.

    Returns:
        Dict with ``lat``, ``lng``, ``city`` (Farmatodo code), and
        ``zone_name`` — or ``None`` if the coordinates cannot be resolved
        (unreachable Nominatim, outside Venezuela, malformed response).
    """
    params = {
        "lat": f"{lat}",
        "lon": f"{lng}",
        "format": "json",
        "addressdetails": 1,
        "zoom": 14,  # neighborhood-level detail
    }
    headers = {
        "User-Agent": "FarmaFacil/0.1 (farmafacil-pharmacy-finder)",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                NOMINATIM_REVERSE_URL, params=params, headers=headers,
            )
            response.raise_for_status()
            hit = response.json()
    except httpx.RequestError as exc:
        logger.error(
            "Nominatim reverse geocode failed for (%.4f, %.4f): %s",
            lat, lng, exc,
        )
        return None
    except ValueError as exc:
        logger.error(
            "Nominatim reverse geocode returned invalid JSON for "
            "(%.4f, %.4f): %s",
            lat, lng, exc,
        )
        return None

    if not isinstance(hit, dict) or "address" not in hit:
        logger.warning(
            "Nominatim reverse geocode returned no address for (%.4f, %.4f)",
            lat, lng,
        )
        return None

    # Guard: only accept Venezuelan coordinates. Users outside VE fall
    # through to the "location not found" path so they can type a city.
    address = hit.get("address", {})
    country_code = (address.get("country_code") or "").lower()
    if country_code and country_code != "ve":
        logger.warning(
            "Reverse geocode rejected — (%.4f, %.4f) is in %s, not Venezuela",
            lat, lng, country_code,
        )
        return None

    # Pick a human-readable zone name from the most specific field available.
    zone_name = (
        address.get("suburb")
        or address.get("neighbourhood")
        or address.get("village")
        or address.get("town")
        or address.get("city")
        or address.get("county")
        or address.get("state")
        or "Ubicación compartida"
    )

    city_code = _extract_city_code(hit)

    logger.info(
        "Reverse-geocoded (%.4f, %.4f) → %s city=%s",
        lat, lng, zone_name, city_code,
    )

    return {
        "lat": lat,
        "lng": lng,
        "city": city_code,
        "zone_name": zone_name,
    }


def _extract_city_code(hit: dict) -> str:
    """Extract Farmatodo city code from Nominatim address details.

    Args:
        hit: Nominatim search result with addressdetails.

    Returns:
        Farmatodo city code (defaults to "CCS" if unknown).
    """
    address = hit.get("address", {})
    display = hit.get("display_name", "").lower()

    # Check address fields against our state/city mapping
    for field in ["city", "town", "municipality", "county", "state", "suburb"]:
        value = address.get(field, "").lower()
        if value in STATE_TO_CITY_CODE:
            return STATE_TO_CITY_CODE[value]

    # Check the full display_name for known patterns
    for key, code in STATE_TO_CITY_CODE.items():
        if key in display:
            return code

    logger.warning("Could not determine city code from: %s", display)
    return "CCS"  # Default to Caracas
