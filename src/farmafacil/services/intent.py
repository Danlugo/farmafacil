"""Intent detection — DB keywords first, AI responder fallback for complex messages.

Flow:
1. Check DB keyword table for exact match (cached, instant)
2. If message looks like a drug name (short, no question marks) — treat as drug search
3. If ambiguous or conversational — delegate to AI responder (classify_with_ai)
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from farmafacil.db.session import async_session
from farmafacil.models.database import IntentKeyword

logger = logging.getLogger(__name__)

# ── In-memory cache for DB keywords (refreshed every 5 minutes) ────────

_keyword_cache: dict[str, tuple[str, str | None]] = {}  # keyword → (action, response)
_cache_loaded_at: float = 0
CACHE_TTL_SECONDS = 300  # 5 minutes


async def _load_keyword_cache() -> None:
    """Load all active keywords from DB into memory cache."""
    global _keyword_cache, _cache_loaded_at
    async with async_session() as session:
        result = await session.execute(
            select(IntentKeyword).where(IntentKeyword.is_active.is_(True))
        )
        keywords = result.scalars().all()
        _keyword_cache = {
            kw.keyword.lower(): (kw.action, kw.response) for kw in keywords
        }
        _cache_loaded_at = time.time()
        logger.debug("Loaded %d intent keywords into cache", len(_keyword_cache))


async def _get_keyword_cache() -> dict[str, tuple[str, str | None]]:
    """Get the keyword cache, refreshing if stale."""
    if time.time() - _cache_loaded_at > CACHE_TTL_SECONDS:
        await _load_keyword_cache()
    return _keyword_cache


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class Intent:
    """Classified user intent with extracted profile data."""

    action: str  # greeting, help, location_change, preference_change, name_change, farewell, drug_search, question, unknown
    drug_query: str | None = None
    response_text: str | None = None
    detected_name: str | None = None
    detected_location: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


HELP_MESSAGE = (
    "\U0001f48a *FarmaFacil — Ayuda*\n\n"
    "Puedo ayudarte a encontrar productos en farmacias de Venezuela: "
    "medicamentos, vitaminas, skincare, cuidado personal, y mas.\n\n"
    "*Buscar producto:*\n"
    "\u2022 Envia el nombre (ej: _losartan_, _protector solar_)\n"
    "\u2022 O describe lo que necesitas (ej: _algo para el dolor de cabeza_)\n\n"
    "*Configuracion:*\n"
    "\u2022 _cambiar zona_ — nueva ubicacion\n"
    "\u2022 _cambiar preferencia_ — modo de visualizacion\n"
    "\u2022 _cambiar nombre_ — actualizar tu nombre\n\n"
    "*Ejemplos:*\n"
    "\u2022 _losartan_\n"
    "\u2022 _acetaminofen 500mg_\n"
    "\u2022 _protector solar_\n"
    "\u2022 _necesito algo para la gripe_"
)


async def classify_intent_keywords(text: str) -> Intent | None:
    """Try to classify intent using DB keyword matching.

    Args:
        text: User message text (already stripped).

    Returns:
        Intent if classified, None if ambiguous (needs LLM).
    """
    text_lower = text.lower().strip()
    cache = await _get_keyword_cache()

    # Exact match in DB keywords
    if text_lower in cache:
        action, response = cache[text_lower]
        return Intent(action=action, response_text=response)

    # Short message (1-4 words), no question marks — likely a drug name
    words = text_lower.split()
    is_question = "?" in text or text_lower.startswith((
        "como ", "donde ", "que ", "cual ", "cuando ", "por que ",
        "cuanto ", "tienen ", "hay ", "puedo ", "puedes ",
    ))

    if len(words) <= 4 and not is_question:
        return Intent(action="drug_search", drug_query=text.strip())

    # Longer text without question markers — still likely a drug search
    if not is_question and len(words) <= 8:
        return Intent(action="drug_search", drug_query=text.strip())

    # Ambiguous — needs LLM
    return None


async def classify_intent_ai(text: str, user_id: int, user_name: str) -> Intent:
    """Use AI responder to classify intent and extract profile data.

    Delegates to the role-based AI system which loads its system prompt
    from the database (editable via admin UI).

    Args:
        text: User message text.
        user_id: The user's database ID.
        user_name: The user's display name.

    Returns:
        Classified intent with optional drug name, name, location, or response.
    """
    from farmafacil.services.ai_responder import classify_with_ai

    ai_result = await classify_with_ai(text, user_id, user_name)

    return Intent(
        action=ai_result.action,
        drug_query=ai_result.drug_query,
        response_text=ai_result.text if ai_result.text else None,
        detected_name=ai_result.detected_name,
        detected_location=ai_result.detected_location,
        input_tokens=ai_result.input_tokens,
        output_tokens=ai_result.output_tokens,
    )


async def classify_intent(text: str, user_id: int = 0, user_name: str = "") -> Intent:
    """Classify user intent — DB keywords first, AI fallback.

    Args:
        text: User message text.
        user_id: The user's database ID (for AI fallback).
        user_name: The user's display name (for AI fallback).

    Returns:
        Classified Intent.
    """
    # Try DB keyword detection first (cached, instant, free)
    intent = await classify_intent_keywords(text)
    if intent is not None:
        logger.debug("Keyword intent: %s for '%s'", intent.action, text[:50])
        return intent

    # Ambiguous — use AI responder
    logger.info("Keyword detection inconclusive for '%s' — calling AI", text[:50])
    return await classify_intent_ai(text, user_id, user_name)
