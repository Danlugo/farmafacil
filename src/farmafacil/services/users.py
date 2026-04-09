"""User service — manage user registration, location, and preferences."""

import logging

from sqlalchemy import select, update

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


async def increment_token_usage(
    user_id: int, input_tokens: int, output_tokens: int,
) -> None:
    """Atomically increment cumulative token counters and store last call tokens.

    Args:
        user_id: The user's database ID.
        input_tokens: Input tokens from the current LLM call.
        output_tokens: Output tokens from the current LLM call.
    """
    if input_tokens == 0 and output_tokens == 0:
        return
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                total_tokens_in=User.total_tokens_in + input_tokens,
                total_tokens_out=User.total_tokens_out + output_tokens,
                last_tokens_in=input_tokens,
                last_tokens_out=output_tokens,
            )
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
