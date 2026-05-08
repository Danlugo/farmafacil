"""User suggestion service — stores /sugerencia command submissions.

Users send ``/sugerencia <description>`` to propose a preferred behavior or
feature idea.  Each submission is stored as a record with a DB-generated ID
that the user receives as confirmation.
"""

import logging

from farmafacil.db.session import async_session
from farmafacil.models.database import UserSuggestion

logger = logging.getLogger(__name__)

# Max message length to accept (prevents abuse — WhatsApp messages are short)
MAX_MESSAGE_LENGTH = 2000


async def create_suggestion(
    user_id: int,
    phone_number: str,
    message: str,
) -> int:
    """Create a suggestion record and return its DB-generated ID.

    Args:
        user_id: The user's database ID.
        phone_number: The user's phone number (E.164 format).
        message: The user's suggestion text (body after the command).

    Returns:
        The new suggestion ID (stored in DB, shown to the user).

    Raises:
        ValueError: If message is empty or whitespace-only.
    """
    if not message or not message.strip():
        raise ValueError("Suggestion message cannot be empty")
    message = message.strip()
    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH]

    async with async_session() as session:
        entry = UserSuggestion(
            user_id=user_id,
            phone_number=phone_number,
            message=message,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        logger.info(
            "Suggestion created: id=%d user=%d msg='%s'",
            entry.id, user_id, message[:80],
        )
        return entry.id


def parse_suggestion_command(text: str) -> str | None:
    """Parse a /sugerencia command from message text.

    Recognises ``/sugerencia`` at the start of the message (after stripping).

    Args:
        text: The raw user message.

    Returns:
        The body text after the command, or ``""`` if the command is present
        but has no body.  Returns ``None`` if the message is not a
        ``/sugerencia`` command.
    """
    if not text:
        return None
    stripped = text.strip()
    lower = stripped.lower()

    prefix = "/sugerencia"

    if lower == prefix:
        return ""
    # Require a whitespace separator so "/sugerenciaXYZ" is not matched
    if (
        lower.startswith(prefix)
        and len(lower) > len(prefix)
        and lower[len(prefix)].isspace()
    ):
        body = stripped[len(prefix):].strip()
        return body

    return None
