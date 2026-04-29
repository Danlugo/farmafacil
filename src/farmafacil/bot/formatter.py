"""Format drug search results for WhatsApp messages."""

from collections import defaultdict
from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchResponse

import re

MAX_PRODUCTS = 8
MAX_STORES_PER_PHARMACY = 3
# Truncate raw OSM opening_hours strings to keep WhatsApp lines short.
# Full OSM strings can be 80+ chars (e.g., "Mo-Fr 08:00-20:00; Sa 09:00-18:00; Su 10:00-14:00").
HOURS_DISPLAY_MAXLEN = 40
URL_DISPLAY_MAXLEN = 40

# Strip control characters and bidi-override codepoints from user-generated
# OSM tag values before rendering. WhatsApp plain-text has no XSS surface,
# but stray LTR/RTL overrides or zero-width chars can garble lines or
# confuse downstream parsers. (From v0.18.0 code-review finding #3.)
_OSM_STRIP_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f​-‍‪-‮]")


def _sanitize_osm_text(value: str | None) -> str | None:
    """Remove control / bidi-override characters from OSM-sourced strings."""
    if value is None:
        return None
    cleaned = _OSM_STRIP_PATTERN.sub("", value)
    return cleaned or None


def _short_hours(opening_hours: str) -> str:
    """Compact an OSM opening_hours string for WhatsApp display.

    OSM hours can be verbose ("Mo-Fr 08:00-20:00; Sa 09:00-18:00; Su 10:00-14:00").
    For chat display we keep the first segment and append an ellipsis if
    truncation occurred — users who need full hours can tap through to
    Google Maps via the address.
    """
    s = opening_hours.strip()
    if len(s) <= HOURS_DISPLAY_MAXLEN:
        return s
    # Keep up to the first ';' or comma — a single weekday range is usually
    # informative enough.
    head = s.split(";", 1)[0].strip()
    if len(head) <= HOURS_DISPLAY_MAXLEN:
        return head + " ..."
    return head[: HOURS_DISPLAY_MAXLEN - 3] + "..."


def _short_url(url: str) -> str:
    """Strip protocol prefix and trailing slash for compact display."""
    s = url.strip()
    for prefix in ("https://", "http://"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.rstrip("/")
    if len(s) <= URL_DISPLAY_MAXLEN:
        return s
    return s[: URL_DISPLAY_MAXLEN - 3] + "..."


def _group_by_product(results: list[DrugResult]) -> list[tuple[str, list[DrugResult]]]:
    """Group results by product name, dedup, and interleave across pharmacies.

    1. Group by exact product name, dedup same-pharmacy entries.
    2. Sort pharmacies within each group (available first, then by price).
    3. Interleave output so products alternate between pharmacy chains.

    Returns a list of (product_name, [results_from_different_pharmacies]).
    """
    groups: dict[str, list[DrugResult]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()  # (product_name, pharmacy_name)

    for r in results:
        key = (r.drug_name, r.pharmacy_name)
        if key not in seen:
            seen.add(key)
            groups[r.drug_name].append(r)

    # Sort pharmacies within each product: available first, then by price
    for name in groups:
        groups[name].sort(
            key=lambda r: (
                0 if r.available else 1,
                r.price_bs if r.price_bs is not None else Decimal("999999"),
            )
        )

    # Split products by their primary pharmacy (first/cheapest), then interleave
    by_pharmacy: dict[str, list[str]] = defaultdict(list)
    for name, pharmacy_results in groups.items():
        primary = pharmacy_results[0].pharmacy_name
        by_pharmacy[primary].append(name)

    # Round-robin across pharmacy chains
    interleaved: list[str] = []
    chains = sorted(by_pharmacy.keys())
    indices = {chain: 0 for chain in chains}

    total = sum(len(v) for v in by_pharmacy.values())
    while len(interleaved) < total:
        added = False
        for chain in chains:
            idx = indices[chain]
            if idx < len(by_pharmacy[chain]):
                interleaved.append(by_pharmacy[chain][idx])
                indices[chain] = idx + 1
                added = True
        if not added:
            break

    return [(name, groups[name]) for name in interleaved]


def _format_price(result: DrugResult) -> str:
    """Format price with discount info for a result."""
    if result.price_bs is None:
        return ""
    price_str = f"Bs. {result.price_bs:,.2f}"
    if result.full_price_bs and result.full_price_bs != result.price_bs:
        price_str += f" ~Bs. {result.full_price_bs:,.2f}~"
    if result.discount_pct:
        price_str += f" ({result.discount_pct})"
    return price_str


def format_search_results(response: SearchResponse) -> str:
    """Format a SearchResponse into a WhatsApp-friendly text message.

    Groups results by product name. Under each product, lists each
    pharmacy chain that carries it, and under each pharmacy, the
    nearest stores with distances.

    Layout:
    *1. Product Name 📋
       🏥 Pharmacy A — Bs. X.XX (~Bs. Y.YY~) (20%) | N tiendas
          📍 Store — X.X km — Bs. X.XX
          📍 Store — X.X km — Bs. X.XX
       🏥 Pharmacy B — Bs. Z.ZZ | Sin stock

    Args:
        response: Search results from the drug search service.

    Returns:
        Formatted text message in Spanish.
    """
    if response.total == 0:
        failed = response.failed_pharmacies
        # Strip "(cache)" / "(catalogo)" suffixes — they are observability
        # labels added by the cache/catalog paths, not real scraper calls.
        # Use endswith so pharmacy names containing these substrings aren't
        # accidentally filtered out.
        queried = [
            p for p in response.searched_pharmacies
            if not (p.endswith(" (cache)") or p.endswith(" (catalogo)"))
        ]

        if failed and queried and len(failed) >= len(queried):
            # All queried pharmacies failed — no data at all
            failed_list = ", ".join(failed)
            return (
                f"\u26a0\ufe0f No pudimos conectar con {failed_list} ahora mismo.\n\n"
                "Intenta de nuevo en unos minutos."
            )
        if failed:
            # Partial failure — some returned empty, some errored
            failed_list = ", ".join(failed)
            return (
                f"No encontramos *{response.query}*.\n\n"
                f"\u26a0\ufe0f Ademas, no pudimos conectar con {failed_list}. "
                "Intenta de nuevo en unos minutos."
            )
        return (
            f"No encontramos resultados para *{response.query}*.\n\n"
            "Intenta con otro nombre o revisa la ortografia."
        )

    product_groups = _group_by_product(response.results)
    pharmacies = ", ".join(
        p.replace(" (cache)", "").replace(" (catalogo)", "")
        for p in response.searched_pharmacies
    )
    zone_label = f" cerca de *{response.zone}*" if response.zone else ""

    header = (
        f"*{response.query}*{zone_label} — "
        f"{len(product_groups)} producto(s)\n"
        f"Farmacias: _{pharmacies}_\n"
    )
    if response.failed_pharmacies:
        failed_list = ", ".join(response.failed_pharmacies)
        header += (
            f"\u26a0\ufe0f No pudimos conectar con {failed_list} — "
            "resultados parciales.\n"
        )
    lines = [header]

    for i, (product_name, pharmacy_results) in enumerate(product_groups[:MAX_PRODUCTS], 1):
        # Check if any result in this group requires prescription
        rx_label = ""
        if any(r.requires_prescription for r in pharmacy_results):
            rx_label = " \U0001f4cb"

        # Product header
        line = f"*{i}. {product_name}*{rx_label}"

        # Each pharmacy that carries this product
        for result in pharmacy_results:
            pharmacy_line = f"\n   \U0001f3e5 {result.pharmacy_name}"
            price_str = _format_price(result)
            if price_str:
                pharmacy_line += f" — {price_str}"
            if result.stores_in_stock > 0:
                pharmacy_line += f" | {result.stores_in_stock} tiendas"
            if not result.available:
                pharmacy_line += " | _Sin stock_"
            line += pharmacy_line

            # Nearby stores
            if result.nearby_stores:
                for store in result.nearby_stores[:MAX_STORES_PER_PHARMACY]:
                    store_line = (
                        f"\n      \U0001f4cd {store.store_name}"
                        f" — {store.distance_km:.1f} km"
                    )
                    if store.price_bs is not None:
                        store_line += f" — Bs. {store.price_bs:,.2f}"
                    line += store_line

        lines.append(line)

    remaining = len(product_groups) - MAX_PRODUCTS
    if remaining > 0:
        lines.append(f"\n... y {remaining} productos mas.")

    if response.similar_count > 0:
        lines.append(
            f"\n\U0001f50d Tambien encontramos *{response.similar_count}* productos similares."
            "\nEnvia _ver similares_ para verlos."
        )

    lines.append(
        "\nEnvia otro medicamento para buscar."
        "\n_cambiar zona_ \u00b7 _ayuda_"
    )
    return "\n".join(lines)


def format_nearby_stores(
    stores: list[dict],
    zone_name: str | None = None,
) -> str:
    """Format a list of nearby stores for WhatsApp.

    Args:
        stores: List of store dicts from get_all_nearby_stores().
        zone_name: User's zone name for display.

    Returns:
        Formatted WhatsApp message.
    """
    if not stores:
        return (
            "\U0001f3e5 No encontramos farmacias cercanas a tu ubicacion.\n\n"
            "Intenta _cambiar zona_ para actualizar tu ubicacion."
        )

    zone_label = f" cerca de *{zone_name}*" if zone_name else ""
    lines = [f"\U0001f3e5 *Farmacias cercanas*{zone_label}\n"]

    for i, store in enumerate(stores, 1):
        chain = store["pharmacy_chain"]
        name = store["store_name"]
        dist = store["distance_km"]
        address = store["address"]
        zone = _sanitize_osm_text(store.get("zone_name"))
        hours = _sanitize_osm_text(store.get("opening_hours"))
        is_24h = store.get("is_24h", False)
        phone = _sanitize_osm_text(store.get("phone"))
        website = _sanitize_osm_text(store.get("website"))

        # First line: chain prefix unless this is an independent — Item 46.
        # The user explicitly asked for no "(Independiente)" suffix, so we
        # just lead with the pharmacy name when there's no real chain.
        is_independent = (chain or "").lower() == "independiente"
        title = name if is_independent else f"{chain} {name}"

        line = f"*{i}. {title}*"

        # Location line — zone (if known) + distance
        loc_bits = []
        if zone:
            loc_bits.append(zone)
        loc_bits.append(f"{dist:.1f} km")
        line += f"\n   \U0001f4cd {' — '.join(loc_bits)}"

        if address:
            line += f"\n   {address}"

        # Hours: prefer the 24h flag (compact) over the raw hours string.
        if is_24h:
            line += "\n   \U0001f319 24 horas"
        elif hours:
            line += f"\n   \U0001f550 {_short_hours(hours)}"

        if phone:
            line += f"\n   \U0001f4de {phone}"
        if website:
            line += f"\n   \U0001f310 {_short_url(website)}"

        lines.append(line)

    lines.append(
        "\nEnvia el nombre de un producto para buscar disponibilidad."
        "\n_cambiar zona_ \u00b7 _ayuda_"
    )
    return "\n".join(lines)
