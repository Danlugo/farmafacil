"""Conversation logging — stores every inbound and outbound WhatsApp message."""

import logging

from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog

logger = logging.getLogger(__name__)


async def log_inbound(
    phone_number: str,
    message_text: str,
    message_type: str = "text",
    wa_message_id: str | None = None,
) -> None:
    """Log an incoming message from a user.

    Args:
        phone_number: Sender's WhatsApp number.
        message_text: The message content.
        message_type: Message type (text, location, image, etc.).
        wa_message_id: WhatsApp message ID for dedup.
    """
    async with async_session() as session:
        entry = ConversationLog(
            phone_number=phone_number,
            direction="inbound",
            message_text=message_text,
            message_type=message_type,
            wa_message_id=wa_message_id,
        )
        session.add(entry)
        await session.commit()


async def log_outbound(phone_number: str, message_text: str) -> None:
    """Log an outgoing message from the bot.

    Args:
        phone_number: Recipient's WhatsApp number.
        message_text: The message content sent.
    """
    async with async_session() as session:
        entry = ConversationLog(
            phone_number=phone_number,
            direction="outbound",
            message_text=message_text,
            message_type="text",
        )
        session.add(entry)
        await session.commit()
