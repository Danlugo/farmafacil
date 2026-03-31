"""Format drug search results for WhatsApp messages."""

from decimal import Decimal

from farmafacil.models.schemas import DrugResult, SearchResponse

MAX_RESULTS_SHOWN = 8


def _sort_by_price(results: list[DrugResult]) -> list[DrugResult]:
    """Sort results by price ascending. Items without price go last."""
    return sorted(
        results,
        key=lambda r: r.price_bs if r.price_bs is not None else Decimal("999999"),
    )


def format_search_results(response: SearchResponse) -> str:
    """Format a SearchResponse into a WhatsApp-friendly text message.

    Results are sorted by price (lowest first) and grouped with their
    pharmacy name so the user can compare across chains.

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

    sorted_results = _sort_by_price(response.results)
    pharmacies = ", ".join(response.searched_pharmacies)
    zone_label = f" cerca de *{response.zone}*" if response.zone else ""

    lines = [
        f"*{response.query}*{zone_label} — "
        f"{response.total} resultado(s)\n"
        f"Farmacias: _{pharmacies}_\n"
    ]

    for i, result in enumerate(sorted_results[:MAX_RESULTS_SHOWN], 1):
        stock_icon = "\u2705" if result.available else "\u274c"
        rx_label = " \U0001f4cb" if result.requires_prescription else ""

        line = f"*{i}.* {stock_icon} {result.drug_name}{rx_label}"
        line += f"\n   \U0001f3e5 {result.pharmacy_name}"

        # Price info
        if result.price_bs is not None:
            price_str = f"Bs. {result.price_bs:,.2f}"
            if result.full_price_bs and result.full_price_bs != result.price_bs:
                price_str += f" ~Bs. {result.full_price_bs:,.2f}~"
            if result.discount_pct:
                price_str += f" ({result.discount_pct})"
            line += f" — {price_str}"

        # Nearby stores
        if result.nearby_stores:
            closest = result.nearby_stores[0]
            line += f"\n   \U0001f4cd {closest.store_name} — {closest.distance_km:.1f} km"
        elif result.stores_in_stock > 0:
            line += f" | {result.stores_in_stock} tiendas"

        if not result.available:
            line += "\n   _Sin stock_"

        lines.append(line)

    if response.total > MAX_RESULTS_SHOWN:
        lines.append(f"\n... y {response.total - MAX_RESULTS_SHOWN} resultados mas.")

    lines.append(
        "\nEnvia otro medicamento para buscar."
        "\n_cambiar zona_ · _ayuda_"
    )
    return "\n".join(lines)
