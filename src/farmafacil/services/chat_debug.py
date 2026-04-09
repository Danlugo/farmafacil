"""Chat debug service — builds debug footer for bot responses."""

import logging

from sqlalchemy import func, select

from farmafacil import __version__
from farmafacil.config import LLM_MODEL
from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, SearchLog, User

logger = logging.getLogger(__name__)

# ── Token cost rates (USD per million tokens) ─────────────────────────
# Source: https://docs.anthropic.com/en/docs/about-claude/pricing
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # (input_cost_per_mtok, output_cost_per_mtok)
    "haiku": (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
}

# Default pricing when model is unknown (use Haiku as baseline)
DEFAULT_PRICING = MODEL_PRICING["haiku"]


def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = "haiku",
) -> float:
    """Estimate USD cost for a given token count and model.

    Args:
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        model: Model family key ("haiku", "sonnet", "opus") or full model name.

    Returns:
        Estimated cost in USD.
    """
    # Resolve model family from full name if needed
    model_lower = model.lower()
    for family in MODEL_PRICING:
        if family in model_lower:
            pricing = MODEL_PRICING[family]
            break
    else:
        pricing = DEFAULT_PRICING

    return (
        (input_tokens / 1_000_000) * pricing[0]
        + (output_tokens / 1_000_000) * pricing[1]
    )


def estimate_cost_breakdown(stats: dict) -> dict[str, float]:
    """Calculate per-model and total costs from stats dict.

    Args:
        stats: Stats dict from get_user_stats() containing per-model token counts.

    Returns:
        Dict with cost_haiku, cost_sonnet, cost_total.
    """
    cost_haiku = estimate_cost(
        stats.get("tokens_in_haiku", 0),
        stats.get("tokens_out_haiku", 0),
        "haiku",
    )
    cost_sonnet = estimate_cost(
        stats.get("tokens_in_sonnet", 0),
        stats.get("tokens_out_sonnet", 0),
        "sonnet",
    )
    return {
        "cost_haiku": cost_haiku,
        "cost_sonnet": cost_sonnet,
        "cost_total": cost_haiku + cost_sonnet,
    }


async def get_user_stats(phone_number: str, user_id: int) -> dict[str, int]:
    """Get aggregate stats for a user.

    Both params are needed because ConversationLog is keyed by phone,
    while SearchLog is keyed by user_id.

    Args:
        phone_number: WhatsApp phone number (for conversation_logs).
        user_id: User database ID (for search_logs).

    Returns:
        Dict with total_questions and total_success counts.
    """
    async with async_session() as session:
        # Total inbound messages from this user
        q_count = await session.execute(
            select(func.count(ConversationLog.id)).where(
                ConversationLog.phone_number == phone_number,
                ConversationLog.direction == "inbound",
            )
        )
        total_questions = q_count.scalar() or 0

        # Total positive feedback from this user
        s_count = await session.execute(
            select(func.count(SearchLog.id)).where(
                SearchLog.user_id == user_id,
                SearchLog.feedback == "yes",
            )
        )
        total_success = s_count.scalar() or 0

        # Cumulative + last call + per-model token totals from user record
        user_result = await session.execute(
            select(
                User.total_tokens_in, User.total_tokens_out,
                User.last_tokens_in, User.last_tokens_out,
                User.tokens_in_haiku, User.tokens_out_haiku, User.calls_haiku,
                User.tokens_in_sonnet, User.tokens_out_sonnet, User.calls_sonnet,
            ).where(User.id == user_id)
        )
        row = user_result.one_or_none()

        # Global token totals across all users
        global_result = await session.execute(
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
        g = global_result.one()

    return {
        "total_questions": total_questions,
        "total_success": total_success,
        # Aggregate totals
        "total_tokens_in": row.total_tokens_in if row else 0,
        "total_tokens_out": row.total_tokens_out if row else 0,
        "last_tokens_in": row.last_tokens_in if row else 0,
        "last_tokens_out": row.last_tokens_out if row else 0,
        # Per-model — user
        "tokens_in_haiku": row.tokens_in_haiku if row else 0,
        "tokens_out_haiku": row.tokens_out_haiku if row else 0,
        "calls_haiku": row.calls_haiku if row else 0,
        "tokens_in_sonnet": row.tokens_in_sonnet if row else 0,
        "tokens_out_sonnet": row.tokens_out_sonnet if row else 0,
        "calls_sonnet": row.calls_sonnet if row else 0,
        # Global totals
        "global_tokens_in": g[0],
        "global_tokens_out": g[1],
        "global_tokens_in_haiku": g[2],
        "global_tokens_out_haiku": g[3],
        "global_calls_haiku": g[4],
        "global_tokens_in_sonnet": g[5],
        "global_tokens_out_sonnet": g[6],
        "global_calls_sonnet": g[7],
    }


def build_debug_footer(
    role_used: str,
    input_tokens: int,
    output_tokens: int,
    total_questions: int,
    total_success: int,
    total_tokens_in: int = 0,
    total_tokens_out: int = 0,
    global_tokens_in: int = 0,
    global_tokens_out: int = 0,
    model_used: str = "",
    calls_haiku: int = 0,
    calls_sonnet: int = 0,
    global_calls_haiku: int = 0,
    global_calls_sonnet: int = 0,
    global_tokens_in_haiku: int = 0,
    global_tokens_out_haiku: int = 0,
    global_tokens_in_sonnet: int = 0,
    global_tokens_out_sonnet: int = 0,
) -> str:
    """Build a debug footer string to append to bot responses.

    Args:
        role_used: AI role name that handled the message.
        input_tokens: Tokens used for input in the current LLM call.
        output_tokens: Tokens used for output in the current LLM call.
        total_questions: Total inbound messages from the user.
        total_success: Total positive feedback count.
        total_tokens_in: Cumulative input tokens for this user.
        total_tokens_out: Cumulative output tokens for this user.
        global_tokens_in: Global cumulative input tokens (all users).
        global_tokens_out: Global cumulative output tokens (all users).
        model_used: Model name used for this call.
        calls_haiku: User's Haiku API call count.
        calls_sonnet: User's Sonnet API call count.
        global_calls_haiku: Global Haiku API call count.
        global_calls_sonnet: Global Sonnet API call count.
        global_tokens_in_haiku: Global Haiku input tokens.
        global_tokens_out_haiku: Global Haiku output tokens.
        global_tokens_in_sonnet: Global Sonnet input tokens.
        global_tokens_out_sonnet: Global Sonnet output tokens.

    Returns:
        Formatted debug footer string.
    """
    # Estimate costs using actual model for this call
    call_cost = estimate_cost(input_tokens, output_tokens, model_used or "haiku")

    # Global cost uses per-model breakdown for accuracy
    global_cost_breakdown = estimate_cost_breakdown({
        "tokens_in_haiku": global_tokens_in_haiku,
        "tokens_out_haiku": global_tokens_out_haiku,
        "tokens_in_sonnet": global_tokens_in_sonnet,
        "tokens_out_sonnet": global_tokens_out_sonnet,
    })
    global_cost = global_cost_breakdown["cost_total"]

    return (
        "\n\n---\n"
        "\U0001f527 *DEBUG*\n"
        f"app version: _{__version__}_\n"
        f"ai model: _{model_used or LLM_MODEL}_\n"
        f"ai role: _{role_used}_\n"
        f"tokens: _{input_tokens} in / {output_tokens} out_\n"
        f"est cost: _${call_cost:.4f}_\n"
        f"user tokens: _{total_tokens_in} in / {total_tokens_out} out_\n"
        f"user calls: _haiku={calls_haiku} sonnet={calls_sonnet}_\n"
        f"global tokens: _{global_tokens_in} in / {global_tokens_out} out_\n"
        f"global calls: _haiku={global_calls_haiku} sonnet={global_calls_sonnet}_\n"
        f"global est cost: _${global_cost:.4f}_\n"
        f"total questions: _{total_questions}_\n"
        f"total success: _{total_success}_"
    )
