"""Format drug search results for WhatsApp messages."""

from farmafacil.models.schemas import SearchResponse


def format_search_results(response: SearchResponse) -> str:
    """Format a SearchResponse into a WhatsApp-friendly text message.

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

    lines = [f"Encontramos *{response.total}* resultado(s) para *{response.query}*:\n"]

    for i, result in enumerate(response.results[:5], 1):
        stock_icon = "\u2705" if result.available else "\u274c"
        rx_icon = " \U0001f4cb" if result.requires_prescription else ""

        line = f"{i}. {stock_icon} *{result.drug_name}*{rx_icon}"

        if result.price_bs is not None:
            line += f"\n   Bs. {result.price_bs:,.2f}"

        if result.stores_in_stock > 0:
            line += f" | {result.stores_in_stock} tiendas con stock"
        elif not result.available:
            line += "\n   Sin stock disponible"

        lines.append(line)

    if response.total > 5:
        lines.append(f"\n... y {response.total - 5} resultados mas.")

    lines.append("\nEnvia otro nombre de medicamento para buscar.")
    return "\n".join(lines)
