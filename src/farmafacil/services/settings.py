"""App settings service — admin-editable config stored in DB."""

import logging

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import AppSetting

logger = logging.getLogger(__name__)

# Default settings with descriptions
DEFAULTS: dict[str, tuple[str, str]] = {
    "cache_ttl_minutes": ("30", "How long to cache drug search results (minutes)"),
    "max_search_results": ("10", "Maximum results per drug search"),
    "max_grid_products": ("6", "Maximum products in grid image"),
    "max_detail_products": ("3", "Maximum products in detail image mode"),
    "store_backfill_interval_hours": ("24", "How often to refresh store locations (hours)"),
}


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
