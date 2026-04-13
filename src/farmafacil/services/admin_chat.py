"""Admin Chat tools — the registry of actions the App Admin AI can call.

The admin AI chat (v0.14.0, Item 35) is gated by ``users.chat_admin``
(UI-editable only) and activated per-session via ``/admin``. When active, the
admin LLM loop emits ``TOOL_CALL`` instructions that are resolved here against
a whitelisted registry. Every tool returns a short text string that can be
either sent back to the LLM for the next reasoning step OR surfaced directly
to WhatsApp.

Design principles:
- Every tool is async, returns ``str``, and never raises for expected-not-found
  conditions — those are reported as "No existe" strings so the LLM can react.
- Tool arguments are JSON-decoded from whatever the LLM emits; we do defensive
  coercion (int / bool / str) inside each tool, never trusting the LLM.
- Code-introspection tools (``read_code``, ``list_code``) enforce a strict
  allowlist rooted at the project root — never any of ``.env*``, ``*.db``,
  hidden files, or paths outside the allowed prefixes.
- "Mutation" tools (update/delete/set) all log an INFO line so every admin
  action is audit-traceable via the docker logs.
- ``report_issue`` writes to ``user_feedback`` with an ``admin_`` prefix so
  ``/farmafacil-review`` and related dev-side skills can pick up admin-flagged
  bugs and improvement ideas from the same backlog as user submissions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete as sql_delete, desc, func, or_, select, update

from farmafacil.config import MODEL_ALIASES
from farmafacil.db.session import async_session
from farmafacil.models.database import (
    AiRole,
    AiRoleRule,
    AiRoleSkill,
    AppSetting,
    ConversationLog,
    PharmacyLocation,
    Product,
    SearchLog,
    User,
    UserFeedback,
    UserMemory,
)
from farmafacil.services import settings as settings_svc
from farmafacil.services.user_memory import get_memory, update_memory

logger = logging.getLogger(__name__)


# ── Project root + allowed paths for code introspection ────────────────

# /src/farmafacil/services/admin_chat.py -> project root is parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Directory prefixes relative to PROJECT_ROOT where read/list are allowed.
_ALLOWED_DIR_PREFIXES: tuple[str, ...] = (
    "src/farmafacil",
    "tests",
    "docs",
)

# Individual files at the project root that can always be read.
_ALLOWED_ROOT_FILES: frozenset[str] = frozenset({
    "CLAUDE.md",
    "IMPROVEMENT-PLAN.md",
    "README.md",
    "pyproject.toml",
    "MEMORY.md",
})

# Files we never read even if inside an allowed dir.
_FORBIDDEN_SUFFIXES: tuple[str, ...] = (
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".so",
)
_FORBIDDEN_NAMES: frozenset[str] = frozenset({
    ".env", ".env.local", ".env.prod", ".env.dev", "credentials.json",
    "farmafacil.db",
})

MAX_READ_BYTES = 64 * 1024  # 64 KiB hard cap on file reads


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


# ── Helpers ─────────────────────────────────────────────────────────────

async def _resolve_user_ref(user_ref: Any) -> User | None:
    """Look up a User by either numeric id or phone number string.

    Used by tools that accept ``user_ref`` as a polymorphic argument. ``int``
    or a digit-string that looks like a row id is treated as the primary key;
    anything else is treated as a phone number.
    """
    if user_ref is None:
        return None
    async with async_session() as session:
        if isinstance(user_ref, int):
            result = await session.execute(select(User).where(User.id == user_ref))
            return result.scalar_one_or_none()
        ref = str(user_ref).strip()
        if not ref:
            return None
        # Try id first if it's a pure number
        if ref.isdigit() and len(ref) <= 6:
            result = await session.execute(
                select(User).where(User.id == int(ref))
            )
            user = result.scalar_one_or_none()
            if user:
                return user
        # Fall back to phone match
        result = await session.execute(
            select(User).where(User.phone_number == ref)
        )
        return result.scalar_one_or_none()


def _fmt_bool(value: Any) -> str:
    return "si" if bool(value) else "no"


def _truncate(text: str | None, n: int = 80) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ── Feedback tools ──────────────────────────────────────────────────────

async def _tool_list_feedback(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    feedback_type = args.get("type")
    reviewed = args.get("reviewed")

    async with async_session() as session:
        stmt = select(UserFeedback).order_by(desc(UserFeedback.created_at)).limit(limit)
        conds = []
        if feedback_type:
            conds.append(UserFeedback.feedback_type == str(feedback_type))
        if reviewed is not None:
            conds.append(UserFeedback.reviewed == bool(reviewed))
        if conds:
            stmt = stmt.where(and_(*conds))
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return "Sin casos."
    lines = [f"Casos ({len(rows)}):"]
    for r in rows:
        rev = "✓" if r.reviewed else "•"
        lines.append(f"{rev} #{r.id} [{r.feedback_type}] {_truncate(r.message, 60)}")
    return "\n".join(lines)


async def _tool_get_feedback(args: dict[str, Any]) -> str:
    fid = int(args.get("id") or 0)
    if not fid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(UserFeedback).where(UserFeedback.id == fid)
        )
        fb = result.scalar_one_or_none()
    if not fb:
        return f"Caso #{fid} no existe."
    return (
        f"Caso #{fb.id}\n"
        f"Tipo: {fb.feedback_type}\n"
        f"Usuario_id: {fb.user_id}\n"
        f"Revisado: {_fmt_bool(fb.reviewed)}\n"
        f"Log_conv: {fb.conversation_log_id}\n"
        f"Fecha: {fb.created_at:%Y-%m-%d %H:%M}\n"
        f"Mensaje: {fb.message}\n"
        f"Notas: {fb.reviewer_notes or '(ninguna)'}"
    )


async def _tool_update_feedback(args: dict[str, Any]) -> str:
    fid = int(args.get("id") or 0)
    if not fid:
        return "Falta id."
    values: dict[str, Any] = {}
    if "reviewed" in args:
        values["reviewed"] = bool(args["reviewed"])
        if values["reviewed"]:
            values["reviewed_at"] = func.now()
    if "reviewer_notes" in args:
        values["reviewer_notes"] = str(args["reviewer_notes"])[:2000]
    if not values:
        return "Nada que actualizar."
    async with async_session() as session:
        result = await session.execute(
            update(UserFeedback).where(UserFeedback.id == fid).values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Caso #{fid} no existe."
    logger.info("admin_chat.update_feedback id=%d values=%s", fid, list(values))
    return f"Caso #{fid} actualizado."


async def _tool_report_issue(args: dict[str, Any]) -> str:
    """Log an admin-flagged issue/idea into ``user_feedback``.

    The ``type`` argument is coerced to one of ``admin_bug``, ``admin_idea``,
    or ``admin_issue`` so downstream dev-side skills (``/farmafacil-review``)
    can pick up admin submissions via a ``feedback_type LIKE 'admin_%'``
    filter. The caller is the admin user — we store their user_id on the
    feedback row so the audit trail is preserved.
    """
    kind = str(args.get("type", "issue")).strip().lower()
    if kind not in {"bug", "idea", "issue"}:
        kind = "issue"
    feedback_type = f"admin_{kind}"
    message = str(args.get("message", "")).strip()
    if not message:
        return "Falta mensaje."
    if len(message) > 2000:
        message = message[:2000]
    admin_user_id = int(args.get("_admin_user_id") or 0)
    if not admin_user_id:
        return "Falta contexto de admin."
    async with async_session() as session:
        entry = UserFeedback(
            user_id=admin_user_id,
            feedback_type=feedback_type,
            message=message,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
    logger.info(
        "admin_chat.report_issue id=%d type=%s admin_user=%d",
        entry.id, feedback_type, admin_user_id,
    )
    return f"Registrado como {feedback_type} #{entry.id}."


# ── Conversation log tools ─────────────────────────────────────────────

async def _tool_list_conversation_logs(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    direction = args.get("direction")
    phone = args.get("phone")

    async with async_session() as session:
        stmt = select(ConversationLog).order_by(desc(ConversationLog.id)).limit(limit)
        conds = []
        if direction in ("inbound", "outbound"):
            conds.append(ConversationLog.direction == direction)
        if phone:
            conds.append(ConversationLog.phone_number == str(phone))
        if conds:
            stmt = stmt.where(and_(*conds))
        result = await session.execute(stmt)
        rows = result.scalars().all()
    if not rows:
        return "Sin logs."
    lines = [f"Logs ({len(rows)}):"]
    for r in rows:
        arrow = "→" if r.direction == "outbound" else "←"
        lines.append(
            f"#{r.id} {arrow} {r.phone_number} [{r.message_type}] "
            f"{_truncate(r.message_text, 60)}"
        )
    return "\n".join(lines)


async def _tool_get_conversation_log(args: dict[str, Any]) -> str:
    lid = int(args.get("id") or 0)
    if not lid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(ConversationLog).where(ConversationLog.id == lid)
        )
        log = result.scalar_one_or_none()
    if not log:
        return f"Log #{lid} no existe."
    return (
        f"Log #{log.id}\n"
        f"Phone: {log.phone_number}\n"
        f"Dir: {log.direction}\n"
        f"Tipo: {log.message_type}\n"
        f"Fecha: {log.created_at:%Y-%m-%d %H:%M}\n"
        f"Texto: {log.message_text}"
    )


# ── AI role / rule / skill tools ───────────────────────────────────────

async def _tool_list_ai_roles(_: dict[str, Any]) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(AiRole).order_by(AiRole.name)
        )
        roles = result.scalars().all()
    if not roles:
        return "Sin roles."
    lines = ["Roles:"]
    for r in roles:
        active = "✓" if r.is_active else "✗"
        lines.append(f"{active} {r.name} — {_truncate(r.description, 60)}")
    return "\n".join(lines)


async def _tool_get_ai_role(args: dict[str, Any]) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return "Falta name."
    async with async_session() as session:
        result = await session.execute(
            select(AiRole).where(AiRole.name == name)
        )
        role = result.scalar_one_or_none()
        if not role:
            return f"Rol {name} no existe."
        rules = list(role.rules)
        skills = list(role.skills)
    lines = [
        f"Rol: {role.name} ({role.display_name})",
        f"Activo: {_fmt_bool(role.is_active)}",
        f"Descripción: {role.description or '(ninguna)'}",
        f"Prompt: {_truncate(role.system_prompt, 200)}",
        f"Reglas ({len(rules)}):",
    ]
    for rule in rules:
        lines.append(
            f"  #{rule.id} {rule.name} [orden={rule.sort_order}] "
            f"{'✓' if rule.is_active else '✗'}"
        )
    lines.append(f"Skills ({len(skills)}):")
    for skill in skills:
        lines.append(
            f"  #{skill.id} {skill.name} {'✓' if skill.is_active else '✗'}"
        )
    return "\n".join(lines)


async def _tool_update_ai_role(args: dict[str, Any]) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return "Falta name."
    values: dict[str, Any] = {}
    for field in ("description", "system_prompt", "display_name"):
        if field in args:
            values[field] = str(args[field])
    if "is_active" in args:
        values["is_active"] = bool(args["is_active"])
    if not values:
        return "Nada que actualizar."
    async with async_session() as session:
        result = await session.execute(
            update(AiRole).where(AiRole.name == name).values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Rol {name} no existe."
    logger.info("admin_chat.update_ai_role name=%s values=%s", name, list(values))
    return f"Rol {name} actualizado."


async def _tool_add_ai_rule(args: dict[str, Any]) -> str:
    role_name = str(args.get("role_name", "")).strip()
    rule_name = str(args.get("name", "")).strip()
    content = str(args.get("content", "")).strip()
    if not (role_name and rule_name and content):
        return "Falta role_name / name / content."
    sort_order = int(args.get("sort_order", 0) or 0)
    async with async_session() as session:
        result = await session.execute(
            select(AiRole.id).where(AiRole.name == role_name)
        )
        role_id = result.scalar_one_or_none()
        if not role_id:
            return f"Rol {role_name} no existe."
        rule = AiRoleRule(
            role_id=role_id, name=rule_name, content=content,
            sort_order=sort_order, is_active=True,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
    logger.info(
        "admin_chat.add_ai_rule role=%s rule_id=%d name=%s",
        role_name, rule.id, rule_name,
    )
    return f"Regla #{rule.id} agregada a {role_name}."


async def _tool_update_ai_rule(args: dict[str, Any]) -> str:
    rid = int(args.get("id") or 0)
    if not rid:
        return "Falta id."
    values: dict[str, Any] = {}
    for field in ("name", "content", "description"):
        if field in args:
            values[field] = str(args[field])
    if "sort_order" in args:
        values["sort_order"] = int(args["sort_order"])
    if "is_active" in args:
        values["is_active"] = bool(args["is_active"])
    if not values:
        return "Nada que actualizar."
    async with async_session() as session:
        result = await session.execute(
            update(AiRoleRule).where(AiRoleRule.id == rid).values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Regla #{rid} no existe."
    logger.info("admin_chat.update_ai_rule id=%d values=%s", rid, list(values))
    return f"Regla #{rid} actualizada."


async def _tool_delete_ai_rule(args: dict[str, Any]) -> str:
    rid = int(args.get("id") or 0)
    if not rid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            sql_delete(AiRoleRule).where(AiRoleRule.id == rid)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Regla #{rid} no existe."
    logger.info("admin_chat.delete_ai_rule id=%d", rid)
    return f"Regla #{rid} eliminada."


async def _tool_add_ai_skill(args: dict[str, Any]) -> str:
    role_name = str(args.get("role_name", "")).strip()
    skill_name = str(args.get("name", "")).strip()
    content = str(args.get("content", "")).strip()
    if not (role_name and skill_name and content):
        return "Falta role_name / name / content."
    async with async_session() as session:
        result = await session.execute(
            select(AiRole.id).where(AiRole.name == role_name)
        )
        role_id = result.scalar_one_or_none()
        if not role_id:
            return f"Rol {role_name} no existe."
        skill = AiRoleSkill(
            role_id=role_id, name=skill_name, content=content, is_active=True,
        )
        session.add(skill)
        await session.commit()
        await session.refresh(skill)
    logger.info(
        "admin_chat.add_ai_skill role=%s skill_id=%d name=%s",
        role_name, skill.id, skill_name,
    )
    return f"Skill #{skill.id} agregada a {role_name}."


async def _tool_update_ai_skill(args: dict[str, Any]) -> str:
    sid = int(args.get("id") or 0)
    if not sid:
        return "Falta id."
    values: dict[str, Any] = {}
    for field in ("name", "content", "description"):
        if field in args:
            values[field] = str(args[field])
    if "is_active" in args:
        values["is_active"] = bool(args["is_active"])
    if not values:
        return "Nada que actualizar."
    async with async_session() as session:
        result = await session.execute(
            update(AiRoleSkill).where(AiRoleSkill.id == sid).values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Skill #{sid} no existe."
    logger.info("admin_chat.update_ai_skill id=%d values=%s", sid, list(values))
    return f"Skill #{sid} actualizada."


async def _tool_delete_ai_skill(args: dict[str, Any]) -> str:
    sid = int(args.get("id") or 0)
    if not sid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            sql_delete(AiRoleSkill).where(AiRoleSkill.id == sid)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Skill #{sid} no existe."
    logger.info("admin_chat.delete_ai_skill id=%d", sid)
    return f"Skill #{sid} eliminada."


# ── User tools ──────────────────────────────────────────────────────────

async def _tool_list_users(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    phone_like = args.get("phone_like")
    async with async_session() as session:
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


# ── Pharmacy & product tools ───────────────────────────────────────────

async def _tool_list_pharmacies(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 20) or 20)
    limit = max(1, min(limit, 100))
    chain = args.get("chain")
    city = args.get("city")
    is_active = args.get("is_active")
    async with async_session() as session:
        stmt = select(PharmacyLocation).order_by(PharmacyLocation.name).limit(limit)
        conds = []
        if chain:
            conds.append(PharmacyLocation.pharmacy_chain == str(chain))
        if city:
            conds.append(PharmacyLocation.city_code == str(city))
        if is_active is not None:
            conds.append(PharmacyLocation.is_active == bool(is_active))
        if conds:
            stmt = stmt.where(and_(*conds))
        result = await session.execute(stmt)
        rows = result.scalars().all()
    if not rows:
        return "Sin farmacias."
    lines = [f"Farmacias ({len(rows)}):"]
    for r in rows:
        status = "✓" if r.is_active else "✗"
        lines.append(
            f"{status} #{r.id} {r.pharmacy_chain} — {r.name} [{r.city_code}]"
        )
    return "\n".join(lines)


async def _tool_toggle_pharmacy(args: dict[str, Any]) -> str:
    pid = int(args.get("id") or 0)
    if not pid:
        return "Falta id."
    is_active = bool(args.get("is_active", False))
    async with async_session() as session:
        result = await session.execute(
            update(PharmacyLocation).where(PharmacyLocation.id == pid).values(
                is_active=is_active
            )
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Farmacia #{pid} no existe."
    logger.info("admin_chat.toggle_pharmacy id=%d is_active=%s", pid, is_active)
    return f"Farmacia #{pid} {'activada' if is_active else 'desactivada'}."


async def _tool_search_products(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Falta query."
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    async with async_session() as session:
        stmt = (
            select(Product)
            .where(or_(
                Product.drug_name.ilike(f"%{query}%"),
                Product.brand.ilike(f"%{query}%"),
            ))
            .order_by(Product.drug_name)
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    if not rows:
        return f"Sin productos para {query!r}."
    lines = [f"Productos ({len(rows)}):"]
    for p in rows:
        lines.append(
            f"#{p.id} [{p.pharmacy_chain}] {_truncate(p.drug_name, 70)}"
        )
    return "\n".join(lines)


async def _tool_get_product(args: dict[str, Any]) -> str:
    pid = int(args.get("id") or 0)
    if not pid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == pid)
        )
        product = result.scalar_one_or_none()
    if not product:
        return f"Producto #{pid} no existe."
    return (
        f"Producto #{product.id}\n"
        f"Cadena: {product.pharmacy_chain}\n"
        f"Nombre: {product.drug_name}\n"
        f"Marca: {product.brand or '-'}\n"
        f"Unidad: {product.unit_count} {product.unit_label or ''}\n"
        f"URL: {product.product_url or '-'}"
    )


# ── Stats tools ─────────────────────────────────────────────────────────

async def _tool_counts(_: dict[str, Any]) -> str:
    async with async_session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        pharmacies = await session.scalar(
            select(func.count()).select_from(PharmacyLocation)
        )
        active_pharmacies = await session.scalar(
            select(func.count()).select_from(PharmacyLocation).where(
                PharmacyLocation.is_active == True  # noqa: E712
            )
        )
        products = await session.scalar(select(func.count()).select_from(Product))
        feedback_total = await session.scalar(
            select(func.count()).select_from(UserFeedback)
        )
        feedback_open = await session.scalar(
            select(func.count()).select_from(UserFeedback).where(
                UserFeedback.reviewed == False  # noqa: E712
            )
        )
        searches = await session.scalar(select(func.count()).select_from(SearchLog))
    return (
        f"Conteos globales:\n"
        f"Usuarios: {users}\n"
        f"Farmacias: {active_pharmacies}/{pharmacies} activas\n"
        f"Productos: {products}\n"
        f"Búsquedas: {searches}\n"
        f"Feedback: {feedback_open} abierto / {feedback_total} total"
    )


async def _tool_top_searches(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    async with async_session() as session:
        result = await session.execute(
            select(
                SearchLog.query,
                func.count(SearchLog.id).label("n"),
            )
            .group_by(SearchLog.query)
            .order_by(desc("n"))
            .limit(limit)
        )
        rows = result.all()
    if not rows:
        return "Sin búsquedas."
    lines = [f"Top búsquedas ({len(rows)}):"]
    for query, count in rows:
        lines.append(f"  {count}× {query}")
    return "\n".join(lines)


# ── App settings tools ──────────────────────────────────────────────────

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


# ── Code introspection tools ───────────────────────────────────────────

def _is_allowed_path(rel: str) -> tuple[bool, str]:
    """Validate a path relative to PROJECT_ROOT against the allowlist.

    Returns (allowed, reason_if_not).
    """
    if not rel:
        return (False, "ruta vacía")
    # Reject absolute / home-expanded paths outright — even if they would
    # resolve inside PROJECT_ROOT, they're never a legitimate admin request.
    if rel.startswith(("/", "~")):
        return (False, "ruta fuera del proyecto")
    # Resolve the path and rely SOLELY on the post-resolution
    # ``relative_to(PROJECT_ROOT)`` guard to catch ``..`` escapes. Do NOT
    # pre-check for ``..`` segments — a naive split-based check gives false
    # confidence and can mask future bypasses. ``resolve()`` + ``relative_to``
    # is the correct and sufficient sandbox boundary.
    try:
        resolved = (PROJECT_ROOT / rel).resolve()
    except (OSError, ValueError):
        return (False, "ruta inválida")
    try:
        resolved_rel = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return (False, "ruta fuera del proyecto")
    rel_str = str(resolved_rel).replace("\\", "/")
    name = resolved.name
    # Reject hidden files and forbidden names/suffixes
    if name in _FORBIDDEN_NAMES or name.startswith("."):
        return (False, "archivo bloqueado")
    if name.endswith(_FORBIDDEN_SUFFIXES):
        return (False, "tipo de archivo bloqueado")
    # Must be either an allowed root file or inside an allowed dir prefix
    if rel_str in _ALLOWED_ROOT_FILES:
        return (True, "")
    for prefix in _ALLOWED_DIR_PREFIXES:
        if rel_str == prefix or rel_str.startswith(prefix + "/"):
            return (True, "")
    return (False, f"fuera del allowlist ({rel_str})")


async def _tool_read_code(args: dict[str, Any]) -> str:
    path = str(args.get("path", "")).strip()
    ok, reason = _is_allowed_path(path)
    if not ok:
        return f"Lectura denegada: {reason}"
    full = (PROJECT_ROOT / path).resolve()
    if not full.is_file():
        return f"{path} no existe."
    try:
        with open(full, "rb") as f:
            raw = f.read(MAX_READ_BYTES + 1)
    except OSError as exc:
        return f"Error leyendo {path}: {exc}"
    truncated = len(raw) > MAX_READ_BYTES
    text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    suffix = "\n...[truncado]" if truncated else ""
    return f"=== {path} ===\n{text}{suffix}"


async def _tool_list_code(args: dict[str, Any]) -> str:
    path = str(args.get("path", "src/farmafacil")).strip()
    ok, reason = _is_allowed_path(path)
    if not ok:
        return f"Listado denegado: {reason}"
    full = (PROJECT_ROOT / path).resolve()
    if not full.exists():
        return f"{path} no existe."
    if full.is_file():
        return f"{path} es un archivo (usa read_code)."
    entries = []
    try:
        for child in sorted(full.iterdir()):
            if child.name.startswith("."):
                continue
            rel = str(child.relative_to(PROJECT_ROOT)).replace("\\", "/")
            if child.is_dir():
                entries.append(f"  [dir] {rel}/")
            else:
                entries.append(f"  {rel}")
    except OSError as exc:
        return f"Error listando {path}: {exc}"
    if not entries:
        return f"{path}: vacío."
    total = len(entries)
    shown = entries[:100]
    suffix = (
        f"\n...[truncado a 100 de {total} entradas]" if total > 100 else ""
    )
    return f"{path}:\n" + "\n".join(shown) + suffix


# ── Tool registry ───────────────────────────────────────────────────────

# Mapping: tool name → (description shown in manifest, coroutine)
ToolFn = "Callable[[dict[str, Any]], Awaitable[str]]"  # type: ignore[name-defined]

# ── File management tools ───────────────────────────────────────────────


async def _tool_list_files(args: dict[str, Any]) -> str:
    """List files in user folder or project docs."""
    from farmafacil.services.file_manager import list_files

    scope = args.get("scope", "user")
    phone = args.get("phone")
    # If no phone given for user scope, use the admin's phone
    admin_id = args.get("_admin_user_id")
    if scope == "user" and not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    return list_files(phone=phone, scope=scope)


async def _tool_read_file(args: dict[str, Any]) -> str:
    """Read a file's content."""
    from farmafacil.services.file_manager import read_file

    path = args.get("path", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    return read_file(path, phone=phone)


async def _tool_write_file(args: dict[str, Any]) -> str:
    """Create or overwrite a file."""
    from farmafacil.services.file_manager import write_file

    path = args.get("path", "")
    content = args.get("content", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    if not path:
        return "Error: path es requerido."
    return write_file(path, content, phone=phone)


async def _tool_delete_file(args: dict[str, Any]) -> str:
    """Delete a file (user scope only)."""
    from farmafacil.services.file_manager import delete_file

    path = args.get("path", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    if not path:
        return "Error: path es requerido."
    return delete_file(path, phone=phone)


async def _tool_batch_simulate(args: dict[str, Any]) -> str:
    """Run a batch of questions through the pharmacy AI and save results.

    Reads a file with one question per line, runs each through
    classify_with_ai (pharmacy_advisor role), and saves the results
    to an output file in the admin's user folder.
    """
    from farmafacil.services.ai_responder import classify_with_ai
    from farmafacil.services.file_manager import read_file, write_file

    input_path = args.get("input_file", "")
    output_path = args.get("output_file", "batch_results.txt")

    admin_id = args.get("_admin_user_id")
    phone = None
    user_name = "TestUser"
    if admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
                user_name = user.name or "TestUser"

    if not input_path:
        return "Error: input_file es requerido (path al archivo con preguntas, una por línea)."

    content = read_file(input_path, phone=phone)
    if content.startswith("Error:") or content.startswith("File not found"):
        return content

    questions = [q.strip() for q in content.strip().split("\n") if q.strip()]
    if not questions:
        return "Error: el archivo está vacío o no tiene preguntas."

    lines = [f"Batch simulation — {len(questions)} questions\n{'='*50}\n"]

    for i, question in enumerate(questions, 1):
        try:
            result = await classify_with_ai(
                question, admin_id or 0, user_name,
            )
            lines.append(f"Q{i}: {question}")
            lines.append(f"ACTION: {result.action}")
            if result.drug_query:
                lines.append(f"DRUG: {result.drug_query}")
            if result.text:
                lines.append(f"RESPONSE: {result.text}")
            if result.clarify_question:
                lines.append(f"CLARIFY: {result.clarify_question}")
            lines.append("")
        except Exception as exc:
            lines.append(f"Q{i}: {question}")
            lines.append(f"ERROR: {exc}")
            lines.append("")

    output = "\n".join(lines)
    write_result = write_file(output_path, output, phone=phone)

    return f"Simulación completada: {len(questions)} preguntas. {write_result}"


# ── Web search tool ─────────────────────────────────────────────────────


async def _tool_web_search(args: dict[str, Any]) -> str:
    """Search the web via Brave Search API."""
    from farmafacil.services.web_search import web_search

    query = args.get("query", "").strip()
    if not query:
        return "Error: query es requerido."
    return await web_search(query)


# ── Scheduler tools ─────────────────────────────────────────────────────


async def _tool_list_scheduled_tasks(args: dict[str, Any]) -> str:
    """List all scheduled tasks with status."""
    from farmafacil.models.database import ScheduledTask

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).order_by(ScheduledTask.id)
        )
        tasks = result.scalars().all()

    if not tasks:
        return "No hay tareas programadas."

    lines = []
    for t in tasks:
        status_icon = {"idle": "⏸️", "running": "🔄", "success": "✅", "failed": "❌"}.get(t.status, "❓")
        enabled_label = "habilitada" if t.enabled else "pausada"
        last = t.last_run_at.strftime("%Y-%m-%d %H:%M") if t.last_run_at else "nunca"
        lines.append(
            f"• **#{t.id}** {status_icon} {t.name} ({enabled_label})\n"
            f"  Intervalo: {t.interval_minutes}min | Última: {last}\n"
            f"  Resultado: {t.last_result or 'N/A'}"
        )
    return "\n".join(lines)


async def _tool_run_scheduled_task(args: dict[str, Any]) -> str:
    """Run a task manually by ID."""
    from farmafacil.services.scheduler import run_task_now

    task_id = int(args.get("task_id", 0))
    if not task_id:
        return "Error: task_id es requerido."
    result = await run_task_now(task_id)
    return f"Tarea #{task_id} ejecutada. Resultado: {result}"


async def _tool_toggle_scheduled_task(args: dict[str, Any]) -> str:
    """Enable or disable a task."""
    from farmafacil.models.database import ScheduledTask

    task_id = int(args.get("task_id", 0))
    enabled = args.get("enabled", True)
    if not task_id:
        return "Error: task_id es requerido."

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(ScheduledTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return f"Tarea #{task_id} no encontrada."
        task.enabled = bool(enabled)
        label = "habilitada" if task.enabled else "pausada"
        await session.commit()

    return f"Tarea #{task_id} ({task.name}) ahora está {label}."


async def _tool_update_scheduled_task(args: dict[str, Any]) -> str:
    """Update the interval of a task."""
    from farmafacil.models.database import ScheduledTask

    task_id = int(args.get("task_id", 0))
    interval = int(args.get("interval_minutes", 0))
    if not task_id or not interval:
        return "Error: task_id e interval_minutes son requeridos."
    if interval < 1:
        return "Error: interval_minutes debe ser >= 1."

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).where(ScheduledTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return f"Tarea #{task_id} no encontrada."
        task.interval_minutes = interval
        await session.commit()

    return f"Tarea #{task_id} ({task.name}) intervalo actualizado a {interval} minutos."


TOOLS: dict[str, tuple[str, Any]] = {
    # Feedback
    "list_feedback": (
        "Listar casos recientes. Args: limit?, type?, reviewed?",
        _tool_list_feedback,
    ),
    "get_feedback": ("Ver un caso por id. Args: id", _tool_get_feedback),
    "update_feedback": (
        "Marcar revisado o agregar nota. Args: id, reviewed?, reviewer_notes?",
        _tool_update_feedback,
    ),
    "report_issue": (
        "Registrar bug/idea/issue flaggeado por el admin para el backlog de "
        "desarrollo. Args: type (bug|idea|issue), message",
        _tool_report_issue,
    ),
    # Conversation logs
    "list_conversation_logs": (
        "Listar logs recientes. Args: limit?, direction?, phone?",
        _tool_list_conversation_logs,
    ),
    "get_conversation_log": ("Ver un log por id. Args: id", _tool_get_conversation_log),
    # AI roles
    "list_ai_roles": ("Listar todos los roles AI.", _tool_list_ai_roles),
    "get_ai_role": ("Ver rol con sus reglas/skills. Args: name", _tool_get_ai_role),
    "update_ai_role": (
        "Actualizar rol. Args: name, description?, system_prompt?, is_active?",
        _tool_update_ai_role,
    ),
    "add_ai_rule": (
        "Agregar regla a un rol. Args: role_name, name, content, sort_order?",
        _tool_add_ai_rule,
    ),
    "update_ai_rule": (
        "Actualizar regla. Args: id, name?, content?, is_active?, sort_order?",
        _tool_update_ai_rule,
    ),
    "delete_ai_rule": ("Eliminar regla. Args: id", _tool_delete_ai_rule),
    "add_ai_skill": (
        "Agregar skill a un rol. Args: role_name, name, content",
        _tool_add_ai_skill,
    ),
    "update_ai_skill": (
        "Actualizar skill. Args: id, name?, content?, is_active?",
        _tool_update_ai_skill,
    ),
    "delete_ai_skill": ("Eliminar skill. Args: id", _tool_delete_ai_skill),
    # Users
    "list_users": (
        "Listar usuarios. Args: limit?, phone_like?", _tool_list_users,
    ),
    "get_user": (
        "Ver perfil de usuario. Args: user_ref (id o phone)", _tool_get_user,
    ),
    "get_user_memory": (
        "Leer memoria de usuario. Args: user_ref", _tool_get_user_memory,
    ),
    "set_user_memory": (
        "Escribir memoria de usuario. Args: user_ref, text", _tool_set_user_memory,
    ),
    "clear_user_memory": (
        "Borrar memoria de usuario. Args: user_ref", _tool_clear_user_memory,
    ),
    "set_user_setting": (
        "Actualizar un campo permitido del perfil. "
        "Args: user_ref, field, value. "
        "Campos: name, display_preference, response_mode_override, "
        "chat_debug, onboarding_step, admin_mode_active.",
        _tool_set_user_setting,
    ),
    # Pharmacies / products
    "list_pharmacies": (
        "Listar farmacias. Args: chain?, city?, is_active?, limit?",
        _tool_list_pharmacies,
    ),
    "toggle_pharmacy": (
        "Activar/desactivar farmacia. Args: id, is_active",
        _tool_toggle_pharmacy,
    ),
    "search_products": (
        "Buscar productos en el catálogo local. Args: query, limit?",
        _tool_search_products,
    ),
    "get_product": ("Ver producto por id. Args: id", _tool_get_product),
    # Stats
    "counts": ("Conteos globales (usuarios, farmacias, productos, etc).", _tool_counts),
    "top_searches": (
        "Top queries del search_logs. Args: limit?", _tool_top_searches,
    ),
    # App settings
    "list_app_settings": ("Listar todas las app_settings.", _tool_list_app_settings),
    "get_app_setting": ("Ver una setting. Args: key", _tool_get_app_setting),
    "set_app_setting": ("Actualizar setting. Args: key, value", _tool_set_app_setting),
    "get_default_model": (
        "Modelo default actual + lista de alias disponibles.",
        _tool_get_default_model,
    ),
    "set_default_model": (
        "Cambiar modelo default para usuarios. Args: alias (haiku|sonnet|opus)",
        _tool_set_default_model,
    ),
    # Code introspection
    "read_code": (
        "Leer archivo del proyecto (solo src/, tests/, docs/ y archivos raíz "
        "permitidos). Args: path",
        _tool_read_code,
    ),
    "list_code": (
        "Listar directorio del proyecto (allowlist). Args: path?",
        _tool_list_code,
    ),
    "list_files": (
        "Listar archivos en carpeta de usuario o docs del proyecto. "
        "Args: scope ('user'|'docs'), phone? (default: admin's phone)",
        _tool_list_files,
    ),
    "read_file": (
        "Leer contenido de un archivo. Args: path (user:file, docs/file, project:file), phone?",
        _tool_read_file,
    ),
    "write_file": (
        "Crear o sobrescribir un archivo. Args: path, content, phone?",
        _tool_write_file,
    ),
    "delete_file": (
        "Eliminar un archivo (solo carpeta de usuario). Args: path, phone?",
        _tool_delete_file,
    ),
    "batch_simulate": (
        "Ejecutar preguntas de un archivo por el AI de farmacia y guardar resultados. "
        "Args: input_file (path), output_file? (default: batch_results.txt)",
        _tool_batch_simulate,
    ),
    "web_search": (
        "Buscar en internet via Brave Search API. Args: query (str)",
        _tool_web_search,
    ),
    "list_scheduled_tasks": (
        "Listar todas las tareas programadas con su estado, intervalo, y "
        "última ejecución. Args: ninguno",
        _tool_list_scheduled_tasks,
    ),
    "run_scheduled_task": (
        "Ejecutar una tarea programada manualmente por ID. Args: task_id (int)",
        _tool_run_scheduled_task,
    ),
    "toggle_scheduled_task": (
        "Habilitar o deshabilitar una tarea programada. Args: task_id (int), enabled (bool)",
        _tool_toggle_scheduled_task,
    ),
    "update_scheduled_task": (
        "Actualizar intervalo de una tarea programada. Args: task_id (int), interval_minutes (int)",
        _tool_update_scheduled_task,
    ),
}


def build_tools_manifest() -> str:
    """Return the textual tool manifest injected into the admin system prompt."""
    lines = ["HERRAMIENTAS DISPONIBLES:"]
    for name, (desc, _) in TOOLS.items():
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines)


async def execute_tool(
    name: str, args: dict[str, Any], *, admin_user_id: int | None = None,
) -> str:
    """Dispatch a tool call by name. Safe-by-default on unknown / failure.

    Args:
        name: Tool name (must exist in ``TOOLS``).
        args: Arguments dict parsed from the LLM's TOOL_CALL block.
        admin_user_id: The calling admin's User.id — injected as
            ``_admin_user_id`` into args so tools that need audit context
            (``report_issue``) can attribute the action.

    Returns:
        A short text string describing the tool result, suitable to feed
        back to the LLM OR to forward to WhatsApp on a FINAL answer.
    """
    if name not in TOOLS:
        return f"Tool desconocida: {name}"
    if not isinstance(args, dict):
        args = {}
    # Strip any LLM-supplied `_admin_user_id` before injection so the LLM can
    # NEVER spoof the caller identity used for audit trails / report_issue.
    # The caller-provided ``admin_user_id`` kwarg is the single source of truth.
    args = {k: v for k, v in args.items() if k != "_admin_user_id"}
    if admin_user_id is not None:
        args["_admin_user_id"] = admin_user_id
    _, fn = TOOLS[name]
    try:
        return await fn(args)
    except Exception as exc:  # noqa: BLE001 — tool errors must never kill the loop
        logger.error(
            "admin_chat tool %s failed args=%s", name, args, exc_info=True,
        )
        return f"Error ejecutando {name}: {exc}"


def parse_tool_args(raw: str) -> dict[str, Any]:
    """Parse the ARGS block from an LLM TOOL_CALL.

    The LLM is instructed to emit JSON. We accept either a JSON object or an
    empty string (= no args). Non-JSON fallback: return empty dict — the tool
    can then report "Falta …" and the LLM will retry with corrected args.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
