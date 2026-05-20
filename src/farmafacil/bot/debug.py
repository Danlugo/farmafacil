"""Debug footer builder for the FarmaFacil WhatsApp bot.

Extracted from handler.py (Item 72 refactor) to keep handler.py focused
on dispatch logic.  The single public function ``_build_debug`` is imported
back into handler.py so that ``farmafacil.bot.handler._build_debug`` remains
a valid mock target for existing tests.
"""

import logging

from farmafacil.services.chat_debug import build_debug_footer, get_user_stats

logger = logging.getLogger(__name__)


async def _build_debug(sender: str, user_id: int, ai_result=None) -> str:
    """Build a debug footer from AI response and user stats.

    Args:
        sender: WhatsApp phone number.
        user_id: User database ID.
        ai_result: AiResponse with role_used and token counts (optional).

    Returns:
        Formatted debug footer string.
    """
    from farmafacil.services.settings import resolve_user_model

    stats = await get_user_stats(sender, user_id)
    role = getattr(ai_result, "role_used", "keyword") if ai_result else "keyword"
    in_tok = getattr(ai_result, "input_tokens", 0) if ai_result else 0
    out_tok = getattr(ai_result, "output_tokens", 0) if ai_result else 0
    # Prefer the model actually used for THIS call (set by ai_responder /
    # intent / refine when an LLM ran). Fall back to the current default
    # so the footer never lies — previously it always showed LLM_MODEL
    # (haiku) regardless of what the admin had set as default.
    # (v0.19.2, Item 49.)
    call_model = getattr(ai_result, "model", "") if ai_result else ""
    model_for_footer = call_model or await resolve_user_model()
    return build_debug_footer(
        role_used=role,
        input_tokens=in_tok,
        output_tokens=out_tok,
        total_questions=stats["total_questions"],
        total_success=stats["total_success"],
        total_tokens_in=stats["total_tokens_in"],
        total_tokens_out=stats["total_tokens_out"],
        global_tokens_in=stats["global_tokens_in"],
        global_tokens_out=stats["global_tokens_out"],
        model_used=model_for_footer,
        calls_haiku=stats["calls_haiku"],
        calls_sonnet=stats["calls_sonnet"],
        global_calls_haiku=stats["global_calls_haiku"],
        global_calls_sonnet=stats["global_calls_sonnet"],
        global_tokens_in_haiku=stats["global_tokens_in_haiku"],
        global_tokens_out_haiku=stats["global_tokens_out_haiku"],
        global_tokens_in_sonnet=stats["global_tokens_in_sonnet"],
        global_tokens_out_sonnet=stats["global_tokens_out_sonnet"],
    )
