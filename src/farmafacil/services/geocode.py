"""Geocoding service — resolve Venezuelan zone/neighborhood names to coordinates."""

import logging

logger = logging.getLogger(__name__)

# Known Venezuelan zones with approximate coordinates and Farmatodo city codes.
# This is a seed list — extend as users mention new zones.
KNOWN_ZONES: dict[str, dict] = {
    # Caracas zones
    "el cafetal": {"lat": 10.4558, "lng": -66.8378, "city": "CCS"},
    "chacao": {"lat": 10.4924, "lng": -66.8572, "city": "CCS"},
    "altamira": {"lat": 10.4973, "lng": -66.8514, "city": "CCS"},
    "las mercedes": {"lat": 10.4856, "lng": -66.8634, "city": "CCS"},
    "sabana grande": {"lat": 10.4926, "lng": -66.8764, "city": "CCS"},
    "los palos grandes": {"lat": 10.5005, "lng": -66.8478, "city": "CCS"},
    "la castellana": {"lat": 10.498, "lng": -66.8568, "city": "CCS"},
    "bello monte": {"lat": 10.487, "lng": -66.8715, "city": "CCS"},
    "chuao": {"lat": 10.4823, "lng": -66.8460, "city": "CCS"},
    "la trinidad": {"lat": 10.4422, "lng": -66.8525, "city": "CCS"},
    "prados del este": {"lat": 10.4533, "lng": -66.8600, "city": "CCS"},
    "santa fe": {"lat": 10.4775, "lng": -66.8430, "city": "CCS"},
    "el hatillo": {"lat": 10.4300, "lng": -66.8250, "city": "CCS"},
    "baruta": {"lat": 10.4440, "lng": -66.8720, "city": "CCS"},
    "petare": {"lat": 10.4828, "lng": -66.8083, "city": "CCS"},
    "la california": {"lat": 10.4880, "lng": -66.8340, "city": "CCS"},
    "los ruices": {"lat": 10.4950, "lng": -66.8370, "city": "CCS"},
    "la urbina": {"lat": 10.4920, "lng": -66.8130, "city": "CCS"},
    "el marques": {"lat": 10.4960, "lng": -66.8230, "city": "CCS"},
    "catia": {"lat": 10.5100, "lng": -66.9400, "city": "CCS"},
    "el paraiso": {"lat": 10.4980, "lng": -66.9170, "city": "CCS"},
    "plaza venezuela": {"lat": 10.4980, "lng": -66.8870, "city": "CCS"},
    "los caobos": {"lat": 10.5000, "lng": -66.8900, "city": "CCS"},
    "san bernardino": {"lat": 10.5100, "lng": -66.8900, "city": "CCS"},
    "caracas": {"lat": 10.4806, "lng": -66.9036, "city": "CCS"},
    # Maracaibo
    "maracaibo": {"lat": 10.6427, "lng": -71.6125, "city": "MCBO"},
    "bella vista": {"lat": 10.6600, "lng": -71.6200, "city": "MCBO"},
    "tierra negra": {"lat": 10.6500, "lng": -71.6300, "city": "MCBO"},
    "la lago": {"lat": 10.6650, "lng": -71.6050, "city": "MCBO"},
    "indio mara": {"lat": 10.6480, "lng": -71.6400, "city": "MCBO"},
    # Valencia
    "valencia": {"lat": 10.1620, "lng": -67.9930, "city": "VAL"},
    "prebo": {"lat": 10.1700, "lng": -68.0100, "city": "VAL"},
    "trigal": {"lat": 10.1800, "lng": -68.0000, "city": "VAL"},
    "naguanagua": {"lat": 10.1950, "lng": -68.0150, "city": "VAL"},
    "san diego": {"lat": 10.2100, "lng": -67.9600, "city": "VAL"},
    # Barquisimeto
    "barquisimeto": {"lat": 10.0678, "lng": -69.3474, "city": "BAR"},
    "este barquisimeto": {"lat": 10.0700, "lng": -69.3200, "city": "BAR"},
    # Maracay
    "maracay": {"lat": 10.2353, "lng": -67.5911, "city": "MAT"},
    "base aragua": {"lat": 10.2530, "lng": -67.6100, "city": "MAT"},
    # Merida
    "merida": {"lat": 8.5897, "lng": -71.1561, "city": "MER"},
    # Puerto Ordaz
    "puerto ordaz": {"lat": 8.2886, "lng": -62.7147, "city": "PTO"},
    "alta vista": {"lat": 8.3100, "lng": -62.7200, "city": "PTO"},
    # San Cristobal
    "san cristobal": {"lat": 7.7669, "lng": -72.2250, "city": "SAC"},
    # Puerto La Cruz / Barcelona
    "puerto la cruz": {"lat": 10.2122, "lng": -64.6317, "city": "PDM"},
    "barcelona": {"lat": 10.1364, "lng": -64.6864, "city": "PDM"},
    "lecheria": {"lat": 10.1884, "lng": -64.6936, "city": "LEC"},
    # Porlamar
    "porlamar": {"lat": 11.0000, "lng": -63.8500, "city": "POR"},
    # Punto Fijo
    "punto fijo": {"lat": 11.6937, "lng": -70.2094, "city": "PTC"},
}


def geocode_zone(zone_text: str) -> dict | None:
    """Resolve a zone/neighborhood name to coordinates and city code.

    Args:
        zone_text: User-provided zone name (e.g., "El Cafetal", "Chacao").

    Returns:
        Dict with lat, lng, city, zone_name — or None if not found.
    """
    normalized = zone_text.strip().lower()

    # Direct match
    if normalized in KNOWN_ZONES:
        data = KNOWN_ZONES[normalized]
        return {
            "lat": data["lat"],
            "lng": data["lng"],
            "city": data["city"],
            "zone_name": zone_text.strip().title(),
        }

    # Partial match — check if the input contains a known zone
    for zone_key, data in KNOWN_ZONES.items():
        if zone_key in normalized or normalized in zone_key:
            return {
                "lat": data["lat"],
                "lng": data["lng"],
                "city": data["city"],
                "zone_name": zone_key.title(),
            }

    logger.warning("Unknown zone: '%s'", zone_text)
    return None
