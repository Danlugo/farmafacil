"""SQLAlchemy ORM models for the FarmaFacil database."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class User(Base):
    """A FarmaFacil user identified by their WhatsApp phone number."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True,
        comment="WhatsApp phone number with country code",
    )
    name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="User display name",
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Neighborhood or zone name (e.g., El Cafetal)",
    )
    city_code: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="Farmatodo city code (e.g., CCS, MCBO)",
    )
    display_preference: Mapped[str] = mapped_column(
        String(20), default="grid",
        comment="How to show results: grid or detail",
    )
    onboarding_step: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="awaiting_name",
        comment="Current onboarding step (NULL = complete)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Pharmacy(Base):
    """Pharmacy chain that we scrape."""

    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    website_url: Mapped[str] = mapped_column(String(500), nullable=False)
    search_url_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="URL template with {query} placeholder",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class DrugListing(Base):
    """A drug found on a pharmacy website."""

    __tablename__ = "drug_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    drug_name_normalized: Mapped[str] = mapped_column(
        String(300), nullable=False, index=True,
        comment="Lowercase, stripped, for fuzzy matching",
    )
    price_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConversationLog(Base):
    """Log of every WhatsApp message in and out for troubleshooting."""

    __tablename__ = "conversation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True,
        comment="WhatsApp phone number",
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="inbound (user→bot) or outbound (bot→user)",
    )
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(20), default="text",
        comment="text, location, image, etc.",
    )
    wa_message_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="WhatsApp message ID for dedup",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IntentKeyword(Base):
    """Keyword-to-intent mapping, editable via admin."""

    __tablename__ = "intent_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="Intent action: greeting, help, location_change, preference_change, name_change, farewell",
    )
    keyword: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True,
        comment="Lowercase keyword or phrase to match",
    )
    response: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Optional canned response for this keyword",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PharmacyLocation(Base):
    """A physical pharmacy location — generic across all pharmacy chains."""

    __tablename__ = "pharmacy_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="ID from the source system (e.g., Farmatodo store ID)",
    )
    pharmacy_chain: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Which chain: Farmatodo, Locatel, XANA, etc.",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name_lower: Mapped[str] = mapped_column(String(100), nullable=False, index=True,
        comment="Lowercase for case-insensitive lookup",
    )
    city_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ProductCache(Base):
    """Cached Algolia product search results with TTL.

    DEPRECATED: Replaced by Product + ProductPrice + SearchQuery tables.
    Kept for backward compatibility during migration.
    """

    __tablename__ = "product_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    city_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    results_json: Mapped[str] = mapped_column(Text, nullable=False,
        comment="JSON-serialized list of DrugResult dicts",
    )
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    cached_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Product(Base):
    """Permanent product catalog — never deleted, only updated."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
        comment="Algolia objectID or equivalent, for deduplication",
    )
    pharmacy_chain: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Which chain: Farmatodo, Armirene, etc.",
    )
    drug_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    drug_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    requires_prescription: Mapped[bool] = mapped_column(Boolean, default=False)
    unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_label: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Per-unit label (e.g., 'Capsulas')",
    )
    product_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["ProductPrice"]] = relationship(
        "ProductPrice", back_populates="product", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("external_id", "pharmacy_chain", name="uq_product_external"),
    )


class ProductPrice(Base):
    """Per-location pricing for a product — updated on each search refresh."""

    __tablename__ = "product_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    city_code: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True,
        comment="Farmatodo city code (e.g., CCS, MCBO)",
    )
    full_price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    offer_price_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    discount_pct: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="Discount text (e.g., '20%')",
    )
    in_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    stores_in_stock_count: Mapped[int] = mapped_column(Integer, default=0)
    stores_with_stock_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="JSON list of store IDs with stock",
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(),
        comment="When this price was last updated from the API",
    )

    product: Mapped["Product"] = relationship("Product", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("product_id", "city_code", name="uq_price_product_city"),
    )


class SearchQuery(Base):
    """Maps search queries to product results for cache lookups."""

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True,
        comment="Normalized search term (lowercase, stripped)",
    )
    city_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    product_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="Ordered list of product IDs in result order",
    )
    total_results: Mapped[int] = mapped_column(Integer, default=0)
    searched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("query", "city_code", name="uq_search_query_city"),
    )


class AppSetting(Base):
    """Admin-editable application settings."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SearchLog(Base):
    """Log of user searches for analytics."""

    __tablename__ = "search_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(
        String(50), default="whatsapp", comment="api, whatsapp, web",
    )
    searched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
