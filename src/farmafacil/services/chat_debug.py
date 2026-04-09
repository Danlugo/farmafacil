"""Chat debug service — builds debug footer for bot responses."""

import logging

from sqlalchemy import func, select

from farmafacil import __version__
from farmafacil.config import LLM_MODEL
from farmafacil.db.session import async_session
from farmafacil.models.database import ConversationLog, SearchLog, User

logger = logging.getLogger(__name__)

# ── Token cost rates (USD per million tokens) ─────────────────────────
# Claude Haiku 4.5: $1.00/MTok input, $5.00/MTok output
# Source: https://docs.anthropic.com/en/docs/about-claude/pricing
COST_PER_MTOK_INPUT = 1.00
COST_PER_MTOK_OUTPUT = 5.00


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a given token count.

    Args:
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Estimated cost in USD.
    """
    return (
        (input_tokens / 1_000_000) * COST_PER_MTOK_INPUT
        + (output_tokens / 1_000_000) * COST_PER_MTOK_OUTPUT
    )


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

        # Cumulative token totals from user record
        user_result = await session.execute(
            select(User.total_tokens_in, User.total_tokens_out).where(
                User.id == user_id
            )
        )
        row = user_result.one_or_none()
        total_tokens_in = row.total_tokens_in if row else 0
        total_tokens_out = row.total_tokens_out if row else 0

        # Global token totals across all users
        global_result = await session.execute(
            select(
                func.coalesce(func.sum(User.total_tokens_in), 0),
                func.coalesce(func.sum(User.total_tokens_out), 0),
            )
        )
        global_row = global_result.one()
        global_tokens_in = global_row[0]
        global_tokens_out = global_row[1]

    return {
        "total_questions": total_questions,
        "total_success": total_success,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "global_tokens_in": global_tokens_in,
        "global_tokens_out": global_tokens_out,
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

    Returns:
        Formatted debug footer string.
    """
    # Estimate costs
    call_cost = estimate_cost(input_tokens, output_tokens)
    global_cost = estimate_cost(global_tokens_in, global_tokens_out)

    return (
        "\n\n---\n"
        "\U0001f527 *DEBUG*\n"
        f"app version: _{__version__}_\n"
        f"ai model: _{LLM_MODEL}_\n"
        f"ai role: _{role_used}_\n"
        f"tokens: _{input_tokens} in / {output_tokens} out_\n"
        f"est cost: _${call_cost:.4f}_\n"
        f"user tokens: _{total_tokens_in} in / {total_tokens_out} out_\n"
        f"global tokens: _{global_tokens_in} in / {global_tokens_out} out_\n"
        f"global est cost: _${global_cost:.4f}_\n"
        f"total questions: _{total_questions}_\n"
        f"total success: _{total_success}_"
    )
