"""App settings service — admin-editable config stored in DB.

Includes a lightweight in-memory cache (Item 57, v0.24.0) so
``get_setting()`` doesn't hit the database on every call.  The cache
uses a per-key TTL of 60 seconds and is invalidated on writes
(``set_setting``, ``set_default_model``).
"""

import asyncio
import logging
import time

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import AppSetting

logger = logging.getLogger(__name__)

# ── In-memory settings cache ─────────────────────────────────────────────
# Maps key → (value, expire_timestamp). Entries older than _CACHE_TTL
# seconds are treated as stale and refreshed from the DB on next access.
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL: float = 60.0  # seconds

# Lock prevents thundering-herd on cache miss: when many concurrent
# requests expire the same key simultaneously, only one hits the DB.
# (v0.24.0 code-review fix)
_cache_lock = asyncio.Lock()


def clear_settings_cache() -> None:
    """Flush the entire settings cache.

    Primarily for tests that need deterministic DB reads.
    """
    _cache.clear()

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
    "post_feedback_suggestion": (
        "false",
        "After YES feedback on drug search, ask user if they want to leave "
        "a suggestion (text or voice). Values: 'true' or 'false'. Per-user "
        "override via users.post_feedback_suggestion column. "
        "(v0.22.2; default changed to 'false' in v0.22.5)",
    ),
    "post_feedback_bug_report": (
        "false",
        "After NO feedback on drug search, ask user if they want to leave "
        "a bug report (text or voice). Values: 'true' or 'false'. Per-user "
        "override via users.post_feedback_bug_report column. "
        "(v0.22.2; default changed to 'false' in v0.22.5)",
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

    Uses a 60-second in-memory cache to avoid hitting the DB on every call.
    Cache is invalidated on writes (``set_setting``, ``set_default_model``).

    Args:
        key: Setting key.

    Returns:
        Setting value as string. Falls back to default if not in DB.
    """
    # Fast path: check cache without lock
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None:
        value, expires = cached
        if now < expires:
            return value

    # Cache miss or expired — acquire lock so only one coroutine hits DB
    # per key (prevents thundering-herd on concurrent requests).
    async with _cache_lock:
        # Double-check: another coroutine may have filled the cache
        cached = _cache.get(key)
        if cached is not None:
            value, expires = cached
            if time.monotonic() < expires:
                return value

        async with async_session() as session:
            result = await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            if setting:
                _cache[key] = (setting.value, time.monotonic() + _CACHE_TTL)
                return setting.value

    # Fallback to defaults (also cache so repeated misses don't hit DB)
    if key in DEFAULTS:
        default_val = DEFAULTS[key][0]
        _cache[key] = (default_val, time.monotonic() + _CACHE_TTL)
        return default_val
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

    Invalidates the in-memory cache for this key on success.
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

    # Invalidate cache after successful write
    _cache.pop(key, None)


_VALID_MODES = {"hybrid", "ai_only"}
_VALID_DEBUG = {"enabled", "disabled"}
_VALID_TOGGLE = {"true", "false"}


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

    # Invalidate cache after successful write
    _cache.pop("default_model", None)

    return normalized


async def resolve_user_model() -> str:
    """Return the full Anthropic model name for user-facing AI calls.

    Reads the ``default_model`` alias from ``app_settings`` and maps it to
    the concrete model id via ``config.MODEL_ALIASES``. This is the SINGLE
    source of truth for which model the bot uses for intent classification
    and pharmacy-advisor responses.

    Used by ``ai_responder.classify_with_ai``, ``ai_responder._call_llm``,
    ``ai_responder.refine_clarified_query``, ``user_memory.auto_update_memory``,
    and the vision/document drug-extraction helpers in ``bot.handler``.

    The admin AI (``run_admin_turn``) does NOT use this resolver — it is
    hardcoded to Opus by design (admin reasoning benefits from Opus, admin
    cost is tracked in its own bucket, and admin work must not be affected
    by user-facing model changes).

    Returns:
        Full model id, e.g. ``"claude-haiku-4-5-20251001"`` or
        ``"claude-sonnet-4-20250514"``. Falls back to the haiku id if the
        stored alias is missing or somehow not in MODEL_ALIASES.
    """
    # Local import to avoid a cycle: config -> services.settings is fine,
    # but importing config at module top would couple settings imports to
    # env-var loading order in tests.
    from farmafacil.config import LLM_MODEL, MODEL_ALIASES

    alias = await get_default_model()
    return MODEL_ALIASES.get(alias, LLM_MODEL)


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


def resolve_post_feedback(user_override: str | None, global_value: str) -> bool:
    """Resolve whether a post-feedback feature is enabled for a user.

    Used for both ``post_feedback_suggestion`` and
    ``post_feedback_bug_report``.  The user column (nullable) takes
    priority; NULL falls through to the global ``app_settings`` value.

    Args:
        user_override: The user's per-user column value (None = use global).
        global_value: The global app setting (``"true"`` or ``"false"``).

    Returns:
        True if the feature is enabled, False otherwise.
    """
    normalized = (user_override or "").strip().lower()
    if normalized in _VALID_TOGGLE:
        return normalized == "true"
    global_norm = (global_value or "").strip().lower()
    if global_norm not in _VALID_TOGGLE:
        logger.warning(
            "Invalid global post_feedback setting '%s' — defaulting to false",
            global_value,
        )
        return False
    return global_norm == "true"
