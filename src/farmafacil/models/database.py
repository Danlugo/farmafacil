"""SQLAlchemy ORM models for the FarmaFacil database."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Neighborhood or zone name (e.g., El Cafetal)",
    )
    city_code: Mapped[str | None] = mapped_column(
        String(10), nullable=True, comment="Farmatodo city code (e.g., CCS, MCBO)",
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
