"""User service — manage user registration, location, and preferences."""

import logging

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from farmafacil.db.session import async_session
from farmafacil.models.database import User

logger = logging.getLogger(__name__)


async def get_or_create_user(phone_number: str) -> User:
    """Get an existing user or create a new one (starts onboarding).

    Args:
        phone_number: WhatsApp phone number with country code.

    Returns:
        The User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(phone_number=phone_number, onboarding_step="welcome")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logger.info("New user created: %s", phone_number)

        return user


async def _get_user(phone_number: str) -> User:
    """Get a user by phone number (must exist)."""
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        return result.scalar_one()


async def update_user_name(phone_number: str, name: str) -> User:
    """Save the user's display name and advance onboarding.

    Args:
        phone_number: WhatsApp phone number.
        name: User's name.

    Returns:
        Updated User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one()
        user.name = name
        user.onboarding_step = "awaiting_location"
        await session.commit()
        await session.refresh(user)
        logger.info("User %s name set to '%s'", phone_number, name)
        return user


async def update_user_location(
    phone_number: str,
    latitude: float,
    longitude: float,
    zone_name: str,
    city_code: str,
) -> User:
    """Update a user's location and advance onboarding.

    Args:
        phone_number: WhatsApp phone number.
        latitude: GPS latitude.
        longitude: GPS longitude.
        zone_name: Human-readable zone name.
        city_code: Farmatodo city code.

    Returns:
        Updated User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one()
        user.latitude = latitude
        user.longitude = longitude
        user.zone_name = zone_name
        user.city_code = city_code
        user.onboarding_step = "awaiting_preference"
        await session.commit()
        await session.refresh(user)
        logger.info("User %s location: %s (%s)", phone_number, zone_name, city_code)
        return user


async def update_user_preference(phone_number: str, preference: str) -> User:
    """Save display preference and complete onboarding.

    Args:
        phone_number: WhatsApp phone number.
        preference: "grid" or "detail".

    Returns:
        Updated User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one()
        user.display_preference = preference
        user.onboarding_step = None  # Onboarding complete
        await session.commit()
        await session.refresh(user)
        logger.info("User %s preference: %s", phone_number, preference)
        return user


async def validate_user_profile(user: User) -> User:
    """Check user profile for inconsistent data and auto-repair.

    Catches states like: onboarding says "awaiting_preference" but name is
    missing, or onboarding is complete but location is missing.

    Args:
        user: The User record to validate.

    Returns:
        The (possibly repaired) User record.
    """
    step = user.onboarding_step
    needs_fix = False
    new_step = step

    if step is None:
        # Onboarding "complete" — verify all required fields exist
        if not user.name:
            new_step = "awaiting_name"
            needs_fix = True
        elif not user.latitude or not user.zone_name:
            new_step = "awaiting_location"
            needs_fix = True
        elif not user.display_preference:
            new_step = "awaiting_preference"
            needs_fix = True
    elif step == "awaiting_preference":
        # Should have name + location by now
        if not user.name:
            new_step = "awaiting_name"
            needs_fix = True
        elif not user.latitude or not user.zone_name:
            new_step = "awaiting_location"
            needs_fix = True
    elif step == "awaiting_location":
        # Should have name by now
        if not user.name:
            new_step = "awaiting_name"
            needs_fix = True

    if needs_fix:
        logger.warning(
            "User %s has inconsistent profile (step=%s, name=%s, zone=%s) — "
            "resetting to %s",
            user.phone_number, step, user.name, user.zone_name, new_step,
        )
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.phone_number == user.phone_number)
            )
            db_user = result.scalar_one()
            db_user.onboarding_step = new_step
            await session.commit()
            await session.refresh(db_user)
            return db_user

    return user


async def update_last_search(
    phone_number: str, query: str, search_log_id: int | None = None,
) -> None:
    """Store the user's last search query and search log ID.

    Args:
        phone_number: WhatsApp phone number.
        query: The drug search query.
        search_log_id: Optional search_logs.id for feedback tracking.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one()
        user.last_search_query = query
        if search_log_id is not None:
            user.last_search_log_id = search_log_id
        await session.commit()


def _classify_model(model: str) -> str:
    """Classify a model string into a known model family.

    Args:
        model: Full model name (e.g., "claude-haiku-4-5-20251001").

    Returns:
        Model family key: "haiku", "sonnet", or "unknown".
    """
    model_lower = model.lower()
    if "haiku" in model_lower:
        return "haiku"
    if "sonnet" in model_lower:
        return "sonnet"
    return "unknown"


async def increment_token_usage(
    user_id: int,
    input_tokens: int,
    output_tokens: int,
    model: str = "",
    *,
    is_admin: bool = False,
) -> None:
    """Atomically increment cumulative token counters, per-model counters, and call counts.

    Args:
        user_id: The user's database ID.
        input_tokens: Input tokens from the current LLM call.
        output_tokens: Output tokens from the current LLM call.
        model: LLM model name used for this call (e.g., "claude-haiku-4-5-20251001").
        is_admin: When True, route tokens to the admin bucket
            (``tokens_in_admin`` / ``tokens_out_admin`` / ``calls_admin``)
            instead of the user-facing per-model buckets. Admin chat turns
            are priced at Opus rates and tracked separately so they don't
            contaminate user-facing cost metrics.
    """
    if input_tokens == 0 and output_tokens == 0:
        return

    # Always update aggregate + last-call counters
    values: dict = {
        "total_tokens_in": User.total_tokens_in + input_tokens,
        "total_tokens_out": User.total_tokens_out + output_tokens,
        "last_tokens_in": input_tokens,
        "last_tokens_out": output_tokens,
    }

    # Route to per-model counters. Admin chat turns go to their own bucket,
    # regardless of which model the admin AI actually used, so admin token
    # stats stay isolated from user-facing stats.
    if is_admin:
        values["tokens_in_admin"] = User.tokens_in_admin + input_tokens
        values["tokens_out_admin"] = User.tokens_out_admin + output_tokens
        values["calls_admin"] = User.calls_admin + 1
    else:
        family = _classify_model(model)
        if family == "haiku":
            values["tokens_in_haiku"] = User.tokens_in_haiku + input_tokens
            values["tokens_out_haiku"] = User.tokens_out_haiku + output_tokens
            values["calls_haiku"] = User.calls_haiku + 1
        elif family == "sonnet":
            values["tokens_in_sonnet"] = User.tokens_in_sonnet + input_tokens
            values["tokens_out_sonnet"] = User.tokens_out_sonnet + output_tokens
            values["calls_sonnet"] = User.calls_sonnet + 1
        else:
            logger.warning(
                "Unknown model family for token tracking: '%s' — "
                "tokens added to aggregate only, not per-model counters",
                model,
            )

    try:
        async with async_session() as session:
            await session.execute(
                update(User).where(User.id == user_id).values(**values)
            )
            await session.commit()
    except SQLAlchemyError:
        logger.error(
            "Failed to persist token usage for user_id=%d "
            "(in=%d, out=%d, model=%s) — tokens lost",
            user_id, input_tokens, output_tokens, model,
            exc_info=True,
        )


async def set_awaiting_clarification(
    phone_number: str, context: str | None,
) -> None:
    """Store (or clear) the original vague query awaiting clarification.

    Used by the clarify_needed flow: when the bot asks a clarifying question
    about a vague category (e.g., "medicinas para la memoria"), it stashes
    the original query here. The next message from the user is merged with
    this context to form a refined drug search query.

    Args:
        phone_number: WhatsApp phone number.
        context: Original vague query, or None to clear.
    """
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.phone_number == phone_number)
            .values(awaiting_clarification_context=context)
        )
        await session.commit()


async def set_awaiting_category_search(
    phone_number: str, category: str | None,
) -> None:
    """Store (or clear) the category the user picked from the greeting menu.

    Used by the category-menu flow (Item 29, v0.13.2): when a returning user
    sends a bare greeting, the bot shows a WhatsApp list message with 5
    categories. When the user taps a category, the chosen category is stashed
    here while the bot waits for a freeform product name in the next message.

    Args:
        phone_number: WhatsApp phone number.
        category: Category label (e.g., "Medicamentos"), or None to clear.
    """
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.phone_number == phone_number)
            .values(awaiting_category_search=category)
        )
        await session.commit()


async def is_chat_admin(phone_number: str) -> bool:
    """Return True if the user has the ``chat_admin`` flag set.

    This flag is EDITABLE ONLY via the SQLAdmin dashboard — never from chat.
    It gates access to the ``/admin`` chat command and the App Admin role.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User.chat_admin).where(User.phone_number == phone_number)
        )
        value = result.scalar_one_or_none()
        return bool(value)


async def set_admin_mode(phone_number: str, active: bool) -> None:
    """Atomically toggle a user's ``admin_mode_active`` runtime flag.

    Used by the ``/admin`` / ``/admin off`` chat commands. Caller is
    responsible for enforcing the ``chat_admin`` gate before enabling.

    Args:
        phone_number: WhatsApp phone number.
        active: True to enter admin mode, False to exit.
    """
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.phone_number == phone_number)
            .values(admin_mode_active=bool(active))
        )
        await session.commit()


async def set_onboarding_step(phone_number: str, step: str | None) -> User:
    """Set the user's current onboarding step.

    Args:
        phone_number: WhatsApp phone number.
        step: Onboarding step or None (complete).

    Returns:
        Updated User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one()
        user.onboarding_step = step
        await session.commit()
        await session.refresh(user)
        return user
