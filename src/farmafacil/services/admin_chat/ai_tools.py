"""Admin chat tools: AI roles, rules, and skills."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete as sql_delete, select, update

from farmafacil.db.session import async_session
from farmafacil.models.database import AiRole, AiRoleRule, AiRoleSkill

from ._helpers import _fmt_bool, _truncate

logger = logging.getLogger(__name__)


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
