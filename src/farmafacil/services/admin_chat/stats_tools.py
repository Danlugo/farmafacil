"""Admin chat tools: stats and counts."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, func, select

from farmafacil.db.session import async_session
from farmafacil.models.database import (
    PharmacyLocation,
    Product,
    SearchLog,
    User,
    UserFeedback,
    UserSuggestion,
)

logger = logging.getLogger(__name__)


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
        suggestions_total = await session.scalar(
            select(func.count()).select_from(UserSuggestion)
        )
        suggestions_open = await session.scalar(
            select(func.count()).select_from(UserSuggestion).where(
                UserSuggestion.reviewed == False  # noqa: E712
            )
        )
    return (
        f"Conteos globales:\n"
        f"Usuarios: {users}\n"
        f"Farmacias: {active_pharmacies}/{pharmacies} activas\n"
        f"Productos: {products}\n"
        f"Búsquedas: {searches}\n"
        f"Feedback: {feedback_open} abierto / {feedback_total} total\n"
        f"Sugerencias: {suggestions_open} abierta / {suggestions_total} total"
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
