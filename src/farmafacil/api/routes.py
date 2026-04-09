"""API route definitions."""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from farmafacil import __version__
from farmafacil.db.session import async_session
from pydantic import BaseModel

from farmafacil.models.database import ConversationLog, IntentKeyword, SearchLog, User
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
                "name": u.name,
                "zone": u.zone_name,
                "city_code": u.city_code,
                "lat": u.latitude,
                "lng": u.longitude,
                "display_preference": u.display_preference,
                "onboarding_step": u.onboarding_step,
                "created": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]


# ── Intent Keywords Management ──────────────────────────────────────────


class IntentCreate(BaseModel):
    action: str
    keyword: str
    response: str | None = None


@router.get("/api/v1/intents")
async def get_intents(action: str | None = None) -> list[dict]:
    """List all intent keywords, optionally filtered by action."""
    async with async_session() as session:
        query = select(IntentKeyword).order_by(IntentKeyword.action, IntentKeyword.keyword)
        if action:
            query = query.where(IntentKeyword.action == action)
        result = await session.execute(query)
        intents = result.scalars().all()
        return [
            {
                "id": i.id,
                "action": i.action,
                "keyword": i.keyword,
                "response": i.response,
                "is_active": i.is_active,
            }
            for i in intents
        ]


@router.post("/api/v1/intents")
async def create_intent(data: IntentCreate) -> dict:
    """Add a new intent keyword."""
    async with async_session() as session:
        intent = IntentKeyword(
            action=data.action,
            keyword=data.keyword.lower().strip(),
            response=data.response,
            is_active=True,
        )
        session.add(intent)
        await session.commit()
        await session.refresh(intent)
        # Invalidate cache
        from farmafacil.services.intent import _load_keyword_cache
        await _load_keyword_cache()
        return {"id": intent.id, "action": intent.action, "keyword": intent.keyword}


@router.get("/api/v1/stats")
async def get_stats(phone: str | None = None) -> dict:
    """Usage statistics — global totals or per-user breakdown.

    Args:
        phone: Optional phone number to get per-user stats.

    Returns:
        Dict with questions, tokens, and success counts.
    """
    async with async_session() as session:
        if phone:
            result = await session.execute(
                select(User).where(User.phone_number == phone)
            )
            user = result.scalar_one_or_none()
            if not user:
                return {"error": "user not found"}
            from farmafacil.services.chat_debug import get_user_stats

            stats = await get_user_stats(phone, user.id)
            return {"phone": phone, "name": user.name, **stats}

        # Global stats
        total_users = (
            await session.execute(select(func.count(User.id)))
        ).scalar() or 0
        total_questions = (
            await session.execute(
                select(func.count(ConversationLog.id)).where(
                    ConversationLog.direction == "inbound"
                )
            )
        ).scalar() or 0
        total_success = (
            await session.execute(
                select(func.count(SearchLog.id)).where(
                    SearchLog.feedback == "yes"
                )
            )
        ).scalar() or 0
        tokens = (
            await session.execute(
                select(
                    func.coalesce(func.sum(User.total_tokens_in), 0),
                    func.coalesce(func.sum(User.total_tokens_out), 0),
                    func.coalesce(func.sum(User.tokens_in_haiku), 0),
                    func.coalesce(func.sum(User.tokens_out_haiku), 0),
                    func.coalesce(func.sum(User.calls_haiku), 0),
                    func.coalesce(func.sum(User.tokens_in_sonnet), 0),
                    func.coalesce(func.sum(User.tokens_out_sonnet), 0),
                    func.coalesce(func.sum(User.calls_sonnet), 0),
                )
            )
        ).one()

        from farmafacil.services.chat_debug import estimate_cost

        cost_haiku = estimate_cost(tokens[2], tokens[3], "haiku")
        cost_sonnet = estimate_cost(tokens[5], tokens[6], "sonnet")

        return {
            "total_users": total_users,
            "total_questions": total_questions,
            "total_success": total_success,
            "total_tokens_in": tokens[0],
            "total_tokens_out": tokens[1],
            "haiku": {
                "tokens_in": tokens[2],
                "tokens_out": tokens[3],
                "calls": tokens[4],
                "est_cost_usd": round(cost_haiku, 4),
            },
            "sonnet": {
                "tokens_in": tokens[5],
                "tokens_out": tokens[6],
                "calls": tokens[7],
                "est_cost_usd": round(cost_sonnet, 4),
            },
            "est_cost_total_usd": round(cost_haiku + cost_sonnet, 4),
        }


@router.delete("/api/v1/intents/{intent_id}")
async def delete_intent(intent_id: int) -> dict:
    """Deactivate an intent keyword."""
    async with async_session() as session:
        result = await session.execute(
            select(IntentKeyword).where(IntentKeyword.id == intent_id)
        )
        intent = result.scalar_one_or_none()
        if not intent:
            return {"error": "not found"}
        intent.is_active = False
        await session.commit()
        from farmafacil.services.intent import _load_keyword_cache
        await _load_keyword_cache()
        return {"id": intent.id, "deactivated": True}
