"""Intent detection — DB keywords first, LLM fallback for complex messages.

Flow:
1. Check DB keyword table for exact match (cached, instant)
2. If message looks like a drug name (short, no question marks) — treat as drug search
3. If ambiguous or conversational — call Claude Haiku to classify + extract profile data
"""

import logging
import time
from dataclasses import dataclass

import anthropic
from sqlalchemy import select

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL
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


HELP_MESSAGE = (
    "\U0001f48a *FarmaFacil — Ayuda*\n\n"
    "Puedo ayudarte a encontrar medicamentos en farmacias de Venezuela.\n\n"
    "*Buscar medicamento:*\n"
    "\u2022 Envia el nombre (ej: _losartan_)\n"
    "\u2022 O describe lo que necesitas (ej: _algo para el dolor de cabeza_)\n\n"
    "*Configuracion:*\n"
    "\u2022 _cambiar zona_ — nueva ubicacion\n"
    "\u2022 _cambiar preferencia_ — modo de visualizacion\n"
    "\u2022 _cambiar nombre_ — actualizar tu nombre\n\n"
    "*Ejemplos:*\n"
    "\u2022 _losartan_\n"
    "\u2022 _acetaminofen 500mg_\n"
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


async def classify_intent_llm(text: str) -> Intent:
    """Use Claude Haiku to classify intent and extract profile data.

    Args:
        text: User message text.

    Returns:
        Classified intent with optional drug name, name, location, or response.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set — falling back to drug search")
        return Intent(action="drug_search", drug_query=text.strip())

    system_prompt = """Eres FarmaFacil, un asistente de WhatsApp que ayuda a personas en Venezuela a encontrar medicamentos en farmacias cercanas.

Tu personalidad: amigable, servicial, empático. Hablas español venezolano natural. Eres conciso (esto es WhatsApp).

INSTRUCCIONES: Analiza el mensaje del usuario y responde en formato estructurado. Extrae TODA la información que puedas del mensaje.

FORMATO DE RESPUESTA (usa exactamente estas líneas, omite las que no apliquen):
ACTION: [greeting|drug_search|question|unknown]
DRUG: [nombre genérico del medicamento si aplica]
NAME: [nombre de la persona si se presenta]
LOCATION: [zona/barrio/ciudad si menciona ubicación]
RESPONSE: [respuesta conversacional si es una pregunta]

EJEMPLOS:
- "Hola soy María de Chacao, busco losartan" →
  ACTION: drug_search
  DRUG: losartan
  NAME: María
  LOCATION: Chacao

- "necesito algo para el dolor de cabeza" →
  ACTION: drug_search
  DRUG: acetaminofen

- "Me llamo José" →
  ACTION: greeting
  NAME: José

- "estoy en La Boyera" →
  ACTION: greeting
  LOCATION: La Boyera

- "hola buenas tardes" →
  ACTION: greeting

- "para qué sirve el losartán?" →
  ACTION: question
  RESPONSE: El losartán es un medicamento para tratar la presión arterial alta (hipertensión). Lo recetan frecuentemente en Venezuela. Consulta con tu médico para la dosis adecuada. Si quieres, envíame "losartan" y te busco dónde está disponible.

- "me duele la garganta" →
  ACTION: drug_search
  DRUG: ibuprofeno

- "donde queda la farmacia Tepuy?" →
  ACTION: question
  RESPONSE: La farmacia Farmatodo TEPUY está ubicada en la Av. Río de Janeiro con Calle Monterrey, Urb. Las Mercedes, Caracas. Puedes buscar cualquier medicamento y te digo si está disponible allí.

REGLAS:
- Si mencionan síntomas, traduce al medicamento genérico más probable
- Si mencionan nombre y medicamento en el mismo mensaje, extrae ambos
- Si preguntan sobre salud, responde brevemente (2-3 oraciones) y recuérdales que pueden buscar medicamentos
- NO diagnostiques ni recomiendes dosis — sugiere consultar al médico
- Si no entiendes: ACTION: unknown"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        reply = response.content[0].text.strip()
        logger.info("LLM intent for '%s': %s", text[:50], reply[:200])

        return _parse_llm_response(reply)

    except Exception:
        logger.error("LLM classification failed", exc_info=True)
        return Intent(action="drug_search", drug_query=text.strip())


async def classify_intent(text: str) -> Intent:
    """Classify user intent — DB keywords first, LLM fallback.

    Args:
        text: User message text.

    Returns:
        Classified Intent.
    """
    # Try DB keyword detection first (cached, instant, free)
    intent = await classify_intent_keywords(text)
    if intent is not None:
        logger.debug("Keyword intent: %s for '%s'", intent.action, text[:50])
        return intent

    # Ambiguous — use LLM
    logger.info("Keyword detection inconclusive for '%s' — calling LLM", text[:50])
    return await classify_intent_llm(text)


def _parse_llm_response(reply: str) -> Intent:
    """Parse the structured LLM response into an Intent."""
    fields: dict[str, str] = {}
    for line in reply.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().upper()
            value = value.strip()
            if key in ("ACTION", "DRUG", "NAME", "LOCATION", "RESPONSE"):
                fields[key] = value

    action = fields.get("ACTION", "unknown").lower()
    if action not in ("greeting", "drug_search", "question", "unknown"):
        action = "question" if fields.get("RESPONSE") else "unknown"

    return Intent(
        action=action,
        drug_query=fields.get("DRUG"),
        response_text=fields.get("RESPONSE"),
        detected_name=fields.get("NAME"),
        detected_location=fields.get("LOCATION"),
    )
