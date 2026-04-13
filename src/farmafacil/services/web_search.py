"""Web search via Brave Search API — admin-only tool.

Free tier: 2000 queries/month. Used by the admin chat AI for
real-time information, fact-checking, and research.
"""

import logging

import httpx

from farmafacil.config import BRAVE_SEARCH_API_KEY

logger = logging.getLogger(__name__)

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_RESULTS = 5
TIMEOUT = 15


async def web_search(query: str) -> str:
    """Search the web via Brave Search API.

    Args:
        query: Search query string.

    Returns:
        Formatted text with search results, or an error message.
    """
    if not BRAVE_SEARCH_API_KEY:
        return "Web search no disponible — falta BRAVE_SEARCH_API_KEY en la configuración."

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }
    params = {
        "q": query,
        "count": MAX_RESULTS,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                BRAVE_API_URL, headers=headers, params=params,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            return "Rate limit alcanzado en Brave Search. Intenta en 10-15 minutos."
        if code in (401, 403):
            return "API key de Brave Search inválida o expirada."
        return f"Error de Brave Search: HTTP {code}"
    except httpx.RequestError as exc:
        return f"Error de conexión con Brave Search: {exc}"

    results = data.get("web", {}).get("results", [])
    if not results:
        return f"No se encontraron resultados para: {query}"

    lines = [f"**Resultados de búsqueda para:** {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Sin título")
        url = r.get("url", "")
        desc = r.get("description", "Sin descripción")
        lines.append(f"{i}. **{title}**\n   {url}\n   {desc}\n")

    return "\n".join(lines)
