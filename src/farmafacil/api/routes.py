"""API route definitions."""

from fastapi import APIRouter, Query
from sqlalchemy import select

from farmafacil import __version__
from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, User
from farmafacil.models.schemas import HealthResponse, SearchRequest, SearchResponse
from farmafacil.services.search import search_drug

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(version=__version__)


@router.post("/api/v1/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """Search for a drug across all pharmacies."""
    return await search_drug(request.query, city=request.city)


@router.get("/api/v1/search", response_model=SearchResponse)
async def search_get(q: str, city: str | None = None) -> SearchResponse:
    """Search for a drug via GET (convenience for WhatsApp bot / browser)."""
    return await search_drug(q, city=city)


@router.get("/api/v1/conversations")
async def get_conversations(
    phone: str | None = None,
    limit: int = Query(50, le=200),
) -> list[dict]:
    """View conversation logs for troubleshooting.

    Args:
        phone: Optional phone number filter.
        limit: Max records to return (default 50).

    Returns:
        List of conversation log entries.
    """
    async with async_session() as session:
        query = select(ConversationLog).order_by(ConversationLog.created_at.desc())
        if phone:
            query = query.where(ConversationLog.phone_number.contains(phone))
        query = query.limit(limit)
        result = await session.execute(query)
        logs = result.scalars().all()

        return [
            {
                "id": log.id,
                "phone": log.phone_number,
                "direction": log.direction,
                "message": log.message_text[:500],
                "type": log.message_type,
                "wa_id": log.wa_message_id,
                "time": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ]


@router.get("/api/v1/users")
async def get_users(limit: int = Query(50, le=200)) -> list[dict]:
    """View registered users.

    Args:
        limit: Max records to return.

    Returns:
        List of user records.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
        users = result.scalars().all()

        return [
            {
                "id": u.id,
                "phone": u.phone_number,
                "zone": u.zone_name,
                "city_code": u.city_code,
                "lat": u.latitude,
                "lng": u.longitude,
                "created": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
