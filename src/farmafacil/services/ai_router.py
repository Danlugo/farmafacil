"""AI Role Router — selects the appropriate AI role for a message.

Uses a lightweight LLM call with the list of available roles and their
descriptions to determine which role should handle a complex message.
Falls back to 'pharmacy_advisor' if routing fails.
"""

import logging

import anthropic

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL
from farmafacil.services.ai_roles import RoleConfig, list_active_roles

logger = logging.getLogger(__name__)

DEFAULT_ROLE = "pharmacy_advisor"


async def route_to_role(message: str) -> str:
    """Select the best AI role to handle a user message.

    Makes a lightweight LLM call with available role descriptions to pick
    the right persona. Returns the role slug name.

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

    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY — defaulting to %s", DEFAULT_ROLE)
        return DEFAULT_ROLE

    roles_description = _build_roles_list(roles)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=50,
            system=(
                "You are a message router. Given a user message and a list of "
                "available AI roles, return ONLY the role name (slug) that best "
                "handles this message. Return just the slug, nothing else."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Available roles:\n{roles_description}\n\n"
                        f"User message: {message}\n\n"
                        "Which role should handle this? Return only the slug."
                    ),
                }
            ],
        )
        selected = response.content[0].text.strip().lower()

        # Validate the selection
        valid_names = {r.name for r in roles}
        if selected in valid_names:
            logger.info("Router selected role '%s' for: %s", selected, message[:80])
            return selected

        logger.warning(
            "Router returned invalid role '%s', defaulting to %s",
            selected, DEFAULT_ROLE,
        )
        return DEFAULT_ROLE

    except Exception:
        logger.error("AI router failed, defaulting to %s", DEFAULT_ROLE, exc_info=True)
        return DEFAULT_ROLE


def _build_roles_list(roles: list[RoleConfig]) -> str:
    """Build a concise roles list for the router prompt.

    Args:
        roles: List of active role configs.

    Returns:
        Formatted string listing each role's name and description.
    """
    lines = []
    for role in roles:
        desc = role.description or role.display_name
        lines.append(f"- {role.name}: {desc}")
    return "\n".join(lines)
