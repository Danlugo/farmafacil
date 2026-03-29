"""User service — manage user registration and location."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from farmafacil.db.session import async_session
from farmafacil.models.database import User

logger = logging.getLogger(__name__)


async def get_or_create_user(phone_number: str) -> User:
    """Get an existing user or create a new one.

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
            user = User(phone_number=phone_number)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            logger.info("New user created: %s", phone_number)

        return user


async def update_user_location(
    phone_number: str,
    latitude: float,
    longitude: float,
    zone_name: str,
    city_code: str,
) -> User:
    """Update a user's location.

    Args:
        phone_number: WhatsApp phone number.
        latitude: GPS latitude.
        longitude: GPS longitude.
        zone_name: Human-readable zone name.
        city_code: Farmatodo city code.

    Returns:
        The updated User record.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_number == phone_number)
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(phone_number=phone_number)
            session.add(user)

        user.latitude = latitude
        user.longitude = longitude
        user.zone_name = zone_name
        user.city_code = city_code
        await session.commit()
        await session.refresh(user)
        logger.info("User %s location updated to %s (%s)", phone_number, zone_name, city_code)
        return user


async def user_has_location(phone_number: str) -> bool:
    """Check if a user has a saved location.

    Args:
        phone_number: WhatsApp phone number.

    Returns:
        True if the user has latitude/longitude set.
    """
    user = await get_or_create_user(phone_number)
    return user.latitude is not None and user.longitude is not None
