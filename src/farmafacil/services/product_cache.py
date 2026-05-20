"""Product catalog service — permanent product store with per-location pricing.

Replaces the old cache-and-delete approach. Products are never deleted, only
upserted. Prices are tracked per city_code with a refresh timestamp. Search
queries map to product IDs so we can serve cached results without re-hitting
the Algolia API while the data is fresh.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select

from farmafacil.db.session import _is_sqlite, async_session
from farmafacil.models.database import (
    Product,
    ProductKeyword,
    ProductPrice,
    SearchQuery,
)
from farmafacil.models.schemas import DrugResult
from farmafacil.services.relevance import classify_pharmaceutical, is_relevant
from farmafacil.services.settings import get_setting_float, get_setting_int

# Dialect-specific INSERT ... ON CONFLICT support.  Both SQLite (≥3.24) and
# PostgreSQL support this syntax; SQLAlchemy exposes it through per-dialect
# ``insert()`` functions.  We select the right one at import time based on
# the DATABASE_URL to avoid runtime overhead.
if _is_sqlite:
    from sqlalchemy.dialects.sqlite import insert as _dialect_insert
else:
    from sqlalchemy.dialects.postgresql import insert as _dialect_insert

logger = logging.getLogger(__name__)


async def get_cached_results(query: str, city_code: str | None) -> list[DrugResult] | None:
    """Check if we have fresh cached results for this query + city.

    Looks up the SearchQuery table to find product IDs, then checks if the
    prices for those products were refreshed within the TTL window.

    Args:
        query: Drug search query.
        city_code: Optional city code for localized pricing.

    Returns:
        List of DrugResult if cache hit and fresh, None if miss or stale.
    """
    ttl_minutes = await get_setting_int("cache_ttl_minutes")
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=ttl_minutes)
    normalized_query = query.lower().strip()

    async with async_session() as session:
        # Find the search query record
        result = await session.execute(
            select(SearchQuery).where(
                SearchQuery.query == normalized_query,
                SearchQuery.city_code == city_code,
            )
        )
        sq = result.scalar_one_or_none()

        if sq is None or not sq.product_ids:
            return None

        # Check if the search itself is fresh enough
        searched_at = sq.searched_at
        if searched_at.tzinfo is None:
            searched_at = searched_at.replace(tzinfo=UTC)
        if searched_at < cutoff:
            return None

        # Load products with their prices
        product_result = await session.execute(
            select(Product).where(Product.id.in_(sq.product_ids))
        )
        products = {p.id: p for p in product_result.scalars().all()}

        if not products:
            return None

        # Build DrugResult list in the original result order
        drug_results = []
        for pid in sq.product_ids:
            product = products.get(pid)
            if product is None:
                continue

            # Find price for the requested city
            price_row = None
            if city_code:
                for p in product.prices:
                    if p.city_code == city_code:
                        price_row = p
                        break
            elif product.prices:
                # No city specified — use first available price
                price_row = product.prices[0]

            drug_results.append(_product_to_drug_result(product, price_row))

        logger.info(
            "Cache hit for '%s' (city=%s): %d products",
            query, city_code, len(drug_results),
        )
        return drug_results


async def find_cached_products(
    query: str, city_code: str | None
) -> list[DrugResult] | None:
    """Search the product catalog by keyword for products with fresh prices.

    Queries the products table directly using the first keyword from the query,
    returning all matching products that have been refreshed within the cache TTL.
    This avoids unnecessary API calls when products were already cached by a
    previous broader search.

    Args:
        query: Drug search query (e.g., "RESVERATROL NAD+VID CAP 125MG X60 HERB").
        city_code: Optional city code for localized pricing.

    Returns:
        List of DrugResult if matching products found with fresh prices, None otherwise.
    """
    ttl_minutes = await get_setting_int("cache_ttl_minutes")
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=ttl_minutes)

    # Extract base term (first significant word) for broad DB search
    tokens = query.lower().strip().split()
    if not tokens:
        return None
    base_term = tokens[0]

    effective_city = city_code or "ALL"

    async with async_session() as session:
        # Find products whose drug_name contains the base keyword,
        # excluding known non-pharmaceutical products (Item 38)
        result = await session.execute(
            select(Product).where(
                func.lower(Product.drug_name).contains(base_term),
                Product.is_pharmaceutical.is_not(False),
            )
        )
        products = result.scalars().all()

        if not products:
            return None

        drug_results = []
        for product in products:
            # Find price for the requested city, check freshness
            price_row = None
            for p in product.prices:
                if p.city_code == effective_city:
                    refreshed_at = p.refreshed_at
                    if refreshed_at.tzinfo is None:
                        refreshed_at = refreshed_at.replace(tzinfo=UTC)
                    if refreshed_at >= cutoff:
                        price_row = p
                    break

            if price_row:
                drug_results.append(_product_to_drug_result(product, price_row))

        if drug_results:
            logger.info(
                "Found %d products in catalog for '%s' (city=%s)",
                len(drug_results), query, city_code,
            )
            return drug_results

    return None


async def save_search_results(
    query: str,
    city_code: str | None,
    results: list[DrugResult],
) -> None:
    """Bulk-upsert products, prices, keywords, and search query mapping.

    Uses ``INSERT ... ON CONFLICT DO UPDATE`` (Item 74) to collapse what was
    previously ~4N individual ORM round-trips (N = len(results)) into ~4
    batch statements:

    1. Bulk upsert products via ``INSERT ON CONFLICT (external_id, pharmacy_chain)``.
    2. SELECT back the product IDs in one query.
    3. Bulk upsert prices via ``INSERT ON CONFLICT (product_id, city_code)``.
    4. Bulk sync keywords: one DELETE + one INSERT for all products.
    5. Upsert the search-query → product_ids mapping (single row).

    Args:
        query: Drug search query (will be normalized).
        city_code: City code for the prices in these results.
        results: Drug results from the scraper.
    """
    if not results:
        return

    normalized_query = query.lower().strip()
    now = datetime.now(tz=UTC)
    now_naive = now.replace(tzinfo=None)

    # Read the relevance threshold from app settings
    threshold = await get_setting_float("relevance_threshold", 0.3)

    # ── Step 1: Prepare product rows ────────────────────────────────────
    # Build a list of dicts for bulk INSERT ... ON CONFLICT.
    product_rows: list[dict] = []
    ext_id_to_drug: dict[tuple[str, str], DrugResult] = {}

    for drug in results:
        external_id = _extract_external_id(drug)
        keywords = _parse_keywords(drug.drug_name)
        pharma = classify_pharmaceutical(drug.drug_class)
        key = (external_id, drug.pharmacy_name)
        ext_id_to_drug[key] = drug

        product_rows.append({
            "external_id": external_id,
            "pharmacy_chain": drug.pharmacy_name,
            "drug_name": drug.drug_name,
            "brand": drug.brand,
            "description": drug.description,
            "image_url": drug.image_url,
            "drug_class": drug.drug_class,
            "requires_prescription": drug.requires_prescription or False,
            "unit_count": drug.unit_count,
            "unit_label": drug.unit_label,
            "product_url": drug.url,
            "keywords": keywords,
            "is_pharmaceutical": pharma,
        })

    async with async_session() as session:
        # ── Step 2: Bulk upsert products ────────────────────────────────
        _PRODUCT_UPDATE_COLS = [
            "drug_name", "brand", "description", "image_url", "drug_class",
            "requires_prescription", "unit_count", "unit_label", "product_url",
            "keywords", "is_pharmaceutical",
        ]
        stmt = _dialect_insert(Product).values(product_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["external_id", "pharmacy_chain"],
            set_={
                col: stmt.excluded[col] for col in _PRODUCT_UPDATE_COLS
            } | {"updated_at": now_naive},
        )
        await session.execute(stmt)

        # ── Step 3: Fetch product IDs in one query ──────────────────────
        ext_ids = [r["external_id"] for r in product_rows]
        chains = [r["pharmacy_chain"] for r in product_rows]
        id_result = await session.execute(
            select(Product.id, Product.external_id, Product.pharmacy_chain).where(
                Product.external_id.in_(ext_ids),
                Product.pharmacy_chain.in_(set(chains)),
            )
        )
        id_map: dict[tuple[str, str], int] = {
            (row.external_id, row.pharmacy_chain): row.id
            for row in id_result.all()
        }

        # Build ordered product_id lists + price rows
        effective_city = city_code or "ALL"
        all_product_ids: list[int] = []
        relevant_product_ids: list[int] = []
        price_rows: list[dict] = []
        all_keywords: list[dict] = []  # (product_id, keyword) pairs
        affected_product_ids: list[int] = []

        for prow in product_rows:
            key = (prow["external_id"], prow["pharmacy_chain"])
            product_id = id_map.get(key)
            if product_id is None:
                logger.warning("Product ID not found after upsert: %s", key)
                continue

            drug = ext_id_to_drug[key]
            all_product_ids.append(product_id)
            affected_product_ids.append(product_id)

            # Relevance scoring — only relevant products enter the cache
            if is_relevant(
                query,
                drug.drug_name,
                drug.drug_class,
                drug.description,
                threshold,
                brand=drug.brand,
            ):
                relevant_product_ids.append(product_id)

            # Price row
            full_price = drug.full_price_bs if drug.full_price_bs else drug.price_bs
            offer_price = drug.price_bs if drug.full_price_bs else None
            price_rows.append({
                "product_id": product_id,
                "city_code": effective_city,
                "full_price_bs": full_price,
                "offer_price_bs": offer_price,
                "discount_pct": drug.discount_pct,
                "in_stock": drug.available or False,
                "stores_in_stock_count": drug.stores_in_stock or 0,
                "stores_with_stock_ids": drug.stores_with_stock_ids or [],
                "refreshed_at": now_naive,
            })

            # Keyword rows
            unique_kws = sorted({kw for kw in prow["keywords"] if kw})
            for kw in unique_kws:
                all_keywords.append({
                    "product_id": product_id,
                    "keyword": kw,
                })

        # ── Step 4: Bulk upsert prices ──────────────────────────────────
        if price_rows:
            price_stmt = _dialect_insert(ProductPrice).values(price_rows)
            price_stmt = price_stmt.on_conflict_do_update(
                index_elements=["product_id", "city_code"],
                set_={
                    "full_price_bs": price_stmt.excluded.full_price_bs,
                    "offer_price_bs": price_stmt.excluded.offer_price_bs,
                    "discount_pct": price_stmt.excluded.discount_pct,
                    "in_stock": price_stmt.excluded.in_stock,
                    "stores_in_stock_count": price_stmt.excluded.stores_in_stock_count,
                    "stores_with_stock_ids": price_stmt.excluded.stores_with_stock_ids,
                    "refreshed_at": price_stmt.excluded.refreshed_at,
                },
            )
            await session.execute(price_stmt)

        # ── Step 5: Bulk sync keywords ──────────────────────────────────
        if affected_product_ids:
            await session.execute(
                delete(ProductKeyword).where(
                    ProductKeyword.product_id.in_(affected_product_ids)
                )
            )
        if all_keywords:
            kw_stmt = _dialect_insert(ProductKeyword).values(all_keywords)
            kw_stmt = kw_stmt.on_conflict_do_nothing(
                index_elements=["product_id", "keyword"],
            )
            await session.execute(kw_stmt)

        # ── Step 6: Upsert search query ─────────────────────────────────
        filtered_count = len(all_product_ids) - len(relevant_product_ids)
        if filtered_count > 0:
            logger.info(
                "Relevance filter: %d/%d products filtered out for '%s' (threshold=%.2f)",
                filtered_count, len(all_product_ids), query, threshold,
            )

        await _upsert_search_query(
            session, normalized_query, city_code, relevant_product_ids,
            len(relevant_product_ids), now,
        )

        await session.commit()

    logger.info(
        "Saved %d relevant products for '%s' (city=%s, %d filtered out)",
        len(relevant_product_ids), query, city_code, filtered_count,
    )


def _parse_keywords(drug_name: str) -> list[str]:
    """Parse a drug name into lowercase whitespace-split tokens.

    Tokenizes the drug_name by splitting on whitespace and lowercasing each
    token. No stemming or fuzzy logic — tokens are exact lowercased words.
    The + character is preserved as part of adjacent tokens (e.g., "NAD+VID"
    stays as one token "nad+vid").

    Args:
        drug_name: Product name string (e.g., "RESVERATROL NAD+VID CAP 125MG X60 HERB").

    Returns:
        List of lowercase tokens (e.g., ["resveratrol", "nad+vid", "cap", "125mg", "x60", "herb"]).
    """
    return [token.lower() for token in drug_name.strip().split()]


async def find_cross_chain_matches(
    query_keywords: list[str],
    city_code: str | None,
    exclude_names: set[str],
) -> list["DrugResult"]:
    """Find products in other chains where ALL query keywords appear in product keywords.

    Uses the indexed ``product_keywords`` table (added in v0.12.6, Item 30):
    a single SQL query selects product ids whose keyword rows cover every
    token in ``query_keywords`` (``WHERE keyword IN (...) GROUP BY product_id
    HAVING COUNT(DISTINCT keyword) = N``). This replaces the earlier
    full-table-scan that loaded every product with non-null ``keywords``
    into Python and filtered client-side, which did not scale.

    Args:
        query_keywords: Lowercase tokens to match against (ALL must be present).
        city_code: City code to filter for available prices.
        exclude_names: Lowercased drug_name values already in exact results (to avoid duplicates).

    Returns:
        List of DrugResult for matching products from other chains.
    """
    if not query_keywords:
        return []

    # Deduplicate in case the caller passed repeated tokens — the HAVING
    # clause counts DISTINCT keywords, so duplicates would not break the
    # query, but we want a clean N that matches the IN (...) cardinality.
    unique_keywords = list({kw for kw in query_keywords if kw})
    if not unique_keywords:
        return []

    effective_city = city_code or "ALL"

    async with async_session() as session:
        # Step 1: indexed lookup on product_keywords — find product_ids
        # that match EVERY query keyword.
        matching_ids_stmt = (
            select(ProductKeyword.product_id)
            .where(ProductKeyword.keyword.in_(unique_keywords))
            .group_by(ProductKeyword.product_id)
            .having(
                func.count(func.distinct(ProductKeyword.keyword))
                == len(unique_keywords)
            )
        )
        id_result = await session.execute(matching_ids_stmt)
        product_ids = [row[0] for row in id_result.all()]

        if not product_ids:
            return []

        # Step 2: load the matched products (with prices via selectin).
        product_result = await session.execute(
            select(Product).where(Product.id.in_(product_ids))
        )
        products = product_result.scalars().all()

    matches: list[DrugResult] = []
    for product in products:
        if product.drug_name.lower().strip() in exclude_names:
            continue

        price_row = None
        for p in product.prices:
            if p.city_code == effective_city:
                price_row = p
                break

        matches.append(_product_to_drug_result(product, price_row))

    if matches:
        logger.info(
            "Cross-chain keyword match: %d products for keywords=%s (city=%s)",
            len(matches), unique_keywords, city_code,
        )

    return matches


def _extract_external_id(drug: DrugResult) -> str:
    """Extract a stable external ID from a DrugResult.

    Uses the URL slug (unique per Farmatodo product) or falls back to
    a combination of pharmacy_name + drug_name.

    Args:
        drug: The drug result.

    Returns:
        A string external ID for deduplication.
    """
    if drug.url:
        # Extract slug from URL like https://www.farmatodo.com.ve/slug
        slug = drug.url.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            return slug
    return f"{drug.pharmacy_name}:{drug.drug_name}".lower()


    # NOTE: The old row-by-row _upsert_product, _sync_product_keywords, and
    # _upsert_price helpers were removed in v0.25.0 (Item 74) and replaced
    # by the bulk INSERT ... ON CONFLICT logic above in save_search_results.


async def _upsert_search_query(
    session,
    query: str,
    city_code: str | None,
    product_ids: list[int],
    total: int,
    now: datetime,
) -> SearchQuery:
    """Find or create a search query mapping.

    Args:
        session: Active database session.
        query: Normalized search query.
        city_code: Optional city code.
        product_ids: Ordered list of product IDs.
        total: Total result count.
        now: Current timestamp.

    Returns:
        The SearchQuery ORM instance.
    """
    result = await session.execute(
        select(SearchQuery).where(
            SearchQuery.query == query,
            SearchQuery.city_code == city_code,
        )
    )
    sq = result.scalar_one_or_none()

    if sq is None:
        sq = SearchQuery(
            query=query,
            city_code=city_code,
            product_ids=product_ids,
            total_results=total,
            searched_at=now.replace(tzinfo=None),
        )
        session.add(sq)
    else:
        sq.product_ids = product_ids
        sq.total_results = total
        sq.searched_at = now.replace(tzinfo=None)

    return sq


def _product_to_drug_result(product: Product, price: ProductPrice | None) -> DrugResult:
    """Convert a Product + ProductPrice to a DrugResult schema.

    Args:
        product: The Product ORM instance.
        price: Optional ProductPrice for the requested city.

    Returns:
        A DrugResult Pydantic model.
    """
    # Determine best price for display
    if price:
        best_price = price.offer_price_bs or price.full_price_bs
        full_price = price.full_price_bs if price.offer_price_bs else None
        discount = price.discount_pct
        available = price.in_stock
        stores_in_stock = price.stores_in_stock_count
        stock_ids = price.stores_with_stock_ids or []
        last_checked = price.refreshed_at
    else:
        best_price = None
        full_price = None
        discount = None
        available = False
        stores_in_stock = 0
        stock_ids = []
        last_checked = product.updated_at or product.created_at

    # Build per-unit label
    unit_label_str = None
    if product.unit_count and product.unit_count > 0 and best_price:
        unit_price = Decimal(str(best_price)) / product.unit_count
        if product.unit_label:
            unit_label_str = f"{product.unit_label} {unit_price:.2f}"

    if last_checked and last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=UTC)

    return DrugResult(
        drug_name=product.drug_name,
        pharmacy_name=product.pharmacy_chain,
        price_bs=best_price,
        full_price_bs=full_price,
        discount_pct=discount,
        available=available,
        url=product.product_url,
        last_checked=last_checked,
        requires_prescription=product.requires_prescription or False,
        image_url=product.image_url,
        brand=product.brand,
        drug_class=product.drug_class,
        unit_label=unit_label_str,
        unit_count=product.unit_count,
        description=product.description,
        stores_in_stock=stores_in_stock,
        stores_with_stock_ids=stock_ids,
    )
