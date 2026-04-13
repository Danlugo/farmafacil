"""App settings service — admin-editable config stored in DB."""

import logging

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import AppSetting

logger = logging.getLogger(__name__)

# Default settings with descriptions
DEFAULTS: dict[str, tuple[str, str]] = {
    "cache_ttl_minutes": ("10080", "How long to cache drug search results (minutes). Default: 1 week"),
    "max_search_results": ("10", "Maximum results per drug search"),
    "max_grid_products": ("6", "Maximum products in grid image"),
    "max_detail_products": ("3", "Maximum products in detail image mode"),
    "store_backfill_interval_hours": ("24", "How often to refresh store locations (hours)"),
    "response_mode": ("hybrid", "Bot response mode: hybrid (keywords+AI) or ai_only (all AI)"),
    "chat_debug": ("disabled", "Chat debug mode: enabled (show debug footer) or disabled"),
    "category_menu_enabled": (
        "true",
        "Show category quick-reply menu on bare greetings for onboarded users "
        "(Item 29, v0.13.2). Values: 'true' or 'false'.",
    ),
    "relevance_threshold": (
        "0.3",
        "Minimum relevance score (0.0-1.0) for a product to be included in "
        "search results. Lower = more permissive, higher = stricter. "
        "(Item 38, v0.15.0)",
    ),
    "default_model": (
        "haiku",
        "Default Claude model alias for USER-FACING AI calls (intent "
        "classification + generate_response). Valid: 'haiku', 'sonnet', "
        "'opus'. Editable via /model <alias> in admin chat. The admin AI "
        "itself always uses Opus regardless of this setting. (Item 35, v0.14.0)",
    ),
}

# Model alias set — must match config.MODEL_ALIASES
VALID_MODEL_ALIASES: set[str] = {"haiku", "sonnet", "opus"}


async def seed_settings() -> None:
    """Seed default settings if they don't exist."""
    async with async_session() as session:
        for key, (value, desc) in DEFAULTS.items():
            result = await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            if result.scalar_one_or_none() is None:
                session.add(AppSetting(key=key, value=value, description=desc))
        await session.commit()


async def get_setting(key: str) -> str:
    """Get a setting value by key.

    Args:
        key: Setting key.

    Returns:
        Setting value as string. Falls back to default if not in DB.
    """
    async with async_session() as session:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            return setting.value

    # Fallback to defaults
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    return ""


async def get_setting_int(key: str) -> int:
    """Get a setting as integer."""
    return int(await get_setting(key))


async def get_setting_float(key: str, fallback: float = 0.0) -> float:
    """Get a setting as float with a safe fallback.

    Args:
        key: Setting key.
        fallback: Value to return if the setting cannot be parsed as float.

    Returns:
        Setting value as float.
    """
    raw = await get_setting(key)
    try:
        return float(raw)
    except (ValueError, TypeError):
        return fallback


async def set_setting(key: str, value: str) -> None:
    """Upsert an AppSetting row by key.

    Used by the admin chat ``set_app_setting`` tool so the App Admin AI can
    flip feature flags (e.g. ``category_menu_enabled``, ``chat_debug``)
    without leaving WhatsApp. Mirrors the ``set_default_model`` upsert
    pattern — creates a new row with the DEFAULTS description if one exists,
    otherwise inserts without a description.
    """
    async with async_session() as session:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        setting = result.scalar_one_or_none()
        if setting is None:
            description = DEFAULTS[key][1] if key in DEFAULTS else None
            session.add(
                AppSetting(key=key, value=value, description=description)
            )
        else:
            setting.value = value
        await session.commit()


_VALID_MODES = {"hybrid", "ai_only"}
_VALID_DEBUG = {"enabled", "disabled"}


def resolve_response_mode(user_mode: str | None, global_mode: str) -> str:
    """Resolve the effective response mode for a user.

    User override takes priority over the global setting.
    Invalid values fall back to 'hybrid' with a warning.

    Args:
        user_mode: The user's response_mode (None = use global).
        global_mode: The global app setting.

    Returns:
        'hybrid' or 'ai_only'.
    """
    if user_mode in _VALID_MODES:
        return user_mode
    if global_mode not in _VALID_MODES:
        logger.warning("Invalid global response_mode '%s' — defaulting to hybrid", global_mode)
        return "hybrid"
    return global_mode


async def get_default_model() -> str:
    """Get the current default model alias for user-facing AI calls.

    Falls back to 'haiku' if the stored value is missing or invalid.

    Returns:
        One of 'haiku', 'sonnet', 'opus'.
    """
    raw = (await get_setting("default_model") or "").strip().lower()
    if raw in VALID_MODEL_ALIASES:
        return raw
    logger.warning(
        "Invalid default_model setting %r — falling back to 'haiku'", raw
    )
    return "haiku"


async def set_default_model(alias: str) -> str:
    """Persist a new default model alias in app_settings.

    Args:
        alias: One of 'haiku', 'sonnet', 'opus' (case-insensitive).

    Returns:
        The normalized alias that was stored.

    Raises:
        ValueError: If the alias is not in VALID_MODEL_ALIASES.
    """
    normalized = (alias or "").strip().lower()
    if normalized not in VALID_MODEL_ALIASES:
        raise ValueError(
            f"Invalid model alias {alias!r}. Valid: "
            f"{sorted(VALID_MODEL_ALIASES)}"
        )

    async with async_session() as session:
        result = await session.execute(
            select(AppSetting).where(AppSetting.key == "default_model")
        )
        setting = result.scalar_one_or_none()
        if setting is None:
            session.add(
                AppSetting(
                    key="default_model",
                    value=normalized,
                    description=DEFAULTS["default_model"][1],
                )
            )
        else:
            setting.value = normalized
        await session.commit()

    return normalized


def resolve_chat_debug(user_debug: str | None, global_debug: str) -> bool:
    """Resolve whether chat debug is enabled for a user.

    User override takes priority over the global setting.

    Args:
        user_debug: The user's chat_debug (None = use global).
        global_debug: The global app setting.

    Returns:
        True if debug is enabled, False otherwise.
    """
    if user_debug in _VALID_DEBUG:
        return user_debug == "enabled"
    if global_debug not in _VALID_DEBUG:
        logger.warning("Invalid global chat_debug '%s' — defaulting to disabled", global_debug)
        return False
    return global_debug == "enabled"
