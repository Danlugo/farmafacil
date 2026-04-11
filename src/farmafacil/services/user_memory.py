"""User Memory service — per-client AI memory that persists across sessions.

Each user gets a markdown-formatted memory document (like a CLAUDE.md per client)
that stores preferences, conversation history, and context. Updated automatically
by the AI after conversations, and also editable by admins via the dashboard.

The memory builds a common-sense profile of each user over time: what they
search for, who they buy for, their health conditions, communication style,
preferred products, and behavioral patterns. It can reference profile data
(location, preferences) and search history without duplicating it.
"""

import logging

import anthropic
from anthropic import APIConnectionError, APIError
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL
from farmafacil.db.session import async_session
from farmafacil.models.database import SearchLog, User, UserMemory

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


async def _get_user_context(user_id: int) -> str:
    """Build a brief context string from user profile and search history.

    Provides the memory LLM with factual data about the user so it can
    build a well-rounded profile without hallucinating details.

    Args:
        user_id: The user's database ID.

    Returns:
        Context string with profile info and recent search patterns.
    """
    lines = []
    async with async_session() as session:
        # Profile info
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            lines.append(f"Name: {user.name or 'unknown'}")
            lines.append(f"Location: {user.zone_name or 'unknown'}")
            lines.append(f"Display preference: {user.display_preference or 'unknown'}")

        # Recent search queries (last 20)
        result = await session.execute(
            select(SearchLog.query, SearchLog.results_count)
            .where(SearchLog.user_id == user_id)
            .order_by(SearchLog.searched_at.desc())
            .limit(20)
        )
        searches = result.all()
        if searches:
            search_list = [f"  - {s.query} ({s.results_count} results)" for s in searches]
            lines.append(f"Recent searches ({len(searches)}):")
            lines.extend(search_list)

    return "\n".join(lines)


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
    user_context = await _get_user_context(user_id)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=(
                "You maintain a memory file for a pharmacy product search bot user "
                "(like a CLAUDE.md — a living document that builds common sense about "
                "this person). Given the existing memory, user profile, search history, "
                "and a new interaction, return the UPDATED memory.\n\n"
                "Keep it concise (bullet points in Spanish). Build a well-rounded profile:\n"
                "- Health conditions, chronic medications, recurring needs\n"
                "- Frequently searched products (note patterns from search history)\n"
                "- Who they buy for (family members, dependents)\n"
                "- Preferred pharmacies, zones, or brands\n"
                "- Communication style (formal/informal, language quirks)\n"
                "- Product preferences (generic vs brand, price-sensitive)\n"
                "- Life context clues (e.g., baby products → has young child)\n"
                "- Important complaints or feedback\n\n"
                "RULES:\n"
                "- Use the search history to identify patterns (2+ searches = recurring)\n"
                "- Don't duplicate profile data that's already in the system (location, name)\n"
                "- Do NOT add: greetings, generic bot commands, trivial one-time searches\n"
                "- If the interaction has nothing worth remembering, return UNCHANGED\n"
                "- Return ONLY the memory text in Spanish, no explanations"
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## User Profile\n{user_context}\n\n"
                        f"## Existing Memory\n{existing_memory or '(vacío — usuario nuevo)'}\n\n"
                        f"## New Interaction\n"
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

    except (APIError, APIConnectionError) as exc:
        logger.error(
            "Auto-update memory — Anthropic API error for user %d: %s",
            user_id, exc,
        )
    except SQLAlchemyError:
        logger.error(
            "Auto-update memory — DB error for user %d", user_id, exc_info=True,
        )
    except Exception:
        # Last-resort: memory update is non-critical (never raises to the
        # handler), so unexpected parsing/shape bugs should not crash the bot.
        logger.error(
            "Auto-update memory — unexpected error for user %d",
            user_id, exc_info=True,
        )
