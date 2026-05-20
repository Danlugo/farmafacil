"""Admin chat tools: user file management (list, read, write, delete)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import User

logger = logging.getLogger(__name__)


async def _tool_list_files(args: dict[str, Any]) -> str:
    """List files in user folder or project docs."""
    from farmafacil.services.file_manager import list_files

    scope = args.get("scope", "user")
    phone = args.get("phone")
    # If no phone given for user scope, use the admin's phone
    admin_id = args.get("_admin_user_id")
    if scope == "user" and not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    return list_files(phone=phone, scope=scope)


async def _tool_read_file(args: dict[str, Any]) -> str:
    """Read a file's content."""
    from farmafacil.services.file_manager import read_file

    path = args.get("path", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    return read_file(path, phone=phone)


async def _tool_write_file(args: dict[str, Any]) -> str:
    """Create or overwrite a file."""
    from farmafacil.services.file_manager import write_file

    path = args.get("path", "")
    content = args.get("content", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    if not path:
        return "Error: path es requerido."
    return write_file(path, content, phone=phone)


async def _tool_delete_file(args: dict[str, Any]) -> str:
    """Delete a file (user scope only)."""
    from farmafacil.services.file_manager import delete_file

    path = args.get("path", "")
    phone = args.get("phone")
    admin_id = args.get("_admin_user_id")
    if not phone and admin_id:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.id == admin_id)
            )
            user = result.scalar_one_or_none()
            if user:
                phone = user.phone_number
    if not path:
        return "Error: path es requerido."
    return delete_file(path, phone=phone)
