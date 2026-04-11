"""AI Roles service — cached loading of roles, rules, and skills.

Provides functions to load AI role configurations from the database with
in-memory caching (5-minute TTL), matching the pattern used by intent keywords.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from farmafacil.db.session import async_session
from farmafacil.models.database import AiRole, AiRoleRule, AiRoleSkill

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minutes


@dataclass
class RoleConfig:
    """Assembled AI role configuration ready for prompt building."""

    name: str
    display_name: str
    description: str
    system_prompt: str
    rules: list[str]
    skills: list[str]


# ── In-memory cache ────────────────────────────────────────────────────

_roles_cache: dict[str, RoleConfig] = {}
_cache_loaded_at: float = 0


async def _load_roles_cache() -> None:
    """Load all active roles with their rules and skills into cache."""
    global _roles_cache, _cache_loaded_at
    async with async_session() as session:
        result = await session.execute(
            select(AiRole)
            .where(AiRole.is_active.is_(True))
            .options(
                selectinload(AiRole.rules),
                selectinload(AiRole.skills),
            )
        )
        roles = result.scalars().all()

        new_cache: dict[str, RoleConfig] = {}
        for role in roles:
            active_rules = [
                r.content
                for r in sorted(role.rules, key=lambda r: r.sort_order)
                if r.is_active
            ]
            active_skills = [
                s.content for s in role.skills
                if s.is_active
            ]
            new_cache[role.name] = RoleConfig(
                name=role.name,
                display_name=role.display_name,
                description=role.description or "",
                system_prompt=role.system_prompt,
                rules=active_rules,
                skills=active_skills,
            )

        _roles_cache = new_cache
        _cache_loaded_at = time.time()
        logger.debug("Loaded %d AI roles into cache", len(_roles_cache))


async def _ensure_cache() -> None:
    """Refresh the cache if stale."""
    if time.time() - _cache_loaded_at > CACHE_TTL_SECONDS:
        await _load_roles_cache()


async def get_role(name: str) -> RoleConfig | None:
    """Get an AI role by slug name.

    Args:
        name: Role slug (e.g., 'pharmacy_advisor').

    Returns:
        RoleConfig if found and active, None otherwise.
    """
    await _ensure_cache()
    return _roles_cache.get(name)


async def list_active_roles() -> list[RoleConfig]:
    """List all active AI roles.

    Returns:
        List of active RoleConfig objects.
    """
    await _ensure_cache()
    return list(_roles_cache.values())


def assemble_prompt(
    role: RoleConfig,
    client_memory: str | None = None,
    user_profile: dict | None = None,
) -> str:
    """Assemble the full system prompt for an AI role.

    Combines the role's base system prompt with its rules, skills,
    live user profile data, and the client's memory into a single
    system prompt string.

    The user profile section is AUTHORITATIVE (live from DB) and takes
    precedence over anything in client memory. Memory is supplementary
    context that captures patterns, preferences, and history.

    Args:
        role: The AI role configuration.
        client_memory: Optional per-user memory text.
        user_profile: Optional dict with live profile data (name, zone, etc.)

    Returns:
        Complete system prompt string.
    """
    parts = [role.system_prompt]

    if role.rules:
        rules_text = "\n\n".join(role.rules)
        parts.append(f"\n\n## Rules\n\n{rules_text}")

    if role.skills:
        skills_text = "\n\n".join(role.skills)
        parts.append(f"\n\n## Skills\n\n{skills_text}")

    # Profile is authoritative — always inject before memory
    if user_profile:
        profile_lines = []
        if user_profile.get("name"):
            profile_lines.append(f"- Nombre: {user_profile['name']}")
        if user_profile.get("zone"):
            profile_lines.append(f"- Ubicación: {user_profile['zone']}")
        if user_profile.get("city_code"):
            profile_lines.append(f"- Ciudad: {user_profile['city_code']}")
        if user_profile.get("preference"):
            pref_label = "galería" if user_profile["preference"] == "grid" else "imagen grande"
            profile_lines.append(f"- Visualización: {pref_label}")
        if profile_lines:
            parts.append(
                "\n\n## User Profile (authoritative — always current)\n\n"
                + "\n".join(profile_lines)
            )

    # Memory is supplementary — patterns, preferences, history
    if client_memory and client_memory.strip():
        parts.append(
            "\n\n## Client Memory (supplementary — may contain outdated info, "
            "profile section above takes precedence)\n\n" + client_memory
        )

    return "".join(parts)
