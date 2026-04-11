"""AI Role Router — selects the appropriate AI role for a message.

Uses keyword heuristics to route messages to the correct AI role.
Falls back to 'pharmacy_advisor' (the primary role) for all messages
that don't match app_support patterns.
"""

import logging

from farmafacil.services.ai_roles import list_active_roles

logger = logging.getLogger(__name__)

DEFAULT_ROLE = "pharmacy_advisor"

# Keywords that indicate the user needs app/technical support
_APP_SUPPORT_KEYWORDS = {
    "no funciona", "no me funciona", "error", "bug", "problema tecnico",
    "problema técnico", "no carga", "no abre", "se tranca", "se congela",
    "no responde", "esta dañado", "está dañado", "como se usa",
    "como funciona la app", "no entiendo como", "no puedo usar",
    "se queda cargando", "no me deja", "falla", "tiene un error",
}


async def route_to_role(message: str) -> str:
    """Select the best AI role to handle a user message.

    Uses keyword matching to detect app_support messages. Everything else
    goes to pharmacy_advisor. Zero LLM calls — instant and free.

    Args:
        message: The user's message text.

    Returns:
        Role name slug (e.g., 'pharmacy_advisor', 'app_support').
    """
    roles = await list_active_roles()
    if not roles:
        logger.warning("No active AI roles found, using default: %s", DEFAULT_ROLE)
        return DEFAULT_ROLE

    # Single role? No routing needed
    if len(roles) == 1:
        return roles[0].name

    # Check valid role names from DB
    valid_names = {r.name for r in roles}

    # Keyword heuristic — check for app_support patterns
    text_lower = message.lower().strip()
    if "app_support" in valid_names:
        for keyword in _APP_SUPPORT_KEYWORDS:
            if keyword in text_lower:
                logger.info("Router matched app_support keyword '%s' for: %s", keyword, message[:80])
                return "app_support"

    return DEFAULT_ROLE
