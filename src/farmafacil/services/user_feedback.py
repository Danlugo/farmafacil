"""User feedback service — stores /bug and /comentario command submissions.

Users can send `/bug <description>` or `/comentario <description>` to submit
feedback. Each submission is stored as a case record with a DB-generated ID
that the user receives as confirmation. Feedback is linked to the originating
conversation log row so reviewers can read the surrounding conversation
context when investigating an issue.
"""

import logging

from sqlalchemy import desc, select

from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, UserFeedback

logger = logging.getLogger(__name__)

# Valid feedback types
VALID_TYPES = {"bug", "comentario"}

# Max message length to accept (prevents abuse — WhatsApp messages are short)
MAX_MESSAGE_LENGTH = 2000


async def _find_latest_inbound_log_id(phone_number: str) -> int | None:
    """Return the id of the most recent inbound conversation log for this phone.

    Used to link a feedback case to the message that triggered it.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ConversationLog.id)
            .where(
                ConversationLog.phone_number == phone_number,
                ConversationLog.direction == "inbound",
            )
            .order_by(desc(ConversationLog.id))
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row


async def create_feedback(
    user_id: int,
    feedback_type: str,
    message: str,
    phone_number: str,
) -> int:
    """Create a feedback case and return its DB-generated ID.

    Args:
        user_id: The user's database ID.
        feedback_type: One of "bug", "comentario".
        message: The user's feedback text (body after the command).
        phone_number: The user's phone, used to link to their latest inbound log.

    Returns:
        The new feedback case ID (stored in DB, shown to the user).

    Raises:
        ValueError: If feedback_type is invalid or message is empty/too long.
    """
    if feedback_type not in VALID_TYPES:
        raise ValueError(
            f"Invalid feedback_type: {feedback_type!r}. "
            f"Must be one of {sorted(VALID_TYPES)}"
        )
    if not message or not message.strip():
        raise ValueError("Feedback message cannot be empty")
    message = message.strip()
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH]

    conversation_log_id = await _find_latest_inbound_log_id(phone_number)

    async with async_session() as session:
        entry = UserFeedback(
            user_id=user_id,
            feedback_type=feedback_type,
            message=message,
            conversation_log_id=conversation_log_id,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        logger.info(
            "Feedback created: id=%d user=%d type=%s conv_log=%s msg='%s'",
            entry.id, user_id, feedback_type,
            conversation_log_id, message[:80],
        )
        return entry.id


def parse_feedback_command(text: str) -> tuple[str, str] | None:
    """Parse a feedback command from message text.

    Recognises `/bug`, `/comentario`, and the common typo `/commentario`.
    The command must be at the start of the message (after stripping).

    Args:
        text: The raw user message.

    Returns:
        Tuple of (feedback_type, body) where feedback_type is "bug" or
        "comentario", and body is the text after the command. Returns None
        if the message is not a feedback command.

        If the command is present but the body is empty, body will be "".
    """
    if not text:
        return None
    stripped = text.strip()
    lower = stripped.lower()

    # Map of prefix → canonical type
    prefixes: list[tuple[str, str]] = [
        ("/bug", "bug"),
        ("/comentario", "comentario"),
        ("/commentario", "comentario"),  # common typo
    ]

    for prefix, feedback_type in prefixes:
        if lower == prefix:
            return (feedback_type, "")
        # Require a whitespace separator so "/bugabc" is not matched
        if lower.startswith(prefix) and len(lower) > len(prefix) and lower[len(prefix)].isspace():
            body = stripped[len(prefix):].strip()
            return (feedback_type, body)

    return None
