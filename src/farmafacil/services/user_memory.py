"""User Memory service — per-client AI memory that persists across sessions.

Each user gets a markdown-formatted memory document (like a CLAUDE.md per client)
that stores preferences, conversation history, and context. Updated automatically
by the AI after conversations, and also editable by admins via the dashboard.
"""

import logging

import anthropic
from sqlalchemy import select

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL
from farmafacil.db.session import async_session
from farmafacil.models.database import UserMemory

logger = logging.getLogger(__name__)

MAX_MEMORY_LENGTH = 3000  # Max characters for memory text


async def get_memory(user_id: int) -> str:
    """Get the memory text for a user.

    Args:
        user_id: The user's database ID.

    Returns:
        Memory text string, or empty string if no memory exists.
    """
    async with async_session() as session:
        result = await session.execute(
            select(UserMemory).where(UserMemory.user_id == user_id)
        )
        memory = result.scalar_one_or_none()
        return memory.memory_text if memory else ""


async def update_memory(
    user_id: int,
    new_text: str,
    updated_by: str = "ai",
) -> None:
    """Update or create the memory for a user.

    Args:
        user_id: The user's database ID.
        new_text: The new memory text content.
        updated_by: Who updated it ('ai' or 'admin').
    """
    # Truncate if too long
    if len(new_text) > MAX_MEMORY_LENGTH:
        new_text = new_text[:MAX_MEMORY_LENGTH]

    async with async_session() as session:
        result = await session.execute(
            select(UserMemory).where(UserMemory.user_id == user_id)
        )
        memory = result.scalar_one_or_none()

        if memory:
            memory.memory_text = new_text
            memory.updated_by = updated_by
        else:
            memory = UserMemory(
                user_id=user_id,
                memory_text=new_text,
                updated_by=updated_by,
            )
            session.add(memory)

        await session.commit()


async def auto_update_memory(
    user_id: int,
    user_name: str,
    user_message: str,
    bot_response: str,
) -> None:
    """Auto-update user memory after a conversation using LLM.

    Makes a lightweight LLM call to extract memorable information from the
    conversation and merge it with the existing memory. Runs async and
    should not block the main response flow.

    Args:
        user_id: The user's database ID.
        user_name: The user's display name.
        user_message: What the user said.
        bot_response: What the bot replied.
    """
    if not ANTHROPIC_API_KEY:
        return

    existing_memory = await get_memory(user_id)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=(
                "You maintain a memory file for a pharmacy search bot user. "
                "Given the existing memory and a new conversation exchange, "
                "return the UPDATED memory. Keep it concise (bullet points). "
                "Only add genuinely useful information:\n"
                "- Health conditions, chronic medications\n"
                "- Family members they buy medicine for\n"
                "- Preferred pharmacies or zones\n"
                "- Language preferences or communication style\n"
                "- Important past interactions\n\n"
                "Do NOT add: greetings, generic searches, one-time queries.\n"
                "If the conversation has nothing worth remembering, return the "
                "existing memory UNCHANGED.\n"
                "Return ONLY the memory text, no explanations."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"User name: {user_name}\n\n"
                        f"Existing memory:\n{existing_memory or '(empty — new user)'}\n\n"
                        f"New conversation:\n"
                        f"User: {user_message}\n"
                        f"Bot: {bot_response[:500]}\n\n"
                        "Return the updated memory:"
                    ),
                }
            ],
        )
        new_memory = response.content[0].text.strip()

        # Only update if the LLM actually changed something
        if new_memory and new_memory != existing_memory:
            await update_memory(user_id, new_memory, updated_by="ai")
            logger.info("Auto-updated memory for user %d", user_id)
        else:
            logger.debug("No memory update needed for user %d", user_id)

    except Exception:
        logger.error("Auto-update memory failed for user %d", user_id, exc_info=True)
