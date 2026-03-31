"""Format drug search results for WhatsApp messages."""

from collections import defaultdict
from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchResponse

MAX_PRODUCTS = 8
MAX_STORES_PER_PHARMACY = 3


def _group_by_product(results: list[DrugResult]) -> list[tuple[str, list[DrugResult]]]:
    """Group results by product name, preserving order of first appearance.

    Returns a list of (product_name, [results_from_different_pharmacies]).
    Within each group, available items come first, sorted by price.
    """
    groups: dict[str, list[DrugResult]] = defaultdict(list)
    order: list[str] = []

    for r in results:
        name = r.drug_name
        if name not in groups:
            order.append(name)
        # Avoid duplicates from same pharmacy (same product listed multiple times)
        already = [existing for existing in groups[name]
                   if existing.pharmacy_name == r.pharmacy_name]
        if not already:
            groups[name].append(r)

    # Sort pharmacies within each product: available first, then by price
    for name in groups:
        groups[name].sort(
            key=lambda r: (
                0 if r.available else 1,
                r.price_bs if r.price_bs is not None else Decimal("999999"),
            )
        )

    return [(name, groups[name]) for name in order]


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
        return (
            f"No encontramos resultados para *{response.query}*.\n\n"
            "Intenta con otro nombre o revisa la ortografia."
        )

    product_groups = _group_by_product(response.results)
    pharmacies = ", ".join(response.searched_pharmacies)
    zone_label = f" cerca de *{response.zone}*" if response.zone else ""

    lines = [
        f"*{response.query}*{zone_label} — "
        f"{len(product_groups)} producto(s)\n"
        f"Farmacias: _{pharmacies}_\n"
    ]

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

    lines.append(
        "\nEnvia otro medicamento para buscar."
        "\n_cambiar zona_ \u00b7 _ayuda_"
    )
    return "\n".join(lines)
