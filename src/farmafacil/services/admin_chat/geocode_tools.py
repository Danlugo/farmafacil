"""Admin chat tools: geocode and location admin (v0.19.0, Item 47)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _tool_geocode_query(args: dict[str, Any]) -> str:
    """Resolve a place name through Nominatim + cache, show what we got."""
    from farmafacil.services.location import resolve

    text = (args.get("text") or args.get("query") or "").strip()
    if not text:
        return "Falta el argumento 'text'."

    result = await resolve(text)
    if result is None:
        return f"Sin resultados para {text!r}."

    lines = [
        f"📍 Resolved: {text!r}",
        f"  → {result.zone_name} ({result.city_code}) at {result.lat:.4f}, {result.lng:.4f}",
        f"  display: {result.display_name or '—'}",
        f"  confidence: {result.confidence:.2f}",
        f"  source: {result.source}",
    ]
    if result.alternatives:
        lines.append("  alternativas:")
        for alt in result.alternatives:
            lines.append(
                f"    • {alt['display_name']} "
                f"({alt['lat']:.4f}, {alt['lng']:.4f}) c={alt['confidence']:.2f}"
            )
    return "\n".join(lines)


async def _tool_geocode_reverse(args: dict[str, Any]) -> str:
    """Reverse-geocode coordinates and report what's there."""
    from farmafacil.services.location import reverse

    try:
        lat = float(args["lat"])
        lng = float(args["lng"])
    except (KeyError, TypeError, ValueError):
        return "Faltan o son inválidos los argumentos lat/lng."

    result = await reverse(lat, lng)
    if result is None:
        return f"Sin resultados para ({lat}, {lng})."

    return (
        f"📍 ({lat:.4f}, {lng:.4f}) → {result.zone_name or '—'} "
        f"({result.city_code})\n"
        f"  display: {result.display_name or '—'}\n"
        f"  source: {result.source}"
    )


async def _tool_set_user_location(args: dict[str, Any]) -> str:
    """Re-resolve and persist a user's coords from a place-name query.

    This is the explicit fix-it tool for Daniel-class onboarding bugs.
    """
    from farmafacil.services.location import set_user_location

    phone = args.get("phone") or args.get("user_ref")
    query = args.get("query") or args.get("text")
    if not phone or not query:
        return "Faltan argumentos: phone, query."

    out = await set_user_location(str(phone), str(query))
    if not out["ok"]:
        return f"❌ {out.get('reason', 'unknown')} (query={query!r}, phone={phone!r})."

    warning = ""
    if out["confidence"] < 0.5:
        warning = f"\n  ⚠️ baja confianza ({out['confidence']:.2f}) — verificá manualmente"

    return (
        f"✅ {out['user_name']} (id={out['user_id']}) updated\n"
        f"  zone: {out['zone_name']} ({out['city_code']})\n"
        f"  coords: {out['lat']:.4f}, {out['lng']:.4f}\n"
        f"  display: {out.get('display_name', '—')}"
        + warning
    )


async def _tool_set_pharmacy_location(args: dict[str, Any]) -> str:
    """Override coords on a pharmacy. Pass query OR (lat, lng) — not both."""
    from farmafacil.services.location import set_pharmacy_location

    try:
        pharmacy_id = int(args["pharmacy_id"])
    except (KeyError, TypeError, ValueError):
        return "Falta o es inválido el argumento 'pharmacy_id'."

    query = args.get("query")
    lat = args.get("lat")
    lng = args.get("lng")

    if query is not None and (lat is not None or lng is not None):
        return "Pasa SOLO query o SOLO (lat, lng) — no ambos."
    if query is None and (lat is None or lng is None):
        return "Faltan argumentos: query o (lat, lng)."

    out = await set_pharmacy_location(
        pharmacy_id,
        query=str(query) if query else None,
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
    )
    if not out["ok"]:
        return f"❌ {out.get('reason', 'unknown')}"

    return (
        f"✅ {out['chain']} {out['name']} (id={out['pharmacy_id']}) updated\n"
        f"  coords: {out['lat']:.4f}, {out['lng']:.4f}\n"
        f"  zone: {out.get('zone_name') or '—'}\n"
        f"  display: {out.get('display_name', '—')}"
    )


async def _tool_geocode_health(args: dict[str, Any]) -> str:
    """Cache health stats for the geocode pipeline."""
    from farmafacil.services.location import geocode_health

    days_arg = args.get("days", 7)
    try:
        days = int(days_arg)
    except (TypeError, ValueError):
        days = 7

    stats = await geocode_health(days=days)
    return (
        f"📊 Geocode cache health\n"
        f"  total rows: {stats['total_rows']}\n"
        f"  fetched in last {days}d: {stats['fetched_last_n_days']}\n"
        f"  forward: {stats['forward_rows']}\n"
        f"  reverse: {stats['reverse_rows']}\n"
        f"  low-confidence (<{stats['min_confidence']}): {stats['low_confidence_rows']}\n"
        f"  TTL: {stats['ttl_days']} days"
    )
