"""Geocoding service — resolve Venezuelan zone/neighborhood names to coordinates.

Uses OpenStreetMap Nominatim for geocoding (free, no API key, knows every
neighborhood in Venezuela). Falls back to a small built-in cache for
common zones to avoid redundant API calls.
"""

import logging

logger = logging.getLogger(__name__)

# Public URLs kept here for callers that still reference them (test
# fixtures patch on these). The actual HTTP calls live in
# ``services.location._nominatim_search`` / ``_nominatim_reverse``.
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

    Thin wrapper over ``services.location.resolve`` (v0.19.0). Kept as a
    separate function with the legacy dict shape for back-compat with
    handler.py and the rest of the codebase. New callers should use
    ``services.location.resolve`` directly to access confidence and
    alternatives.

    Args:
        zone_text: User-provided zone name (e.g., "La Boyera", "El Cafetal").

    Returns:
        Dict with lat, lng, city, zone_name — or None if not found.
    """
    from farmafacil.services.location import resolve

    result = await resolve(zone_text)
    if result is None:
        logger.warning("Geocode returned no result for '%s'", zone_text)
        return None

    logger.info(
        "Geocoded '%s' → %s (%.4f, %.4f) city=%s confidence=%.2f source=%s",
        zone_text, result.zone_name, result.lat, result.lng,
        result.city_code, result.confidence, result.source,
    )

    return {
        "lat": result.lat,
        "lng": result.lng,
        "city": result.city_code,
        "zone_name": result.zone_name,
    }


async def reverse_geocode(lat: float, lng: float) -> dict | None:
    """Reverse-geocode a (latitude, longitude) pair into a city + zone name.

    Used when a user shares their WhatsApp location pin during onboarding
    (Item 24, v0.13.0). Thin wrapper over ``services.location.reverse``
    (v0.19.0) — kept with the legacy dict shape for back-compat. Falls
    back to the "Ubicación compartida" sentinel when no specific zone
    field was returned, so existing UX strings keep working.

    Returns ``None`` for non-Venezuelan coordinates or when Nominatim
    returns nothing usable.
    """
    from farmafacil.services.location import _nominatim_reverse, reverse

    # Country-code guard: we still need to reject obviously-non-VE
    # coordinates explicitly because location.reverse trusts whatever
    # Nominatim sends back. Cheap precheck via the raw adapter.
    raw = await _nominatim_reverse(lat, lng)
    if raw is None:
        return None
    country_code = ((raw.get("address") or {}).get("country_code") or "").lower()
    if country_code and country_code != "ve":
        logger.warning(
            "Reverse geocode rejected — (%.4f, %.4f) is in %s, not Venezuela",
            lat, lng, country_code,
        )
        return None

    result = await reverse(lat, lng)
    if result is None:
        return None

    zone_name = result.zone_name or "Ubicación compartida"
    logger.info(
        "Reverse-geocoded (%.4f, %.4f) → %s city=%s source=%s",
        lat, lng, zone_name, result.city_code, result.source,
    )
    return {
        "lat": lat,
        "lng": lng,
        "city": result.city_code,
        "zone_name": zone_name,
    }


async def reverse_geocode_zone(lat: float, lng: float) -> str | None:
    """Reverse-geocode coordinates to a neighborhood/zone name only.

    Thin wrapper around ``reverse_geocode`` that returns just the
    ``zone_name`` string. Used by the v0.18.0 zone backfill task to label
    pharmacy_locations rows with their neighborhood (e.g., "Las Mercedes",
    "Chuao") so the bot can show useful context like "Farmatodo TEPUY —
    Las Mercedes — 5.6 km".

    Returns the literal "Ubicación compartida" sentinel from
    ``reverse_geocode`` only when no specific neighborhood/town field was
    populated by Nominatim — callers should treat that as "no useful zone"
    and fall back to NULL in the DB.

    Args:
        lat: Latitude in decimal degrees.
        lng: Longitude in decimal degrees.

    Returns:
        Zone/neighborhood name, or None if reverse geocoding failed or
        returned only the fallback sentinel.
    """
    result = await reverse_geocode(lat, lng)
    if not result:
        return None
    zone = result.get("zone_name")
    if not zone or zone == "Ubicación compartida":
        return None
    return zone


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
