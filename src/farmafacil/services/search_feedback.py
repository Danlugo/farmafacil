"""Search feedback service — records user satisfaction after drug searches."""

import logging

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import SearchLog

logger = logging.getLogger(__name__)

# ── Feedback parsing ──────────────────────────────────────────────────────

_POSITIVE = {"sí", "si", "yes", "👍", "1", "ok", "bien", "perfecto", "gracias"}
_NEGATIVE = {"no", "👎", "0", "nada", "mal", "nope"}


def parse_feedback(text: str) -> str | None:
    """Parse user feedback text into 'yes', 'no', or None (unrecognized).

    Args:
        text: User message text (already stripped).

    Returns:
        'yes', 'no', or None if the text doesn't look like feedback.
    """
    normalized = text.lower().strip().rstrip("!.?")
    if normalized in _POSITIVE:
        return "yes"
    if normalized in _NEGATIVE:
        return "no"
    return None


# ── Database operations ───────────────────────────────────────────────────


async def log_search(user_id: int, query: str, results_count: int) -> int:
    """Create a search log entry and return its ID.

    Args:
        user_id: The user's database ID.
        query: The search query string.
        results_count: Number of results found.

    Returns:
        The search log ID (used to attach feedback later).
    """
    async with async_session() as session:
        entry = SearchLog(
            user_id=user_id,
            query=query,
            results_count=results_count,
            source="whatsapp",
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        logger.info("Search logged: id=%d user=%d query='%s' results=%d",
                     entry.id, user_id, query[:50], results_count)
        return entry.id


async def record_feedback(search_log_id: int, feedback: str) -> None:
    """Record positive/negative feedback on a search log entry.

    Args:
        search_log_id: The search log entry ID.
        feedback: 'yes' or 'no'.
    """
    async with async_session() as session:
        result = await session.execute(
            select(SearchLog).where(SearchLog.id == search_log_id)
        )
        entry = result.scalar_one_or_none()
        if entry:
            entry.feedback = feedback
            await session.commit()
            logger.info("Feedback recorded: search_log=%d feedback=%s",
                        search_log_id, feedback)


async def record_feedback_detail(search_log_id: int, detail: str) -> None:
    """Record the user's explanation for negative feedback.

    Args:
        search_log_id: The search log entry ID.
        detail: The user's explanation text.
    """
    async with async_session() as session:
        result = await session.execute(
            select(SearchLog).where(SearchLog.id == search_log_id)
        )
        entry = result.scalar_one_or_none()
        if entry:
            entry.feedback_detail = detail
            await session.commit()
            logger.info("Feedback detail recorded: search_log=%d detail='%s'",
                        search_log_id, detail[:100])
