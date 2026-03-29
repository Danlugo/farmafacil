"""Pydantic schemas for API request/response models."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class DrugResult(BaseModel):
    """A single drug search result from a pharmacy."""

    drug_name: str = Field(..., description="Name of the drug as listed by the pharmacy")
    pharmacy_name: str = Field(..., description="Name of the pharmacy chain")
    price: Decimal | None = Field(None, description="Price in USD if available")
    price_bs: Decimal | None = Field(None, description="Price in Bolivares if available")
    available: bool = Field(..., description="Whether the drug is currently in stock")
    url: str | None = Field(None, description="Direct URL to the product page")
    last_checked: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC), description="When this result was last verified"
    )


class SearchRequest(BaseModel):
    """Drug search request."""

    query: str = Field(..., min_length=2, max_length=200, description="Drug name to search for")


class SearchResponse(BaseModel):
    """Drug search response with results from all pharmacies."""

    query: str
    results: list[DrugResult]
    total: int
    searched_pharmacies: list[str]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
