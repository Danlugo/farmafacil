"""Admin chat tools: web search."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _tool_web_search(args: dict[str, Any]) -> str:
    """Search the web via Brave Search API."""
    from farmafacil.services.web_search import web_search

    query = args.get("query", "").strip()
    if not query:
        return "Error: query es requerido."
    return await web_search(query)
