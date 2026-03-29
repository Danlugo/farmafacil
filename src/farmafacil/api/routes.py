"""API route definitions."""

from fastapi import APIRouter

from farmafacil import __version__
from farmafacil.models.schemas import HealthResponse, SearchRequest, SearchResponse
from farmafacil.services.search import search_drug

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(version=__version__)


@router.post("/api/v1/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Search for a drug across all pharmacies.

    Args:
        request: Search request with the drug name query.

    Returns:
        Aggregated results from all pharmacy scrapers.
    """
    return await search_drug(request.query)


@router.get("/api/v1/search", response_model=SearchResponse)
async def search_get(q: str) -> SearchResponse:
    """Search for a drug via GET (convenience for WhatsApp bot / browser).

    Args:
        q: Drug name query string.

    Returns:
        Aggregated results from all pharmacy scrapers.
    """
    return await search_drug(q)
