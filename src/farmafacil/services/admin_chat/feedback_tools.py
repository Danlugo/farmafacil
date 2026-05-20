"""Admin chat tools: feedback, suggestions, and voice messages."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, desc, func, select, update

from farmafacil.db.session import async_session
from farmafacil.models.database import (
    SearchLog,
    UserFeedback,
    UserSuggestion,
    VoiceMessage,
)

from ._helpers import _fmt_bool, _truncate

logger = logging.getLogger(__name__)


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


# ── Suggestion tools ──────────────────────────────────────────────────

async def _tool_list_suggestions(args: dict[str, Any]) -> str:
    """List user suggestions, optionally filtered by reviewed status."""
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    reviewed = args.get("reviewed")

    async with async_session() as session:
        stmt = (
            select(UserSuggestion)
            .order_by(desc(UserSuggestion.created_at))
            .limit(limit)
        )
        if reviewed is not None:
            stmt = stmt.where(UserSuggestion.reviewed == bool(reviewed))
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return "Sin sugerencias."
    lines = [f"Sugerencias ({len(rows)}):"]
    for r in rows:
        rev = "✓" if r.reviewed else "•"
        lines.append(f"{rev} #{r.id} {_truncate(r.message, 60)}")
    return "\n".join(lines)


async def _tool_get_suggestion(args: dict[str, Any]) -> str:
    """Get full details of a single suggestion by ID."""
    sid = int(args.get("id") or 0)
    if not sid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(UserSuggestion).where(UserSuggestion.id == sid)
        )
        sug = result.scalar_one_or_none()
    if not sug:
        return f"Sugerencia #{sid} no existe."
    reviewed_at = sug.reviewed_at.strftime("%Y-%m-%d %H:%M") if sug.reviewed_at else "(pendiente)"
    return (
        f"Sugerencia #{sug.id}\n"
        f"Usuario_id: {sug.user_id}\n"
        f"Teléfono: {sug.phone_number}\n"
        f"Revisado: {_fmt_bool(sug.reviewed)}\n"
        f"Revisado_en: {reviewed_at}\n"
        f"Fecha: {sug.created_at:%Y-%m-%d %H:%M}\n"
        f"Mensaje: {sug.message}\n"
        f"Notas: {sug.admin_notes or '(ninguna)'}"
    )


async def _tool_update_suggestion(args: dict[str, Any]) -> str:
    """Mark a suggestion as reviewed and/or add admin notes."""
    sid = int(args.get("id") or 0)
    if not sid:
        return "Falta id."
    values: dict[str, Any] = {}
    if "reviewed" in args:
        values["reviewed"] = bool(args["reviewed"])
        if values["reviewed"]:
            values["reviewed_at"] = func.now()
    if "admin_notes" in args:
        values["admin_notes"] = str(args["admin_notes"])[:2000]
    if not values:
        return "Nada que actualizar."
    async with async_session() as session:
        result = await session.execute(
            update(UserSuggestion)
            .where(UserSuggestion.id == sid)
            .values(**values)
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Sugerencia #{sid} no existe."
    logger.info("admin_chat.update_suggestion id=%d values=%s", sid, list(values))
    return f"Sugerencia #{sid} actualizada."


# ── Voice message tools ──────────────────────────────────────────────────

async def _tool_list_voice_messages(args: dict[str, Any]) -> str:
    """List recent voice messages, optionally filtered by user phone."""
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    phone = args.get("phone")

    async with async_session() as session:
        stmt = (
            select(VoiceMessage)
            .order_by(desc(VoiceMessage.created_at))
            .limit(limit)
        )
        if phone:
            stmt = stmt.where(VoiceMessage.phone_number == str(phone))
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return "Sin mensajes de voz."
    lines = [f"Mensajes de voz ({len(rows)}):"]
    for r in rows:
        tx = _truncate(r.transcription or "(sin transcripción)", 50)
        lang = r.original_language or "?"
        dur = f"{r.duration_seconds:.0f}s" if r.duration_seconds else "?"
        lines.append(
            f"🎙️ #{r.id} [{lang}] {dur} — {tx} "
            f"(tel: {r.phone_number}, {r.created_at:%Y-%m-%d %H:%M})"
        )
    return "\n".join(lines)


async def _tool_get_voice_message(args: dict[str, Any]) -> str:
    """Get full details of a single voice message by ID, including linked actions."""
    vm_id = int(args.get("id") or 0)
    if not vm_id:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(VoiceMessage).where(VoiceMessage.id == vm_id)
        )
        vm = result.scalar_one_or_none()
        if not vm:
            return f"Mensaje de voz #{vm_id} no existe."

        # Find linked actions (searches, feedback, suggestions)
        linked_searches = (await session.execute(
            select(SearchLog).where(SearchLog.voice_message_id == vm_id)
        )).scalars().all()
        linked_feedback = (await session.execute(
            select(UserFeedback).where(UserFeedback.voice_message_id == vm_id)
        )).scalars().all()
        linked_suggestions = (await session.execute(
            select(UserSuggestion).where(UserSuggestion.voice_message_id == vm_id)
        )).scalars().all()

    dur = f"{vm.duration_seconds:.1f}s" if vm.duration_seconds else "(desconocido)"
    lines = [
        f"Mensaje de voz #{vm.id}",
        f"Usuario_id: {vm.user_id}",
        f"Teléfono: {vm.phone_number}",
        f"Idioma: {vm.original_language or '(no detectado)'}",
        f"Duración: {dur}",
        f"Audio: {vm.audio_path}",
        f"Transcripción: {vm.transcription or '(sin transcripción)'}",
        f"Traducción ES: {vm.translation_es or '(pendiente)'}",
        f"Traducción EN: {vm.translation_en or '(pendiente)'}",
        f"Modelo: {vm.transcription_model or '(desconocido)'}",
        f"WA msg: {vm.wa_message_id}",
        f"Conversación: #{vm.conversation_log_id or '(no vinculado)'}",
        f"Fecha: {vm.created_at:%Y-%m-%d %H:%M}",
    ]

    # Append linked action summary
    if linked_searches:
        lines.append(f"\n🔍 Búsquedas vinculadas ({len(linked_searches)}):")
        for s in linked_searches:
            lines.append(f"  #{s.id}: '{s.query}' → {s.results_count} resultados, feedback={s.feedback or '—'}")
    if linked_feedback:
        lines.append(f"\n🐛 Feedback vinculado ({len(linked_feedback)}):")
        for fb in linked_feedback:
            lines.append(f"  #{fb.id}: [{fb.feedback_type}] {_truncate(fb.message, 60)}")
    if linked_suggestions:
        lines.append(f"\n💡 Sugerencias vinculadas ({len(linked_suggestions)}):")
        for s in linked_suggestions:
            lines.append(f"  #{s.id}: {_truncate(s.message, 60)}")
    if not (linked_searches or linked_feedback or linked_suggestions):
        lines.append("\n(Sin acciones vinculadas)")

    return "\n".join(lines)
