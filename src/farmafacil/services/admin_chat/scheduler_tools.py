"""Admin chat tools: scheduled task management."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from farmafacil.db.session import async_session

logger = logging.getLogger(__name__)


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
