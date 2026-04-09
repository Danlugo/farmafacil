"""Format drug search results for WhatsApp messages."""

from collections import defaultdict
from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchResponse

MAX_PRODUCTS = 8
MAX_STORES_PER_PHARMACY = 3


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

        line = f"*{i}. {name}*\n   \U0001f4cd {chain} — {dist:.1f} km"
        if address:
            line += f"\n   {address}"
        lines.append(line)

    lines.append(
        "\nEnvia el nombre de un producto para buscar disponibilidad."
        "\n_cambiar zona_ \u00b7 _ayuda_"
    )
    return "\n".join(lines)
