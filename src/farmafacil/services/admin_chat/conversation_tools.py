"""Admin chat tools: conversation logs."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, desc, select

from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog

from ._helpers import _truncate

logger = logging.getLogger(__name__)


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
