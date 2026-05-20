"""Admin chat tools: users and user memory."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete as sql_delete, select, update

from farmafacil.db.session import async_session
from farmafacil.models.database import User, UserMemory
from farmafacil.services.user_memory import get_memory, update_memory

from ._helpers import _fmt_bool, _resolve_user_ref

logger = logging.getLogger(__name__)

# ── Whitelisted user setting fields ────────────────────────────────────

# ``set_user_setting`` only accepts these field names. Intentionally excludes:
#   - chat_admin  (UI-only by design; see Item 35 security note)
#   - id / phone_number / created_at  (immutable identity)
#   - token counters  (only touched by increment_token_usage)
_USER_SETTABLE_FIELDS: frozenset[str] = frozenset({
    "name",
    "display_preference",     # "grid" or "detail"
    "response_mode_override", # "text", "ai_only", "hybrid", or None
    "chat_debug",             # bool
    "onboarding_step",        # reset onboarding from chat
    "admin_mode_active",      # for emergency kill of a stuck admin mode
})


async def _tool_list_users(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    phone_like = args.get("phone_like")
    async with async_session() as session:
        from sqlalchemy import desc
        stmt = select(User).order_by(desc(User.id)).limit(limit)
        if phone_like:
            # Escape SQL LIKE wildcards so an LLM-supplied ``%`` or ``_`` in
            # the search term can't silently turn into a full-table scan or
            # overly-broad match on the high-cardinality users table.
            escaped = (
                str(phone_like).replace("\\", "\\\\")
                .replace("%", "\\%").replace("_", "\\_")
            )
            stmt = stmt.where(
                User.phone_number.like(f"%{escaped}%", escape="\\")
            )
        result = await session.execute(stmt)
        users = result.scalars().all()
    if not users:
        return "Sin usuarios."
    lines = [f"Usuarios ({len(users)}):"]
    for u in users:
        admin_flag = " [ADMIN]" if u.chat_admin else ""
        lines.append(
            f"#{u.id} {u.phone_number} {u.name or '(sin nombre)'} "
            f"zona={u.zone_name or '-'}{admin_flag}"
        )
    return "\n".join(lines)


async def _tool_get_user(args: dict[str, Any]) -> str:
    user = await _resolve_user_ref(args.get("user_ref") or args.get("id") or args.get("phone"))
    if not user:
        return "Usuario no existe."
    return (
        f"Usuario #{user.id}\n"
        f"Phone: {user.phone_number}\n"
        f"Name: {user.name or '(sin nombre)'}\n"
        f"Zona: {user.zone_name or '-'} ({user.city_code or '-'})\n"
        f"Preferencia: {user.display_preference}\n"
        f"Onboarding: {user.onboarding_step or 'completo'}\n"
        f"Chat admin: {_fmt_bool(user.chat_admin)}\n"
        f"Admin activo: {_fmt_bool(user.admin_mode_active)}\n"
        f"Chat debug: {_fmt_bool(user.chat_debug)}\n"
        f"Tokens: {user.total_tokens_in} in / {user.total_tokens_out} out\n"
        f"Llamadas: haiku={user.calls_haiku} sonnet={user.calls_sonnet} "
        f"admin={user.calls_admin}"
    )


async def _tool_get_user_memory(args: dict[str, Any]) -> str:
    user = await _resolve_user_ref(args.get("user_ref") or args.get("id") or args.get("phone"))
    if not user:
        return "Usuario no existe."
    memory = await get_memory(user.id)
    if not memory:
        return f"Usuario #{user.id} sin memoria."
    return f"Memoria #{user.id}:\n{memory}"


async def _tool_set_user_memory(args: dict[str, Any]) -> str:
    user = await _resolve_user_ref(args.get("user_ref") or args.get("id") or args.get("phone"))
    if not user:
        return "Usuario no existe."
    text = str(args.get("text", "")).strip()
    if not text:
        return "Falta text."
    await update_memory(user.id, text, updated_by="admin")
    logger.info("admin_chat.set_user_memory user_id=%d len=%d", user.id, len(text))
    return f"Memoria de #{user.id} actualizada."


async def _tool_clear_user_memory(args: dict[str, Any]) -> str:
    user = await _resolve_user_ref(args.get("user_ref") or args.get("id") or args.get("phone"))
    if not user:
        return "Usuario no existe."
    async with async_session() as session:
        await session.execute(
            sql_delete(UserMemory).where(UserMemory.user_id == user.id)
        )
        await session.commit()
    logger.info("admin_chat.clear_user_memory user_id=%d", user.id)
    return f"Memoria de #{user.id} eliminada."


async def _tool_set_user_setting(args: dict[str, Any]) -> str:
    user = await _resolve_user_ref(args.get("user_ref") or args.get("id") or args.get("phone"))
    if not user:
        return "Usuario no existe."
    field = str(args.get("field", "")).strip()
    if field not in _USER_SETTABLE_FIELDS:
        allowed = ", ".join(sorted(_USER_SETTABLE_FIELDS))
        return f"Campo no permitido. Permitidos: {allowed}"
    value = args.get("value")
    # Coerce value based on field type
    if field in ("chat_debug", "admin_mode_active"):
        value = bool(value) if not isinstance(value, str) else value.lower() in ("true", "1", "yes", "si")
    elif field == "onboarding_step" and value in ("", None, "null"):
        value = None
    else:
        value = str(value) if value is not None else None
    async with async_session() as session:
        await session.execute(
            update(User).where(User.id == user.id).values(**{field: value})
        )
        await session.commit()
    logger.info(
        "admin_chat.set_user_setting user_id=%d field=%s value=%r",
        user.id, field, value,
    )
    return f"#{user.id}.{field} = {value!r}"
