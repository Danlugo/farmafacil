"""Pydantic schemas for API request/response models."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class NearbyStore(BaseModel):
    """A store near the user that has a specific drug in stock."""

    store_name: str
    address: str
    distance_km: float
    price_bs: Decimal | None = None


class DrugResult(BaseModel):
    """A single drug search result from a pharmacy."""

    drug_name: str = Field(..., description="Name of the drug as listed by the pharmacy")
    pharmacy_name: str = Field(..., description="Name of the pharmacy chain")
    price: Decimal | None = Field(None, description="Price in USD if available")
    price_bs: Decimal | None = Field(None, description="Price in Bolivares if available")
    available: bool = Field(..., description="Whether the drug is currently in stock")
    url: str | None = Field(None, description="Direct URL to the product page")
    last_checked: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC),
        description="When this result was last verified",
    )
    requires_prescription: bool = Field(
        False, description="Whether a prescription is required"
    )
    image_url: str | None = Field(None, description="Product image URL")
    brand: str | None = Field(None, description="Drug brand/manufacturer")
    drug_class: str | None = Field(None, description="Pharmacological class")
    full_price_bs: Decimal | None = Field(None, description="Original price before discount")
    discount_pct: str | None = Field(None, description="Discount percentage (e.g., '20%')")
    unit_label: str | None = Field(None, description="Per-unit label (e.g., 'Capsulas a Bs')")
    unit_count: int | None = Field(None, description="Number of units in package")
    description: str | None = Field(None, description="Product description")
    stores_in_stock: int = Field(0, description="Number of stores with stock")
    stores_with_stock_ids: list[int] = Field(
        default_factory=list, description="Store IDs that have this drug"
    )
    nearby_stores: list[NearbyStore] = Field(
        default_factory=list, description="Nearby stores with this drug in stock"
    )


class SearchRequest(BaseModel):
    """Drug search request."""

    query: str = Field(
        ..., min_length=2, max_length=200, description="Drug name to search for"
    )
    city: str | None = Field(
        None, description="City for localized pricing (e.g., caracas, maracaibo)"
    )


class SearchResponse(BaseModel):
    """Drug search response with results from all pharmacies."""

    query: str
    city: str | None = None
    zone: str | None = None
    results: list[DrugResult]
    total: int
    searched_pharmacies: list[str]
    similar_count: int = 0


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
