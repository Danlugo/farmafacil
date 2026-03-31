"""Product catalog service — permanent product store with per-location pricing.

Replaces the old cache-and-delete approach. Products are never deleted, only
upserted. Prices are tracked per city_code with a refresh timestamp. Search
queries map to product IDs so we can serve cached results without re-hitting
the Algolia API while the data is fresh.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from farmafacil.db.session import async_session
from farmafacil.models.database import Product, ProductPrice, SearchQuery
from farmafacil.models.schemas import DrugResult
from farmafacil.services.settings import get_setting_int

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


async def save_search_results(
    query: str,
    city_code: str | None,
    results: list[DrugResult],
) -> None:
    """Upsert products, prices, and search query mapping.

    For each DrugResult:
    1. Find or create a Product row by external_id + pharmacy_chain.
    2. Update all product attributes.
    3. Find or create a ProductPrice row by product_id + city_code.
    4. Update price, stock, discount, refreshed_at.
    5. Upsert the SearchQuery row mapping query+city to product IDs.

    Args:
        query: Drug search query (will be normalized).
        city_code: City code for the prices in these results.
        results: Drug results from the scraper.
    """
    if not results:
        return

    normalized_query = query.lower().strip()
    now = datetime.now(tz=UTC)
    product_ids: list[int] = []

    async with async_session() as session:
        for drug in results:
            # Determine external_id — use product URL slug or drug_name as fallback
            external_id = _extract_external_id(drug)

            # Find or create product
            product = await _upsert_product(session, drug, external_id, now)
            product_ids.append(product.id)

            # Upsert price for this city
            effective_city = city_code or "ALL"
            await _upsert_price(session, product.id, effective_city, drug, now)

        # Upsert search query mapping
        await _upsert_search_query(
            session, normalized_query, city_code, product_ids, len(results), now,
        )

        await session.commit()

    logger.info(
        "Saved %d products for '%s' (city=%s)",
        len(product_ids), query, city_code,
    )


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


async def _upsert_product(
    session, drug: DrugResult, external_id: str, now: datetime,
) -> Product:
    """Find or create a product, updating its attributes.

    Args:
        session: Active database session.
        drug: Drug result with product data.
        external_id: Unique external identifier.
        now: Current timestamp.

    Returns:
        The Product ORM instance (with id set).
    """
    result = await session.execute(
        select(Product).where(
            Product.external_id == external_id,
            Product.pharmacy_chain == drug.pharmacy_name,
        )
    )
    product = result.scalar_one_or_none()

    if product is None:
        product = Product(
            external_id=external_id,
            pharmacy_chain=drug.pharmacy_name,
            drug_name=drug.drug_name,
            brand=drug.brand,
            description=drug.description,
            image_url=drug.image_url,
            drug_class=drug.drug_class,
            requires_prescription=drug.requires_prescription,
            unit_count=drug.unit_count,
            unit_label=drug.unit_label,
            product_url=drug.url,
        )
        session.add(product)
        await session.flush()  # Get the id
    else:
        # Update existing product attributes
        product.drug_name = drug.drug_name
        product.brand = drug.brand
        product.description = drug.description
        product.image_url = drug.image_url
        product.drug_class = drug.drug_class
        product.requires_prescription = drug.requires_prescription
        product.unit_count = drug.unit_count
        product.unit_label = drug.unit_label
        product.product_url = drug.url
        product.updated_at = now.replace(tzinfo=None)

    return product


async def _upsert_price(
    session,
    product_id: int,
    city_code: str,
    drug: DrugResult,
    now: datetime,
) -> ProductPrice:
    """Find or create a price record, updating pricing attributes.

    Args:
        session: Active database session.
        product_id: FK to the product.
        city_code: City code for this price.
        drug: Drug result with pricing data.
        now: Current timestamp.

    Returns:
        The ProductPrice ORM instance.
    """
    result = await session.execute(
        select(ProductPrice).where(
            ProductPrice.product_id == product_id,
            ProductPrice.city_code == city_code,
        )
    )
    price = result.scalar_one_or_none()

    # Determine full vs offer price
    full_price = drug.full_price_bs if drug.full_price_bs else drug.price_bs
    offer_price = drug.price_bs if drug.full_price_bs else None

    if price is None:
        price = ProductPrice(
            product_id=product_id,
            city_code=city_code,
            full_price_bs=full_price,
            offer_price_bs=offer_price,
            discount_pct=drug.discount_pct,
            in_stock=drug.available,
            stores_in_stock_count=drug.stores_in_stock,
            stores_with_stock_ids=drug.stores_with_stock_ids or [],
            refreshed_at=now.replace(tzinfo=None),
        )
        session.add(price)
    else:
        price.full_price_bs = full_price
        price.offer_price_bs = offer_price
        price.discount_pct = drug.discount_pct
        price.in_stock = drug.available
        price.stores_in_stock_count = drug.stores_in_stock
        price.stores_with_stock_ids = drug.stores_with_stock_ids or []
        price.refreshed_at = now.replace(tzinfo=None)

    return price


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
