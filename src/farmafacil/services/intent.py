"""Intent detection — keyword-first, LLM fallback for complex messages.

Flow:
1. Check for exact keyword matches (greetings, commands, zone changes) — instant
2. If message looks like a drug name (short, no question marks) — treat as drug search
3. If ambiguous or conversational — call Claude Haiku to classify intent and extract drug name
"""

import logging
import re
from dataclasses import dataclass

import anthropic

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)

GREETING_WORDS = {
    "hola", "hi", "hello", "hey", "buenos dias", "buenas tardes",
    "buenas noches", "buenas", "saludos", "que tal",
}

LOCATION_CHANGE_WORDS = {
    "cambiar ubicacion", "cambiar ubicación", "cambiar zona",
    "nueva ubicacion", "nueva ubicación", "otra zona", "moverme",
}

HELP_WORDS = {
    "ayuda", "help", "como funciona", "que puedes hacer",
    "que haces", "instrucciones", "menu",
}


@dataclass
class Intent:
    """Classified user intent."""

    action: str  # "greeting", "location_change", "help", "drug_search", "question", "unknown"
    drug_query: str | None = None  # Extracted drug name for drug_search intent
    response_text: str | None = None  # Direct response for question intent


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


def classify_intent_keywords(text: str) -> Intent | None:
    """Try to classify intent using keyword matching only.

    Args:
        text: User message text (already stripped).

    Returns:
        Intent if classified, None if ambiguous (needs LLM).
    """
    text_lower = text.lower().strip()

    # Greetings
    if text_lower in GREETING_WORDS:
        return Intent(action="greeting")

    # Location change
    if text_lower in LOCATION_CHANGE_WORDS:
        return Intent(action="location_change")

    # Help
    if text_lower in HELP_WORDS:
        return Intent(action="help", response_text=HELP_MESSAGE)

    # Short message (1-4 words), no question marks, no common question starters
    # → almost certainly a drug name
    words = text_lower.split()
    is_question = "?" in text or text_lower.startswith(("como ", "donde ", "que ", "cual ",
        "cuando ", "por que ", "cuanto ", "tienen ", "hay ", "puedo ", "puedes "))

    if len(words) <= 4 and not is_question:
        return Intent(action="drug_search", drug_query=text.strip())

    # Longer text without question markers — still likely a drug search
    # (e.g., "losartan potasico 50mg tabletas")
    if not is_question and len(words) <= 8:
        return Intent(action="drug_search", drug_query=text.strip())

    # Ambiguous — needs LLM
    return None


async def classify_intent_llm(text: str) -> Intent:
    """Use Claude Haiku to classify intent and extract drug names.

    Args:
        text: User message text.

    Returns:
        Classified intent with optional drug name or response.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY set — falling back to drug search")
        return Intent(action="drug_search", drug_query=text.strip())

    system_prompt = """Eres FarmaFacil, un asistente de WhatsApp que ayuda a personas en Venezuela a encontrar medicamentos en farmacias cercanas.

Tu personalidad: amigable, servicial, empático con la situación de salud en Venezuela. Hablas español venezolano natural (no demasiado formal). Eres conciso porque esto es WhatsApp, no un email.

TU TRABAJO PRINCIPAL es clasificar el mensaje del usuario y responder adecuadamente.

REGLA 1 — Si el usuario busca un medicamento (por nombre, síntoma, o condición médica), responde EXACTAMENTE así:
DRUG_SEARCH: [nombre genérico del medicamento más probable]

Ejemplos:
- "necesito algo para el dolor de cabeza" → DRUG_SEARCH: acetaminofen
- "medicina para la presión alta" → DRUG_SEARCH: losartan
- "algo para la gripe" → DRUG_SEARCH: antigripal
- "antibiótico para infección urinaria" → DRUG_SEARCH: ciprofloxacina
- "insulina para diabéticos" → DRUG_SEARCH: insulina
- "me duele la garganta" → DRUG_SEARCH: ibuprofeno
- "tengo alergia" → DRUG_SEARCH: loratadina

REGLA 2 — Si el usuario hace una pregunta sobre salud, medicamentos, farmacias, o el servicio, responde brevemente en español de forma amigable. Limita tu respuesta a 2-3 oraciones máximo.

Cosas que PUEDES responder:
- Preguntas generales sobre medicamentos ("para qué sirve el losartán?")
- Cómo usar FarmaFacil ("cómo busco un medicamento?")
- Preguntas sobre farmacias en Venezuela
- Información general de salud (con disclaimer de consultar al médico)

Cosas que NO PUEDES hacer (y debes decirlo amablemente):
- Diagnosticar enfermedades — sugiere ir al médico
- Recomendar dosis específicas — sugiere consultar con su farmacéutico
- Procesar pagos o pedidos (aún no está disponible)
- Buscar farmacias que no sean Farmatodo (por ahora)

Siempre termina tu respuesta recordándole que puede enviar el nombre de un medicamento para buscarlo.

REGLA 3 — Si no entiendes el mensaje, responde:
UNKNOWN"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )
        reply = response.content[0].text.strip()
        logger.info("LLM intent for '%s': %s", text[:50], reply[:100])

        # Parse the LLM response
        if reply.startswith("DRUG_SEARCH:"):
            drug_name = reply.replace("DRUG_SEARCH:", "").strip()
            return Intent(action="drug_search", drug_query=drug_name)
        elif reply.startswith("UNKNOWN"):
            return Intent(
                action="question",
                response_text=(
                    "No estoy seguro de lo que necesitas. "
                    "Envia el nombre de un medicamento y te busco donde esta disponible.\n\n"
                    "Escribe _ayuda_ para ver las instrucciones."
                ),
            )
        else:
            # LLM gave a conversational response
            return Intent(action="question", response_text=reply)

    except Exception:
        logger.error("LLM classification failed", exc_info=True)
        # Fallback: treat as drug search
        return Intent(action="drug_search", drug_query=text.strip())


async def classify_intent(text: str) -> Intent:
    """Classify user intent — keywords first, LLM fallback.

    Args:
        text: User message text.

    Returns:
        Classified Intent.
    """
    # Try keyword detection first (instant, free)
    intent = classify_intent_keywords(text)
    if intent is not None:
        logger.debug("Keyword intent: %s for '%s'", intent.action, text[:50])
        return intent

    # Ambiguous — use LLM
    logger.info("Keyword detection inconclusive for '%s' — calling LLM", text[:50])
    return await classify_intent_llm(text)
