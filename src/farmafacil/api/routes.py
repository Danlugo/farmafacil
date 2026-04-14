"""API route definitions."""

import csv
import io
from html import escape

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
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
                    func.coalesce(func.sum(User.tokens_in_admin), 0),
                    func.coalesce(func.sum(User.tokens_out_admin), 0),
                    func.coalesce(func.sum(User.calls_admin), 0),
                )
            )
        ).one()

        from farmafacil.services.chat_debug import estimate_cost

        cost_haiku = estimate_cost(tokens[2], tokens[3], "haiku")
        cost_sonnet = estimate_cost(tokens[5], tokens[6], "sonnet")
        cost_admin = estimate_cost(tokens[8], tokens[9], "opus")

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
            "admin": {
                "tokens_in": tokens[8],
                "tokens_out": tokens[9],
                "calls": tokens[10],
                "est_cost_usd": round(cost_admin, 4),
            },
            "est_cost_total_usd": round(
                cost_haiku + cost_sonnet + cost_admin, 4
            ),
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
            <div class="card">
                <div class="label">Admin (Opus)</div>
                <div class="value">{stats.get('calls_admin', 0)}</div>
                <div class="sub">{stats.get('tokens_in_admin', 0):,} in / {stats.get('tokens_out_admin', 0):,} out</div>
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
            <div class="card">
                <div class="label">Admin Cost</div>
                <div class="value cost">${costs.get('cost_admin', 0.0):.4f}</div>
                <div class="sub">$15.00 / $75.00 per MTok (Opus)</div>
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


# ── Scheduled Tasks API ───────────────────────────────────────────────


@router.get("/api/v1/scheduled-tasks")
@limiter.limit("60/minute")
async def list_scheduled_tasks(request: Request) -> list[dict]:
    """List all scheduled tasks with their status."""
    from farmafacil.models.database import ScheduledTask

    async with async_session() as session:
        result = await session.execute(
            select(ScheduledTask).order_by(ScheduledTask.id)
        )
        tasks = result.scalars().all()

    return [
        {
            "id": t.id,
            "name": t.name,
            "task_key": t.task_key,
            "interval_minutes": t.interval_minutes,
            "enabled": t.enabled,
            "status": t.status,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
            "last_result": t.last_result,
            "last_duration_seconds": t.last_duration_seconds,
        }
        for t in tasks
    ]


@router.post("/api/v1/scheduled-tasks/{task_id}/run")
@limiter.limit("10/minute")
async def run_scheduled_task(request: Request, task_id: int) -> dict:
    """Manually trigger a scheduled task."""
    from farmafacil.services.scheduler import run_task_now

    result = await run_task_now(task_id)
    return {"task_id": task_id, "result": result}


# ── Conversation Session Viewer + CSV/DOCX Export ────────────────────

# A "session" is a continuous conversation — messages separated by
# gaps larger than this are considered separate sessions.
SESSION_GAP_MINUTES = 30


def _group_into_sessions(messages: list) -> list[dict]:
    """Group ordered messages into sessions by time gap.

    Returns:
        List of dicts: {start, end, messages: [...], count}
    """
    from datetime import timedelta

    if not messages:
        return []

    sessions = []
    current: list = [messages[0]]

    for i in range(1, len(messages)):
        prev_ts = messages[i - 1].created_at
        curr_ts = messages[i].created_at
        if prev_ts and curr_ts:
            gap = curr_ts - prev_ts
            if gap > timedelta(minutes=SESSION_GAP_MINUTES):
                sessions.append({
                    "start": current[0].created_at,
                    "end": current[-1].created_at,
                    "messages": current,
                    "count": len(current),
                })
                current = []
        current.append(messages[i])

    if current:
        sessions.append({
            "start": current[0].created_at,
            "end": current[-1].created_at,
            "messages": current,
            "count": len(current),
        })

    return sessions


@router.get("/admin/conversations", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def admin_conversations_list(request: Request) -> HTMLResponse:
    """Render a list of users with links to their conversation sessions."""
    async with async_session() as session:
        result = await session.execute(
            select(
                ConversationLog.phone_number,
                func.count(ConversationLog.id).label("msg_count"),
                func.max(ConversationLog.created_at).label("last_msg"),
            )
            .group_by(ConversationLog.phone_number)
            .order_by(func.max(ConversationLog.created_at).desc())
        )
        phones = result.all()

        user_result = await session.execute(select(User))
        users_by_phone = {
            u.phone_number: u.name or "(sin nombre)"
            for u in user_result.scalars().all()
        }

    rows = []
    for phone, count, last_msg in phones:
        name = users_by_phone.get(phone, "(desconocido)")
        safe_phone = escape(phone)
        safe_name = escape(name)
        last_str = last_msg.strftime("%Y-%m-%d %H:%M") if last_msg else ""
        rows.append(
            f'<tr>'
            f'<td><a href="/admin/conversations/{safe_phone}">{safe_name}</a></td>'
            f'<td>{safe_phone}</td><td>{count}</td><td>{last_str}</td>'
            f'<td>'
            f'<a href="/api/v1/conversations/export?phone={safe_phone}&format=csv">CSV</a> · '
            f'<a href="/api/v1/conversations/export?phone={safe_phone}&format=docx">Word</a>'
            f'</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html><head><title>Conversations</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em;}}
h1{{color:#1a73e8;}}
table{{width:100%;border-collapse:collapse;margin-top:1em;}}
th,td{{padding:.5em;text-align:left;border-bottom:1px solid #ddd;}}
th{{background:#f5f5f5;}}
a{{color:#1a73e8;text-decoration:none;}}
a:hover{{text-decoration:underline;}}
.nav{{margin-bottom:1em;}}
.nav a{{margin-right:1em;}}
</style></head><body>
<div class="nav">
  <a href="/admin">← Admin</a>
  <a href="/api/v1/conversations/export?format=csv">Export all (CSV)</a>
</div>
<h1>Conversations</h1>
<p>{len(rows)} users with conversation history. Click a name to see sessions.</p>
<table>
<tr><th>Name</th><th>Phone</th><th>Messages</th><th>Last</th><th>Export</th></tr>
{"".join(rows)}
</table>
</body></html>"""
    return HTMLResponse(html)


@router.get("/admin/conversations/{phone}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def admin_conversation_sessions(request: Request, phone: str) -> HTMLResponse:
    """List all sessions for a user, each as a collapsible thread."""
    safe_phone = escape(phone)

    async with async_session() as session:
        user_result = await session.execute(
            select(User).where(User.phone_number == phone)
        )
        user = user_result.scalar_one_or_none()
        user_name = (user.name if user else None) or "(sin nombre)"

        msg_result = await session.execute(
            select(ConversationLog)
            .where(ConversationLog.phone_number == phone)
            .order_by(ConversationLog.created_at.asc())
        )
        messages = msg_result.scalars().all()

    sessions = _group_into_sessions(messages)

    session_html = []
    for idx, sess in enumerate(reversed(sessions), 1):  # newest first
        session_num = len(sessions) - idx + 1
        start = sess["start"].strftime("%Y-%m-%d %H:%M") if sess["start"] else "?"
        end = sess["end"].strftime("%H:%M") if sess["end"] else "?"
        duration_min = 0
        if sess["start"] and sess["end"]:
            duration_min = round((sess["end"] - sess["start"]).total_seconds() / 60)

        bubbles = []
        for msg in sess["messages"]:
            ts = msg.created_at.strftime("%H:%M:%S") if msg.created_at else ""
            is_inbound = msg.direction == "inbound"
            bubble_class = "inbound" if is_inbound else "outbound"
            label = "👤" if is_inbound else "🤖"
            if msg.message_type == "admin_out":
                label = "🛠️"
                bubble_class = "admin"
            text = escape(msg.message_text or "").replace("\n", "<br>")
            bubbles.append(
                f'<div class="msg {bubble_class}">'
                f'<div class="meta">{label} {ts}</div>'
                f'<div class="text">{text}</div>'
                f'</div>'
            )

        # Session ID is the start timestamp for export link
        session_ts = sess["start"].strftime("%Y%m%d_%H%M%S") if sess["start"] else f"session_{session_num}"
        session_iso = sess["start"].isoformat() if sess["start"] else ""

        session_html.append(f"""
<details {'open' if idx == 1 else ''} class="session">
<summary>
  <strong>Sesión {session_num}</strong> — {start} → {end}
  ({sess["count"]} mensajes, {duration_min} min)
  <span class="export-links">
    <a href="/api/v1/conversations/export?phone={safe_phone}&session={session_iso}&format=csv" onclick="event.stopPropagation()">CSV</a> ·
    <a href="/api/v1/conversations/export?phone={safe_phone}&session={session_iso}&format=docx" onclick="event.stopPropagation()">Word</a>
  </span>
</summary>
<div class="bubbles">
{"".join(bubbles)}
</div>
</details>
""")

    html = f"""<!DOCTYPE html>
<html><head><title>Sesiones — {escape(user_name)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;padding:0 1em;background:#f0f0f0;}}
h1{{color:#1a73e8;}}
.nav{{margin-bottom:1em;}}
.nav a{{margin-right:1em;color:#1a73e8;text-decoration:none;}}
.summary-box{{background:#fff;padding:1em;border-radius:8px;margin-bottom:1em;}}
.session{{background:#fff;border-radius:8px;margin:1em 0;padding:0;}}
.session summary{{padding:1em;cursor:pointer;font-size:1em;border-radius:8px;}}
.session summary:hover{{background:#f5f5f5;}}
.session[open] summary{{border-bottom:1px solid #ddd;border-radius:8px 8px 0 0;}}
.export-links{{float:right;font-size:.85em;}}
.export-links a{{color:#1a73e8;margin:0 .3em;}}
.bubbles{{padding:1em;}}
.msg{{margin:.6em 0;padding:.7em 1em;border-radius:12px;max-width:80%;word-wrap:break-word;}}
.msg.inbound{{background:#e8f0fe;border:1px solid #bbd0f7;margin-right:auto;}}
.msg.outbound{{background:#d4edda;border:1px solid #c3e6cb;margin-left:auto;}}
.msg.admin{{background:#fff3cd;border:1px solid #ffeaa7;margin-left:auto;}}
.meta{{font-size:.7em;color:#666;margin-bottom:.2em;}}
.text{{font-size:.95em;white-space:pre-wrap;}}
</style></head><body>
<div class="nav">
  <a href="/admin/conversations">← All users</a>
  <a href="/api/v1/conversations/export?phone={safe_phone}&format=csv">Export all sessions (CSV)</a>
  <a href="/api/v1/conversations/export?phone={safe_phone}&format=docx">Export all (Word)</a>
</div>
<div class="summary-box">
  <h1>{escape(user_name)}</h1>
  <p>Phone: <code>{safe_phone}</code> • {len(messages)} total messages • {len(sessions)} session(s)</p>
  <p style="color:#666;font-size:.85em;">Sessions group messages within {SESSION_GAP_MINUTES} minutes of each other.</p>
</div>
{"".join(session_html)}
</body></html>"""
    return HTMLResponse(html)


def _filter_session_messages(messages: list, session_iso: str) -> list:
    """Filter messages to the specific session starting at the given timestamp."""
    from datetime import datetime, timedelta

    try:
        target_start = datetime.fromisoformat(session_iso)
    except ValueError:
        return messages

    # Find the session that starts at or near target_start
    sessions = _group_into_sessions(messages)
    for sess in sessions:
        if sess["start"] and abs((sess["start"] - target_start).total_seconds()) < 2:
            return sess["messages"]

    return []


@router.get("/api/v1/conversations/export")
@limiter.limit("10/minute")
async def export_conversations(
    request: Request,
    phone: str | None = Query(None),
    session: str | None = Query(None, description="ISO timestamp of session start"),
    format: str = Query("csv", pattern="^(csv|docx)$"),
) -> StreamingResponse:
    """Export conversations as CSV or Word document.

    Args:
        phone: Optional — filter to a single user.
        session: Optional — ISO timestamp of a specific session's start.
        format: 'csv' or 'docx'.
    """
    async with async_session() as session_db:
        stmt = select(ConversationLog)
        if phone:
            stmt = stmt.where(ConversationLog.phone_number == phone)
        stmt = stmt.order_by(ConversationLog.created_at.asc())
        result = await session_db.execute(stmt)
        messages = result.scalars().all()

        user_result = await session_db.execute(select(User))
        users_by_phone = {
            u.phone_number: u.name or "" for u in user_result.scalars().all()
        }

    if session:
        messages = _filter_session_messages(messages, session)

    if format == "docx":
        return _export_as_docx(messages, users_by_phone, phone, session)

    return _export_as_csv(messages, users_by_phone, phone, session)


def _export_as_csv(messages, users_by_phone, phone, session_iso):
    """Produce a CSV export."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "id", "timestamp", "phone", "name", "direction",
        "message_type", "message_text", "wa_message_id",
    ])
    for msg in messages:
        writer.writerow([
            msg.id,
            msg.created_at.isoformat() if msg.created_at else "",
            msg.phone_number,
            users_by_phone.get(msg.phone_number, ""),
            msg.direction,
            msg.message_type,
            msg.message_text or "",
            msg.wa_message_id or "",
        ])

    buffer.seek(0)
    suffix = f"_{phone}" if phone else "_all"
    if session_iso:
        suffix += f"_session_{session_iso.replace(':', '').replace('-', '')[:15]}"
    filename = f"conversations{suffix}.csv"

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_as_docx(messages, users_by_phone, phone, session_iso):
    """Produce a Word document export with chat-transcript styling."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    title = "FarmaFacil Conversation"
    if phone:
        name = users_by_phone.get(phone, "")
        title += f" — {name} ({phone})" if name else f" — {phone}"
    doc.add_heading(title, level=1)

    if session_iso:
        doc.add_paragraph(f"Session: {session_iso}").italic = True

    doc.add_paragraph(f"Total messages: {len(messages)}").italic = True
    doc.add_paragraph("")

    for msg in messages:
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else ""
        is_inbound = msg.direction == "inbound"
        name = users_by_phone.get(msg.phone_number, "")

        if msg.message_type == "admin_out":
            label = "🛠️ Admin AI"
            color = RGBColor(0xB3, 0x7F, 0x00)  # amber
        elif is_inbound:
            label = f"👤 {name or msg.phone_number}"
            color = RGBColor(0x1A, 0x73, 0xE8)  # blue
        else:
            label = "🤖 FarmaFacil Bot"
            color = RGBColor(0x2E, 0x7D, 0x32)  # green

        # Header paragraph
        header = doc.add_paragraph()
        header_run = header.add_run(f"{label} — {ts}")
        header_run.bold = True
        header_run.font.color.rgb = color
        header_run.font.size = Pt(9)

        # Message body
        body = doc.add_paragraph(msg.message_text or "")
        for run in body.runs:
            run.font.size = Pt(11)

        doc.add_paragraph("")  # spacer

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    suffix = f"_{phone}" if phone else "_all"
    if session_iso:
        suffix += f"_session_{session_iso.replace(':', '').replace('-', '')[:15]}"
    filename = f"conversations{suffix}.docx"

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
