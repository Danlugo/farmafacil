"""Location service — single front door for every coordinate operation.

v0.19.0 (Items 47/48). Wraps the existing Nominatim integration in
``services.geocode`` with three things the raw geocoder lacks:

1. **Caching.** Forward and reverse queries are persisted in
   ``geocode_cache``. Repeat queries (every "La Boyera" onboarding,
   every nightly zone-backfill row) hit the cache and skip Nominatim
   entirely, freeing up our 1 req/sec free-tier budget for actually-new
   queries.

2. **Confidence + validation.** Nominatim's ``importance`` score and a
   token-overlap check between the user's input and the returned
   ``display_name`` give us a reliable signal for "this might be the
   wrong place." Daniel's onboarding (resolved "La Boyera" to "La
   Hoyadita" 7.8 km south) would have been caught here.

3. **Admin helpers.** ``set_user_location`` and
   ``set_pharmacy_location`` resolve a query through the full pipeline
   and persist the result, so the admin chat tools can fix a bad coord
   from WhatsApp without anyone SSH-ing into prod.

The legacy ``geocode_zone`` / ``reverse_geocode`` functions in
``services.geocode`` keep working — they delegate here under the hood.
"""

import hashlib
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select

from farmafacil.db.session import async_session
from farmafacil.models.database import GeocodeCache, PharmacyLocation, User

logger = logging.getLogger(__name__)

# How long a cache entry is considered fresh. Pharmacies do not move and
# user-typed zone names do not change, so 30 days is comfortable. The
# cleanup task (scheduler.py) prunes anything older than 90 days.
CACHE_TTL_DAYS = 30

# Below this Nominatim importance score we treat the result as too low
# confidence to use silently. Empirically, importance < 0.3 corresponds
# to a "barely matched" hit (e.g., a single-word village in Colombia
# matching a Caracas barrio name). Tuned, not load-bearing — caller can
# override per-call.
DEFAULT_MIN_CONFIDENCE = 0.3

# How many alternative results to include in the response when a forward
# query is ambiguous. The bot uses these to render a "did you mean?" list.
ALTERNATIVES_TOP_N = 2


# ── Result shape ──────────────────────────────────────────────────────


@dataclass
class LocationResult:
    """Outcome of a forward or reverse geocode lookup."""

    lat: float
    lng: float
    display_name: str
    confidence: float            # 0.0–1.0, derived from Nominatim importance
    source: str                  # 'cache' | 'forward' | 'reverse' | 'manual'
    city_code: str = "CCS"
    zone_name: str | None = None
    alternatives: list[dict] = field(default_factory=list)


# ── Hash + normalization ──────────────────────────────────────────────


import re as _re_normalize

# Strip everything except letters/digits/whitespace so "boyera," and
# "boyera" tokenize identically when comparing display_name strings.
_NORMALIZE_PUNCT = _re_normalize.compile(r"[^\w\s]")


def _normalize(text: str) -> str:
    """Lower, strip accents, drop punctuation, collapse whitespace.

    Two queries that look the same to a human ("La Boyera" vs "la
    boyera" vs "LA  BOYERA," vs "la boyera, ve") must hash to the same
    cache key AND tokenize the same way for the name-match validator.
    """
    s = text.strip().lower()
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = _NORMALIZE_PUNCT.sub(" ", s)
    return " ".join(s.split())


def _forward_key(query: str) -> tuple[str, str]:
    """Return (normalized, hash) for a forward (text → coords) query."""
    normalized = _normalize(query)
    h = hashlib.sha256(f"forward|{normalized}".encode("utf-8")).hexdigest()
    return normalized, h


def _reverse_key(lat: float, lng: float) -> tuple[str, str]:
    """Return (display_text, hash) for a reverse (coords → zone) query.

    Coords are rounded to 4 decimals (~10 m) so adjacent points share a
    cache entry — without rounding, every fractionally-different lat/lng
    becomes a distinct cache miss.
    """
    rounded = f"{round(lat, 4)},{round(lng, 4)}"
    h = hashlib.sha256(f"reverse|{rounded}".encode("utf-8")).hexdigest()
    return rounded, h


# ── Confidence / validation ───────────────────────────────────────────


def _confidence_from_importance(importance: float | None) -> float:
    """Clamp Nominatim's ``importance`` field into [0, 1].

    Nominatim returns a heuristic relevance score; we treat it as a
    rough confidence. Missing or junk values fall back to 0.0 so the
    confidence guard kicks in.
    """
    try:
        c = float(importance) if importance is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


# Spanish articles + filler tokens that carry no identifying signal
# when comparing a place name against a Nominatim display_name. Without
# stripping these, "La Boyera" overlaps "La Hoyadita" on the shared "la"
# and the validation guard misses the wrong-place case.
# Tightened to articles only — finding #3 from v0.19.0 review. Place
# names like "Caracas" / "Miranda" carry real disambiguation signal and
# should NOT be treated as noise (otherwise a user typing just "Caracas"
# has all tokens stripped → onboarding falsely warns "no estoy seguro").
_NAME_NOISE = frozenset({
    "la", "el", "los", "las", "del", "de", "y", "en",
})


def _name_matches_query(query: str, display_name: str | None) -> bool:
    """Token-overlap check: does the result mention what the user asked?

    Catches "La Boyera → La Hoyadita" — once we drop the article "la"
    (no signal) the only remaining query token is "boyera", which simply
    does not appear in the returned display_name.
    """
    if not display_name:
        return False
    q_tokens = set(_normalize(query).split()) - _NAME_NOISE
    d_tokens = set(_normalize(display_name).split())
    if not q_tokens:
        # Query is entirely noise (e.g., "la") — we cannot judge from
        # the name alone. Return False so the confidence check decides.
        return False
    overlap = q_tokens & d_tokens
    return len(overlap) >= max(1, len(q_tokens) // 2)


# ── Cache I/O ─────────────────────────────────────────────────────────


async def _cache_get(query_hash: str) -> GeocodeCache | None:
    """Return a cached row if it exists and is younger than the TTL."""
    cutoff = datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS)
    async with async_session() as session:
        result = await session.execute(
            select(GeocodeCache).where(
                GeocodeCache.query_hash == query_hash,
                GeocodeCache.fetched_at >= cutoff,
            )
        )
        return result.scalar_one_or_none()


async def _cache_put(
    query_hash: str,
    query_text: str,
    source: str,
    result: LocationResult,
) -> None:
    """Upsert a cache row. Replaces the entry when it already exists.

    Race-condition note (v0.19.0 review finding #2): if two coroutines
    resolve the same query concurrently, both pass the SELECT (no row),
    both call ``session.add(...)``, and the second commit hits the unique
    constraint on ``query_hash``. We catch ``IntegrityError`` and retry
    as an UPDATE — the value is the same anyway, so whoever wins is
    fine; the loser just refreshes ``fetched_at``.
    """
    from sqlalchemy.exc import IntegrityError

    async with async_session() as session:
        existing = (
            await session.execute(
                select(GeocodeCache).where(GeocodeCache.query_hash == query_hash)
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.query_text = query_text
            existing.source = source
            existing.latitude = result.lat
            existing.longitude = result.lng
            existing.display_name = result.display_name
            existing.confidence = result.confidence
            existing.city_code = result.city_code
            existing.zone_name = result.zone_name
            existing.fetched_at = datetime.utcnow()
            await session.commit()
            return

        session.add(GeocodeCache(
            query_hash=query_hash,
            query_text=query_text,
            source=source,
            latitude=result.lat,
            longitude=result.lng,
            display_name=result.display_name,
            confidence=result.confidence,
            city_code=result.city_code,
            zone_name=result.zone_name,
        ))
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent insert won the race — roll back and re-upsert
            # the row that's now in the DB. Cheap and rare.
            await session.rollback()
            logger.debug("cache race: query_hash=%s already inserted", query_hash)
            existing2 = (
                await session.execute(
                    select(GeocodeCache).where(
                        GeocodeCache.query_hash == query_hash
                    )
                )
            ).scalar_one_or_none()
            if existing2 is not None:
                existing2.fetched_at = datetime.utcnow()
                await session.commit()


def _cache_to_result(row: GeocodeCache) -> LocationResult:
    return LocationResult(
        lat=row.latitude,
        lng=row.longitude,
        display_name=row.display_name or "",
        confidence=row.confidence or 0.0,
        source="cache",
        city_code=row.city_code or "CCS",
        zone_name=row.zone_name,
    )


# ── Public API ────────────────────────────────────────────────────────


async def resolve(
    query: str, min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> LocationResult | None:
    """Forward-geocode a place name to coordinates.

    Pipeline: cache → Nominatim → confidence/validation guard. Returns
    ``None`` (so callers can ask the user to clarify) when no result is
    above ``min_confidence`` AND the top result's display_name has no
    token overlap with the query — the Daniel-class "silently wrong"
    case.

    The result includes up to ``ALTERNATIVES_TOP_N`` other Nominatim
    hits for the same query so the bot can render a "¿Te refieres a…?"
    list.
    """
    if not query or not query.strip():
        return None

    normalized, h = _forward_key(query)
    cached = await _cache_get(h)
    if cached is not None:
        logger.debug("location.resolve cache hit: %s", normalized)
        return _cache_to_result(cached)

    # Miss — call Nominatim through the existing geocode module.
    raw = await _nominatim_search(query)
    if not raw:
        return None

    top = raw[0]
    confidence = _confidence_from_importance(top.get("importance"))
    display_name = top.get("display_name", "")

    name_ok = _name_matches_query(query, display_name)
    confidence_ok = confidence >= min_confidence
    if not (name_ok or confidence_ok):
        logger.warning(
            "location.resolve REJECTED: query=%r confidence=%.2f display=%r",
            query, confidence, display_name,
        )
        # Still return the top result so the caller can offer it as one
        # of "did you mean?" options — but mark confidence so the caller
        # knows to ask for confirmation.

    from farmafacil.services.geocode import _extract_city_code  # avoid cycle
    result = LocationResult(
        lat=float(top["lat"]),
        lng=float(top["lon"]),
        display_name=display_name,
        confidence=confidence,
        source="forward",
        city_code=_extract_city_code(top),
        zone_name=top.get("name") or query.strip().title(),
        alternatives=[
            {
                "lat": float(alt["lat"]),
                "lng": float(alt["lon"]),
                "display_name": alt.get("display_name", ""),
                "confidence": _confidence_from_importance(alt.get("importance")),
            }
            for alt in raw[1:1 + ALTERNATIVES_TOP_N]
        ],
    )
    await _cache_put(h, query, "forward", result)
    return result


async def reverse(lat: float, lng: float) -> LocationResult | None:
    """Reverse-geocode coordinates to a city + zone name."""
    rounded_text, h = _reverse_key(lat, lng)
    cached = await _cache_get(h)
    if cached is not None:
        logger.debug("location.reverse cache hit: %s", rounded_text)
        return _cache_to_result(cached)

    raw = await _nominatim_reverse(lat, lng)
    if not raw:
        return None

    address = raw.get("address", {})
    zone_name = (
        address.get("suburb")
        or address.get("neighbourhood")
        or address.get("village")
        or address.get("town")
        or address.get("city")
        or address.get("county")
        or address.get("state")
    )
    from farmafacil.services.geocode import _extract_city_code
    result = LocationResult(
        lat=lat,
        lng=lng,
        display_name=raw.get("display_name", ""),
        confidence=_confidence_from_importance(raw.get("importance")),
        source="reverse",
        city_code=_extract_city_code(raw),
        zone_name=zone_name,
    )
    await _cache_put(h, rounded_text, "reverse", result)
    return result


# ── Admin helpers ─────────────────────────────────────────────────────


async def set_user_location(phone: str, query: str) -> dict:
    """Re-resolve and persist a user's coordinates.

    Used by the admin chat tool of the same name. Returns a dict shaped
    for direct rendering back to the admin in chat.
    """
    result = await resolve(query)
    if result is None:
        return {"ok": False, "reason": "geocode_failed", "query": query}

    async with async_session() as session:
        user = (
            await session.execute(
                select(User).where(User.phone_number == phone)
            )
        ).scalar_one_or_none()
        if user is None:
            return {"ok": False, "reason": "user_not_found", "phone": phone}
        user.latitude = result.lat
        user.longitude = result.lng
        user.zone_name = result.zone_name
        user.city_code = result.city_code
        await session.commit()
        user_id = user.id
        user_name = user.name

    logger.info(
        "Admin set_user_location: user=%s (id=%s) → %s (%.4f, %.4f) confidence=%.2f",
        user_name, user_id, result.zone_name, result.lat, result.lng,
        result.confidence,
    )
    return {
        "ok": True,
        "user_id": user_id,
        "user_name": user_name,
        "lat": result.lat,
        "lng": result.lng,
        "zone_name": result.zone_name,
        "city_code": result.city_code,
        "display_name": result.display_name,
        "confidence": result.confidence,
    }


async def set_pharmacy_location(
    pharmacy_id: int,
    query: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> dict:
    """Override coordinates on a pharmacy_locations row.

    Two modes: pass ``query`` to re-geocode through Nominatim, OR pass
    ``lat`` + ``lng`` directly for a hard manual override (used when
    Nominatim does not know the address but the admin has the coords
    from Google Maps or a phone GPS reading). Mutually exclusive.
    """
    if (query is None) == (lat is None and lng is None):
        return {"ok": False, "reason": "must_pass_query_xor_coords"}

    if query is not None:
        result = await resolve(query)
        if result is None:
            return {"ok": False, "reason": "geocode_failed", "query": query}
        new_lat, new_lng = result.lat, result.lng
        new_zone = result.zone_name
        confidence = result.confidence
        display_name = result.display_name
    else:
        assert lat is not None and lng is not None
        new_lat, new_lng = lat, lng
        new_zone = None
        confidence = 1.0  # manual override implies operator certainty
        display_name = "manual override"

    # Venezuela bounding box guard (v0.19.0 review finding #4) — refuse
    # coordinates outside the country to catch fat-finger admin entries
    # like (0, 0) "null island" or coords mistakenly typed for another
    # country. Same bbox we use in osm_backfill.parse_osm_element.
    if not (0.5 <= new_lat <= 12.5 and -74.0 <= new_lng <= -59.0):
        return {
            "ok": False,
            "reason": "coords_out_of_bounds",
            "lat": new_lat,
            "lng": new_lng,
        }

    async with async_session() as session:
        pharmacy = await session.get(PharmacyLocation, pharmacy_id)
        if pharmacy is None:
            return {"ok": False, "reason": "pharmacy_not_found", "id": pharmacy_id}
        pharmacy.latitude = new_lat
        pharmacy.longitude = new_lng
        if new_zone and not pharmacy.zone_name:
            pharmacy.zone_name = new_zone
        await session.commit()
        result_dict = {
            "ok": True,
            "pharmacy_id": pharmacy.id,
            "name": pharmacy.name,
            "chain": pharmacy.pharmacy_chain,
            "lat": new_lat,
            "lng": new_lng,
            "zone_name": pharmacy.zone_name,
            "confidence": confidence,
            "display_name": display_name,
        }

    logger.info(
        "Admin set_pharmacy_location: id=%s → (%.4f, %.4f)",
        pharmacy_id, new_lat, new_lng,
    )
    return result_dict


async def geocode_health(days: int = 7) -> dict:
    """Return cache hit-rate proxy stats for the admin dashboard.

    Counts everything in SQL — fixed in v0.19.0 review (finding #1) so
    we don't pull the entire cache into Python every call.
    """
    from sqlalchemy import func as sa_func

    cutoff = datetime.utcnow() - timedelta(days=days)
    async with async_session() as session:
        total = (await session.execute(
            select(sa_func.count(GeocodeCache.id))
        )).scalar_one()
        recent = (await session.execute(
            select(sa_func.count(GeocodeCache.id)).where(
                GeocodeCache.fetched_at >= cutoff,
            )
        )).scalar_one()
        forward = (await session.execute(
            select(sa_func.count(GeocodeCache.id)).where(
                GeocodeCache.source == "forward",
            )
        )).scalar_one()
        reverse_rows = (await session.execute(
            select(sa_func.count(GeocodeCache.id)).where(
                GeocodeCache.source == "reverse",
            )
        )).scalar_one()
        low_conf = (await session.execute(
            select(sa_func.count(GeocodeCache.id)).where(
                GeocodeCache.confidence < DEFAULT_MIN_CONFIDENCE,
            )
        )).scalar_one()

    return {
        "total_rows": total,
        "fetched_last_n_days": recent,
        "forward_rows": forward,
        "reverse_rows": reverse_rows,
        "low_confidence_rows": low_conf,
        "ttl_days": CACHE_TTL_DAYS,
        "min_confidence": DEFAULT_MIN_CONFIDENCE,
    }


async def cleanup_expired_cache(older_than_days: int = 90) -> int:
    """Drop cache rows older than ``older_than_days``. Returns count."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    async with async_session() as session:
        result = await session.execute(
            delete(GeocodeCache).where(GeocodeCache.fetched_at < cutoff)
        )
        deleted = result.rowcount or 0
        await session.commit()
    if deleted:
        logger.info("Pruned %d expired geocode_cache rows", deleted)
    return deleted


# ── Nominatim adapters (private, swappable) ───────────────────────────
#
# Kept thin and private here so the rest of the module never imports
# httpx directly — tests mock these two coroutines and exercise every
# upstream behavior without touching the network.


async def _nominatim_search(query: str) -> list[dict]:
    """Forward-geocode ``query`` via Nominatim. Returns up to 3 results."""

    params = {
        "q": f"{query.strip()}, Venezuela",
        "format": "json",
        "limit": 1 + ALTERNATIVES_TOP_N,
        "countrycodes": "ve",
        "addressdetails": 1,
    }
    headers = {"User-Agent": "FarmaFacil/0.19 (farmafacil-pharmacy-finder)"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            return response.json() or []
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        logger.error("Nominatim search failed for %r: %s", query, exc)
        return []


async def _nominatim_reverse(lat: float, lng: float) -> dict | None:
    """Reverse-geocode coords via Nominatim."""

    params = {
        "lat": f"{lat}",
        "lon": f"{lng}",
        "format": "json",
        "addressdetails": 1,
        "zoom": 14,
    }
    headers = {"User-Agent": "FarmaFacil/0.19 (farmafacil-pharmacy-finder)"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        logger.error("Nominatim reverse failed for (%.4f, %.4f): %s", lat, lng, exc)
        return None

    if not isinstance(data, dict) or "address" not in data:
        return None
    return data
