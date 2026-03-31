"""User service — manage user registration, location, and preferences."""

import logging

from sqlalchemy import select

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
