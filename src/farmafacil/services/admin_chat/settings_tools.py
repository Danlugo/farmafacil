"""Admin chat tools: app settings and default model."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from farmafacil.config import MODEL_ALIASES
from farmafacil.db.session import async_session
from farmafacil.models.database import AppSetting
from farmafacil.services import settings as settings_svc

from ._helpers import _truncate

logger = logging.getLogger(__name__)


async def _tool_list_app_settings(_: dict[str, Any]) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(AppSetting).order_by(AppSetting.key)
        )
        rows = result.scalars().all()
    if not rows:
        return "Sin settings."
    lines = ["App settings:"]
    for r in rows:
        lines.append(f"  {r.key} = {_truncate(r.value, 60)}")
    return "\n".join(lines)


async def _tool_get_app_setting(args: dict[str, Any]) -> str:
    key = str(args.get("key", "")).strip()
    if not key:
        return "Falta key."
    value = await settings_svc.get_setting(key)
    if value == "":
        return f"Setting {key} no existe."
    return f"{key} = {value}"


async def _tool_set_app_setting(args: dict[str, Any]) -> str:
    key = str(args.get("key", "")).strip()
    value = args.get("value")
    if not key:
        return "Falta key."
    if value is None:
        return "Falta value."
    # Warn (but don't block) if this key does not already exist — protects
    # against typos that would permanently corrupt a critical setting
    # (e.g. ``cache_ttl_mniutes`` instead of ``cache_ttl_minutes``).
    existing = await settings_svc.get_setting(key)
    is_new = existing == ""
    await settings_svc.set_setting(key, str(value))
    if is_new:
        logger.warning(
            "admin_chat.set_app_setting CREATED new key=%s value=%r", key, value,
        )
        suffix = "  (NUEVA key — verifica que no sea un typo)"
    else:
        logger.info("admin_chat.set_app_setting key=%s value=%r", key, value)
        suffix = ""
    return f"{key} = {value}{suffix}"


async def _tool_get_default_model(_: dict[str, Any]) -> str:
    alias = await settings_svc.get_default_model()
    full = MODEL_ALIASES.get(alias, "?")
    lines = [f"Modelo actual: {alias} ({full})", "Disponibles:"]
    for k, v in MODEL_ALIASES.items():
        marker = "→ " if k == alias else "  "
        lines.append(f"{marker}{k} = {v}")
    return "\n".join(lines)


async def _tool_set_default_model(args: dict[str, Any]) -> str:
    alias = str(args.get("alias", "")).strip().lower()
    if not alias:
        return "Falta alias (haiku / sonnet / opus)."
    try:
        new_alias = await settings_svc.set_default_model(alias)
    except ValueError as exc:
        return f"Error: {exc}"
    logger.info("admin_chat.set_default_model alias=%s", new_alias)
    return f"Modelo default = {new_alias} ({MODEL_ALIASES[new_alias]})"
