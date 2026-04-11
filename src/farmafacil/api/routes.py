"""API route definitions."""

from html import escape

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from farmafacil import __version__
from farmafacil.api.limiter import limiter
from farmafacil.db.session import async_session
from pydantic import BaseModel, Field

from farmafacil.models.database import ConversationLog, IntentKeyword, SearchLog, User
from farmafacil.models.schemas import HealthResponse, SearchRequest, SearchResponse
from farmafacil.services.search import search_drug

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(version=__version__)


@router.post("/api/v1/search", response_model=SearchResponse)
@limiter.limit("30/minute")
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    """Search for a drug across all pharmacies."""
    return await search_drug(body.query, city=body.city)


@router.get("/api/v1/search", response_model=SearchResponse)
@limiter.limit("30/minute")
async def search_get(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    city: str | None = Query(None, max_length=50),
) -> SearchResponse:
    """Search for a drug via GET (convenience for WhatsApp bot / browser)."""
    return await search_drug(q, city=city)


@router.get("/api/v1/conversations")
@limiter.limit("60/minute")
async def get_conversations(
    request: Request,
    phone: str | None = Query(None, max_length=30),
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
@limiter.limit("60/minute")
async def get_users(
    request: Request,
    limit: int = Query(50, le=200),
) -> list[dict]:
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
    action: str = Field(..., min_length=1, max_length=50)
    keyword: str = Field(..., min_length=1, max_length=100)
    response: str | None = Field(None, max_length=2000)


@router.get("/api/v1/intents")
@limiter.limit("30/minute")
async def get_intents(
    request: Request,
    action: str | None = Query(None, max_length=50),
) -> list[dict]:
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
@limiter.limit("30/minute")
async def create_intent(request: Request, data: IntentCreate) -> dict:
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
@limiter.limit("60/minute")
async def get_stats(
    request: Request,
    phone: str | None = Query(None, max_length=30),
) -> dict:
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
@limiter.limit("30/minute")
async def delete_intent(request: Request, intent_id: int) -> dict:
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


# ── Admin User Stats Page ──────────────────────────────────────────────


@router.get("/admin/user-stats/{user_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def admin_user_stats(request: Request, user_id: int) -> HTMLResponse:
    """Render an HTML stats dashboard for a single user.

    Args:
        user_id: Database ID of the user.

    Returns:
        HTML page with usage stats, cost estimates, and activity metrics.
    """
    from farmafacil.services.chat_debug import estimate_cost_breakdown, get_user_stats

    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return HTMLResponse("<h1>User not found</h1>", status_code=404)

        stats = await get_user_stats(user.phone_number, user.id)

        # Search counts
        total_searches = (
            await session.execute(
                select(func.count(SearchLog.id)).where(SearchLog.user_id == user_id)
            )
        ).scalar() or 0
        successful_searches = stats["total_success"]

        # Recent searches
        recent = await session.execute(
            select(SearchLog.query, SearchLog.results_count, SearchLog.feedback, SearchLog.searched_at)
            .where(SearchLog.user_id == user_id)
            .order_by(SearchLog.searched_at.desc())
            .limit(10)
        )
        recent_searches = recent.all()

    costs = estimate_cost_breakdown(stats)
    success_rate = (successful_searches / total_searches * 100) if total_searches > 0 else 0

    # All user-sourced values are HTML-escaped to prevent stored XSS
    # (names and search queries come from WhatsApp and must not be trusted).
    safe_name = escape(user.name or "Unknown")
    safe_phone = escape(user.phone_number or "")
    safe_zone = escape(user.zone_name or "—")
    safe_city = escape(user.city_code or "—")
    safe_title_name = escape(user.name or user.phone_number or "")

    # Build HTML
    searches_html = ""
    for s in recent_searches:
        fb = s.feedback or "—"
        ts = s.searched_at.strftime("%Y-%m-%d %H:%M") if s.searched_at else "—"
        fb_class = "success" if fb == "yes" else ("danger" if fb == "no" else "")
        searches_html += (
            f"<tr><td>{escape(s.query or '')}</td><td>{s.results_count}</td>"
            f'<td class="{fb_class}">{escape(fb)}</td><td>{ts}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Stats — {safe_title_name}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 900px; margin: 40px auto; padding: 0 20px; color: #333; }}
        h1 {{ color: #1a73e8; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 24px 0; }}
        .card {{ background: #f8f9fa; border-radius: 8px; padding: 20px; border: 1px solid #e0e0e0; }}
        .card .label {{ font-size: 13px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .card .value {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
        .card .sub {{ font-size: 12px; color: #888; margin-top: 4px; }}
        .cost {{ color: #1a73e8; }}
        .success {{ color: #0d904f; }}
        .danger {{ color: #d93025; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e0e0e0; }}
        th {{ background: #f1f3f4; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; }}
        a.back {{ display: inline-block; margin-bottom: 16px; color: #1a73e8; text-decoration: none; }}
        a.back:hover {{ text-decoration: underline; }}
        .section {{ margin-top: 32px; }}
        .section h2 {{ font-size: 18px; color: #444; border-bottom: 2px solid #1a73e8; padding-bottom: 8px; }}
    </style>
</head>
<body>
    <a class="back" href="/admin/user/details/{user_id}">&larr; Back to User</a>
    <h1>{safe_name} &mdash; Usage Stats</h1>
    <p>Phone: {safe_phone} &bull; Zone: {safe_zone} &bull; City: {safe_city}</p>

    <div class="section">
        <h2>Activity</h2>
        <div class="grid">
            <div class="card">
                <div class="label">Questions</div>
                <div class="value">{stats['total_questions']}</div>
                <div class="sub">Total inbound messages</div>
            </div>
            <div class="card">
                <div class="label">Searches</div>
                <div class="value">{total_searches}</div>
                <div class="sub">{successful_searches} successful ({success_rate:.0f}%)</div>
            </div>
            <div class="card">
                <div class="label">Success Rate</div>
                <div class="value {'success' if success_rate >= 50 else 'danger'}">{success_rate:.0f}%</div>
                <div class="sub">Positive feedback / total searches</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Token Usage</h2>
        <div class="grid">
            <div class="card">
                <div class="label">Total Tokens</div>
                <div class="value">{stats['total_tokens_in'] + stats['total_tokens_out']:,}</div>
                <div class="sub">{stats['total_tokens_in']:,} in / {stats['total_tokens_out']:,} out</div>
            </div>
            <div class="card">
                <div class="label">Haiku</div>
                <div class="value">{stats['calls_haiku']}</div>
                <div class="sub">{stats['tokens_in_haiku']:,} in / {stats['tokens_out_haiku']:,} out</div>
            </div>
            <div class="card">
                <div class="label">Sonnet</div>
                <div class="value">{stats['calls_sonnet']}</div>
                <div class="sub">{stats['tokens_in_sonnet']:,} in / {stats['tokens_out_sonnet']:,} out</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Estimated Cost</h2>
        <div class="grid">
            <div class="card">
                <div class="label">Total Cost</div>
                <div class="value cost">${costs['cost_total']:.4f}</div>
                <div class="sub">All models combined</div>
            </div>
            <div class="card">
                <div class="label">Haiku Cost</div>
                <div class="value cost">${costs['cost_haiku']:.4f}</div>
                <div class="sub">$1.00 / $5.00 per MTok</div>
            </div>
            <div class="card">
                <div class="label">Sonnet Cost</div>
                <div class="value cost">${costs['cost_sonnet']:.4f}</div>
                <div class="sub">$3.00 / $15.00 per MTok</div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Recent Searches</h2>
        <table>
            <thead>
                <tr><th>Query</th><th>Results</th><th>Feedback</th><th>Date</th></tr>
            </thead>
            <tbody>
                {searches_html if searches_html else '<tr><td colspan="4">No searches yet</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="section" style="margin-top:40px; padding-top:16px; border-top:1px solid #e0e0e0; color:#888; font-size:12px;">
        FarmaFacil v{__version__} &bull;
        <a href="/api/v1/stats?phone={safe_phone}" style="color:#1a73e8;">JSON API</a>
    </div>
</body>
</html>"""
    return HTMLResponse(html)
