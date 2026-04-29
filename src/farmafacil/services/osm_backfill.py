"""OpenStreetMap backfill — discover pharmacies + rich attributes from OSM.

Closes the coverage gap left by our 3 chain scrapers (Farmatodo, Farmacias
SAAS, Locatel) — they only return their own stores. OSM is community-mapped
and includes independents, smaller chains, and rich tags (opening hours,
phone, website, email).

Flow:
  1. POST a single Overpass QL query for every ``amenity=pharmacy`` in
     Venezuela. One request per backfill — Overpass enforces 2 req/min so
     we deliberately batch in one query.
  2. For each result, normalize tags (handling both ``phone`` and
     ``contact:phone``, etc.) and detect the chain from name/brand/operator.
  3. Dedup against existing ``pharmacy_locations`` rows: skip if any
     existing row is within ``DUPLICATE_RADIUS_M`` AND the names overlap
     (token-set similarity ≥ ``DUPLICATE_NAME_THRESHOLD``).
  4. New rows: insert with chain-detected ``pharmacy_chain``. Rows that
     match existing entries: UPDATE missing attributes (phone, hours,
     website, email) without touching name/coords/chain.

Independent pharmacies are stored as ``pharmacy_chain="Independiente"`` so
``services.search._enrich_with_nearby_stores`` can filter them out (they
don't have a stock API), while the nearest-store feature still surfaces them.
"""

import logging
import math
import re
from typing import Any

import httpx
from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 90  # seconds

# Single Overpass QL query that fetches every pharmacy in Venezuela in one
# request. ``out tags center;`` returns full tag set + a center coord for
# ways/relations (so we can treat them like nodes).
OVERPASS_QUERY = """
[out:json][timeout:60];
area["ISO3166-1"="VE"]->.searchArea;
(
  node["amenity"="pharmacy"](area.searchArea);
  way["amenity"="pharmacy"](area.searchArea);
  relation["amenity"="pharmacy"](area.searchArea);
);
out tags center;
""".strip()

# Two locations within 100m + name overlap >= 0.7 are considered duplicates.
# Tuned for our use case: chain stores often have very similar names within
# a small block, but 100m is well outside any reasonable "same store" radius.
DUPLICATE_RADIUS_M: float = 100.0
DUPLICATE_NAME_THRESHOLD: float = 0.7

# Chain detection — substrings to match (case-insensitive) against name +
# brand + operator. Order matters only when chains overlap (e.g. "Locatel"
# could appear inside "Farmacia Locatel del Hatillo" — we still want it as
# Locatel).
_CHAIN_PATTERNS: list[tuple[str, str]] = [
    ("farmatodo", "Farmatodo"),
    ("farmacias saas", "Farmacias SAAS"),
    ("saas", "Farmacias SAAS"),
    ("locatel", "Locatel"),
    ("farmarebajas", "Farmarebajas"),
    ("farmahorro", "Farmahorro"),
    ("xana", "Farmacias XANA"),
]

INDEPENDIENTE_CHAIN: str = "Independiente"


# ── Overpass / parsing ─────────────────────────────────────────────────


async def fetch_osm_pharmacies() -> list[dict[str, Any]]:
    """Run the Overpass query and return the raw element list.

    Returns:
        List of OSM elements (each has ``id``, ``type``, ``tags`` and
        either ``lat``/``lon`` for nodes or ``center`` for ways/relations).
        Empty list on any failure — callers should treat that as "no
        backfill happened this cycle" rather than crashing.
    """
    headers = {"User-Agent": "FarmaFacil/0.18 (farmafacil-pharmacy-finder)"}
    try:
        async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT) as client:
            resp = await client.post(
                OVERPASS_URL,
                content=OVERPASS_QUERY,
                headers={**headers, "Content-Type": "text/plain; charset=utf-8"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Overpass returned %d — backfill skipped this cycle",
            exc.response.status_code,
        )
        return []
    except httpx.RequestError as exc:
        logger.error("Overpass request failed: %s", exc)
        return []
    except ValueError as exc:
        logger.error("Overpass returned invalid JSON: %s", exc)
        return []

    elements = data.get("elements", [])
    logger.info("Overpass returned %d elements", len(elements))
    return elements


def parse_osm_element(element: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a single OSM element into a backfill-ready dict.

    Handles both nodes (lat/lon at top level) and ways/relations (lat/lon
    in ``center``). Pulls phone/website/email from both their canonical
    tags (``phone``) and the OSM ``contact:*`` namespace, preferring the
    canonical form when both are present.

    Returns ``None`` for elements that lack a usable name or coordinates —
    those would never be useful to surface to the user.
    """
    tags = element.get("tags") or {}
    name = (tags.get("name") or "").strip()
    if not name:
        return None

    # Coordinates — nodes have lat/lon directly, ways/relations use center.
    if "lat" in element and "lon" in element:
        lat, lng = element["lat"], element["lon"]
    elif "center" in element:
        center = element["center"]
        lat, lng = center.get("lat"), center.get("lon")
    else:
        return None
    if lat is None or lng is None:
        return None

    # Reject obviously non-Venezuelan coordinates as a defensive guard.
    # VE actual bbox: lat 0.6–12.2, lng -73.4 to -59.8. Code uses a 0.6°
    # margin on each side (lat 0.5–12.5, lng -74.0 to -59.0) so the very
    # westernmost Táchira and Esequiba edge cases aren't rejected.
    if not (0.5 <= lat <= 12.5 and -74.0 <= lng <= -59.0):
        return None

    osm_id = element.get("id")
    osm_type = element.get("type", "node")

    # Build a structured address from addr:* tags when the chain APIs left
    # us nothing. Otherwise prefer whatever we already have.
    address_parts = []
    for key in ("addr:street", "addr:housenumber", "addr:suburb", "addr:city"):
        value = tags.get(key)
        if value:
            address_parts.append(value)
    address = ", ".join(address_parts) if address_parts else None

    # Phone, website, email — both canonical and contact:* namespaces.
    # OSM phone tags often pack multiple numbers separated by `;` or `,`
    # (e.g., "+58 212-8724131; +58 414-1234567"). The pharmacy_locations
    # column is VARCHAR(30) so we keep the FIRST number only and truncate
    # defensively. Other text fields are clamped to their column lengths
    # too — OSM data quality varies and we'd rather drop tail bytes than
    # crash the entire backfill cycle.
    phone_raw = tags.get("phone") or tags.get("contact:phone")
    phone = None
    if phone_raw:
        first = re.split(r"[;,]", phone_raw, maxsplit=1)[0].strip()
        phone = first[:30] or None

    website = tags.get("website") or tags.get("contact:website")
    if website:
        website = website.strip()[:500] or None
    email = tags.get("email") or tags.get("contact:email")
    if email:
        email = email.strip()[:255] or None

    opening_hours = (tags.get("opening_hours") or "").strip() or None
    if opening_hours and len(opening_hours) > 255:
        opening_hours = opening_hours[:255]

    # name and name_lower are VARCHAR(100). Pharmacy names rarely exceed
    # this but a few OSM entries include the full address in the name.
    name = name[:100]

    chain = detect_chain(
        name=name,
        brand=tags.get("brand"),
        operator=tags.get("operator"),
    )

    return {
        "external_id": f"osm-{osm_type}-{osm_id}"[:150],
        "name": name,
        "pharmacy_chain": chain[:100],
        "latitude": float(lat),
        "longitude": float(lng),
        "address": address,
        "phone": phone,
        "website": website,
        "email": email,
        "opening_hours": opening_hours,
        "is_24h": is_24h_from_hours(opening_hours),
    }


def detect_chain(
    name: str, brand: str | None = None, operator: str | None = None,
) -> str:
    """Map an OSM pharmacy to one of our known chains, or Independiente.

    Checks name + brand + operator in turn against a curated substring
    list. Case-insensitive. Returns the canonical chain name our scrapers
    use (e.g., "Farmacias SAAS" not "SAAS"), or ``INDEPENDIENTE_CHAIN``
    when nothing matches.
    """
    haystack = " ".join(s for s in (name, brand, operator) if s).lower()
    for pattern, canonical in _CHAIN_PATTERNS:
        if pattern in haystack:
            return canonical
    return INDEPENDIENTE_CHAIN


def is_24h_from_hours(opening_hours: str | None) -> bool:
    """Detect 24-hour pharmacies from an OSM ``opening_hours`` string.

    OSM's canonical tag for round-the-clock is ``24/7``. We also accept a
    handful of common authoring variants (``24x7``, ``24h``, ``Mo-Su 00:00-24:00``)
    so we don't miss obviously-24h stores due to tag drift. Anything else
    is treated as not-24h — partial-day hours are NOT promoted.
    """
    if not opening_hours:
        return False
    s = opening_hours.strip().lower().replace(" ", "")
    if s in {"24/7", "24x7", "24h", "00:00-24:00"}:
        return True
    # "Mo-Su 00:00-24:00" or similar — must cover all days AND full day.
    if "00:00-24:00" in s and ("mo-su" in s or "mo-sun" in s or "all" in s):
        return True
    return False


# ── Dedup ─────────────────────────────────────────────────────────────


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    R = 6371000  # Earth radius in meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


_NAME_NOISE = {
    "farmacia", "farmacias", "pharmacy", "drugstore",
    "de", "la", "el", "los", "las", "del", "y", "&",
}


def _name_tokens(name: str) -> set[str]:
    """Tokenize a pharmacy name into a comparison-friendly set.

    Strips accents, lowercases, drops punctuation and the common-word
    noise list above. The leftover tokens carry the actual identifying
    signal ("tepuy", "chuao", "los naranjos").
    """
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name.lower())
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    plain = re.sub(r"[^\w\s]", " ", plain)
    return {t for t in plain.split() if t and t not in _NAME_NOISE and len(t) > 1}


def name_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity in [0, 1].

    Two empty token sets return 0.0 (we cannot decide they are the same
    pharmacy from a name alone — fall back to the distance check).
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_duplicate(
    osm_row: dict[str, Any], existing: PharmacyLocation,
) -> bool:
    """Decide whether an OSM result already exists in pharmacy_locations.

    The join condition is intentionally loose: same coords (within
    ``DUPLICATE_RADIUS_M`` meters) AND name overlap above
    ``DUPLICATE_NAME_THRESHOLD``. Either signal alone produces too many
    false positives — chain stores cluster geographically and OSM names
    drift from chain-API names.
    """
    if existing.latitude is None or existing.longitude is None:
        return False
    distance = haversine_m(
        osm_row["latitude"], osm_row["longitude"],
        existing.latitude, existing.longitude,
    )
    if distance > DUPLICATE_RADIUS_M:
        return False
    sim = name_similarity(osm_row["name"], existing.name)
    return sim >= DUPLICATE_NAME_THRESHOLD


# ── Backfill orchestration ────────────────────────────────────────────


async def backfill_from_osm() -> dict[str, int]:
    """Run a full OSM backfill cycle and return summary counts.

    The function is idempotent: running it twice in a row should be a
    near-no-op because all OSM rows match existing entries on the second
    pass, so the second call only re-checks fields that are still NULL.

    Returns:
        Dict with ``inserted``, ``updated``, ``skipped`` (already-current),
        ``rejected`` (no name / no coords / outside VE) keys.
    """
    elements = await fetch_osm_pharmacies()
    if not elements:
        return {"inserted": 0, "updated": 0, "skipped": 0, "rejected": 0}

    parsed: list[dict[str, Any]] = []
    rejected = 0
    for el in elements:
        row = parse_osm_element(el)
        if row is None:
            rejected += 1
        else:
            parsed.append(row)

    inserted = 0
    updated = 0
    skipped = 0

    async with async_session() as session:
        existing_rows = (
            (await session.execute(select(PharmacyLocation)))
            .scalars()
            .all()
        )

        for osm_row in parsed:
            duplicate = next(
                (e for e in existing_rows if is_duplicate(osm_row, e)),
                None,
            )
            if duplicate is not None:
                # Backfill missing fields only — never overwrite chain-API
                # data that's already populated. We treat empty strings the
                # same as NULL because chain APIs sometimes write "".
                changed = False
                for field in ("phone", "website", "email", "opening_hours"):
                    current = getattr(duplicate, field, None)
                    incoming = osm_row.get(field)
                    if incoming and not current:
                        setattr(duplicate, field, incoming)
                        changed = True
                if osm_row["is_24h"] and not duplicate.is_24h:
                    duplicate.is_24h = True
                    changed = True
                if changed:
                    updated += 1
                else:
                    skipped += 1
                continue

            new_row = PharmacyLocation(
                external_id=osm_row["external_id"],
                pharmacy_chain=osm_row["pharmacy_chain"],
                name=osm_row["name"],
                name_lower=osm_row["name"].lower(),
                city_code="",  # zone backfill task can fill via reverse geocode
                address=osm_row["address"],
                latitude=osm_row["latitude"],
                longitude=osm_row["longitude"],
                phone=osm_row["phone"],
                opening_hours=osm_row["opening_hours"],
                is_24h=osm_row["is_24h"],
                website=osm_row["website"],
                email=osm_row["email"],
                is_active=True,
            )
            session.add(new_row)
            # Append to in-memory list so subsequent OSM rows in this same
            # batch can match against rows we JUST inserted (e.g., when OSM
            # has both a `node` and a `way` for the same physical pharmacy).
            # Without this, the second occurrence escapes dedup and we
            # double-insert. (Fix from code-review finding #1.)
            existing_rows.append(new_row)
            inserted += 1

        await session.commit()

    summary = {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "rejected": rejected,
    }
    logger.info("OSM backfill complete: %s", summary)
    return summary
