"""Admin chat tools: pharmacies and products."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import and_, or_, select, update

from farmafacil.db.session import async_session
from farmafacil.models.database import PharmacyLocation, Product

from ._helpers import _truncate

logger = logging.getLogger(__name__)


async def _tool_list_pharmacies(args: dict[str, Any]) -> str:
    limit = int(args.get("limit", 20) or 20)
    limit = max(1, min(limit, 100))
    chain = args.get("chain")
    city = args.get("city")
    is_active = args.get("is_active")
    async with async_session() as session:
        stmt = select(PharmacyLocation).order_by(PharmacyLocation.name).limit(limit)
        conds = []
        if chain:
            conds.append(PharmacyLocation.pharmacy_chain == str(chain))
        if city:
            conds.append(PharmacyLocation.city_code == str(city))
        if is_active is not None:
            conds.append(PharmacyLocation.is_active == bool(is_active))
        if conds:
            stmt = stmt.where(and_(*conds))
        result = await session.execute(stmt)
        rows = result.scalars().all()
    if not rows:
        return "Sin farmacias."
    lines = [f"Farmacias ({len(rows)}):"]
    for r in rows:
        status = "✓" if r.is_active else "✗"
        lines.append(
            f"{status} #{r.id} {r.pharmacy_chain} — {r.name} [{r.city_code}]"
        )
    return "\n".join(lines)


async def _tool_toggle_pharmacy(args: dict[str, Any]) -> str:
    pid = int(args.get("id") or 0)
    if not pid:
        return "Falta id."
    is_active = bool(args.get("is_active", False))
    async with async_session() as session:
        result = await session.execute(
            update(PharmacyLocation).where(PharmacyLocation.id == pid).values(
                is_active=is_active
            )
        )
        await session.commit()
    if result.rowcount == 0:
        return f"Farmacia #{pid} no existe."
    logger.info("admin_chat.toggle_pharmacy id=%d is_active=%s", pid, is_active)
    return f"Farmacia #{pid} {'activada' if is_active else 'desactivada'}."


async def _tool_search_products(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Falta query."
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    async with async_session() as session:
        stmt = (
            select(Product)
            .where(or_(
                Product.drug_name.ilike(f"%{query}%"),
                Product.brand.ilike(f"%{query}%"),
            ))
            .order_by(Product.drug_name)
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    if not rows:
        return f"Sin productos para {query!r}."
    lines = [f"Productos ({len(rows)}):"]
    for p in rows:
        lines.append(
            f"#{p.id} [{p.pharmacy_chain}] {_truncate(p.drug_name, 70)}"
        )
    return "\n".join(lines)


async def _tool_get_product(args: dict[str, Any]) -> str:
    pid = int(args.get("id") or 0)
    if not pid:
        return "Falta id."
    async with async_session() as session:
        result = await session.execute(
            select(Product).where(Product.id == pid)
        )
        product = result.scalar_one_or_none()
    if not product:
        return f"Producto #{pid} no existe."
    return (
        f"Producto #{product.id}\n"
        f"Cadena: {product.pharmacy_chain}\n"
        f"Nombre: {product.drug_name}\n"
        f"Marca: {product.brand or '-'}\n"
        f"Unidad: {product.unit_count} {product.unit_label or ''}\n"
        f"URL: {product.product_url or '-'}"
    )
