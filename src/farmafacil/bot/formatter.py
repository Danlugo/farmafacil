"""Format drug search results for WhatsApp messages."""

from collections import defaultdict
from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchResponse

MAX_RESULTS_PER_PHARMACY = 4
MAX_STORES_PER_PHARMACY = 3


def _interleave_by_pharmacy(results: list[DrugResult]) -> list[DrugResult]:
    """Interleave results round-robin across pharmacies, sorted by price within each.

    Ensures all pharmacy chains are represented in the visible results,
    even when one chain has much lower prices than another.
    """
    by_pharmacy: dict[str, list[DrugResult]] = defaultdict(list)
    for r in results:
        by_pharmacy[r.pharmacy_name].append(r)

    for name in by_pharmacy:
        by_pharmacy[name].sort(
            key=lambda r: r.price_bs if r.price_bs is not None else Decimal("999999")
        )
        available = [r for r in by_pharmacy[name] if r.available]
        unavailable = [r for r in by_pharmacy[name] if not r.available]
        by_pharmacy[name] = available + unavailable

    interleaved: list[DrugResult] = []
    pharmacy_names = sorted(by_pharmacy.keys())
    indices = {name: 0 for name in pharmacy_names}

    while len(interleaved) < len(results):
        added = False
        for name in pharmacy_names:
            idx = indices[name]
            if idx < len(by_pharmacy[name]) and idx < MAX_RESULTS_PER_PHARMACY:
                interleaved.append(by_pharmacy[name][idx])
                indices[name] = idx + 1
                added = True
        if not added:
            break

    return interleaved


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


def _format_store_price(store) -> str:
    """Format a per-store price line."""
    if store.price_bs is not None:
        return f"Bs. {store.price_bs:,.2f}"
    return ""


def format_search_results(response: SearchResponse) -> str:
    """Format a SearchResponse into a WhatsApp-friendly text message.

    Layout per product:
    *1. Product Name 📋
       🏥 Pharmacy — Bs. X.XX ~Bs. Y.YY~ (20%) | N tiendas
          📍 Store — X.X km
          📍 Store — X.X km

    Args:
        response: Search results from the drug search service.

    Returns:
        Formatted text message in Spanish.
    """
    if response.total == 0:
        return (
            f"No encontramos resultados para *{response.query}*.\n\n"
            "Intenta con otro nombre o revisa la ortografia."
        )

    display_results = _interleave_by_pharmacy(response.results)
    pharmacies = ", ".join(response.searched_pharmacies)
    zone_label = f" cerca de *{response.zone}*" if response.zone else ""

    lines = [
        f"*{response.query}*{zone_label} — "
        f"{response.total} resultado(s)\n"
        f"Farmacias: _{pharmacies}_\n"
    ]

    for i, result in enumerate(display_results, 1):
        rx_label = " \U0001f4cb" if result.requires_prescription else ""

        # Product name header
        line = f"*{i}. {result.drug_name}*{rx_label}"

        # Pharmacy line with price
        pharmacy_line = f"\n   \U0001f3e5 {result.pharmacy_name}"
        price_str = _format_price(result)
        if price_str:
            pharmacy_line += f" — {price_str}"
        if result.stores_in_stock > 0:
            pharmacy_line += f" | {result.stores_in_stock} tiendas"
        if not result.available:
            pharmacy_line += " | _Sin stock_"
        line += pharmacy_line

        # Nearby stores with distance (and per-store price if available)
        if result.nearby_stores:
            for store in result.nearby_stores[:MAX_STORES_PER_PHARMACY]:
                store_line = f"\n      \U0001f4cd {store.store_name} — {store.distance_km:.1f} km"
                store_price = _format_store_price(store)
                if store_price:
                    store_line += f" — {store_price}"
                line += store_line

        lines.append(line)

    remaining = response.total - len(display_results)
    if remaining > 0:
        lines.append(f"\n... y {remaining} resultados mas.")

    lines.append(
        "\nEnvia otro medicamento para buscar."
        "\n_cambiar zona_ \u00b7 _ayuda_"
    )
    return "\n".join(lines)
