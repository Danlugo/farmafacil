"""Shared helpers used across admin_chat domain modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import User


async def _resolve_user_ref(user_ref: Any) -> User | None:
    """Look up a User by either numeric id or phone number string.

    Used by tools that accept ``user_ref`` as a polymorphic argument. ``int``
    or a digit-string that looks like a row id is treated as the primary key;
    anything else is treated as a phone number.
    """
    if user_ref is None:
        return None
    async with async_session() as session:
        if isinstance(user_ref, int):
            result = await session.execute(select(User).where(User.id == user_ref))
            return result.scalar_one_or_none()
        ref = str(user_ref).strip()
        if not ref:
            return None
        # Try id first if it's a pure number
        if ref.isdigit() and len(ref) <= 6:
            result = await session.execute(
                select(User).where(User.id == int(ref))
            )
            user = result.scalar_one_or_none()
            if user:
                return user
        # Fall back to phone match
        result = await session.execute(
            select(User).where(User.phone_number == ref)
        )
        return result.scalar_one_or_none()


def _fmt_bool(value: Any) -> str:
    return "si" if bool(value) else "no"


def _truncate(text: str | None, n: int = 80) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"
