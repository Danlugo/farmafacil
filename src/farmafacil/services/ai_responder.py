"""AI Responder — orchestrates role-based LLM responses.

Handles the full flow for complex messages that need AI:
1. Route to the appropriate AI role
2. Assemble the full prompt (role + rules + skills + client memory)
3. Call the LLM
4. Auto-update client memory (async, non-blocking)

Also handles intent classification for the onboarding flow, extracting
name, location, and drug queries from user messages.
"""

import logging
from dataclasses import dataclass

import anthropic

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL
from farmafacil.services.ai_roles import assemble_prompt, get_role
from farmafacil.services.ai_router import DEFAULT_ROLE, route_to_role
from farmafacil.services.user_memory import auto_update_memory, get_memory

logger = logging.getLogger(__name__)


@dataclass
class AiResponse:
    """Response from the AI responder."""

    text: str
    role_used: str
    action: str = "ai_response"
    drug_query: str | None = None
    detected_name: str | None = None
    detected_location: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


# ── Hardcoded fallback prompt (safety net if no roles in DB) ──────────

_FALLBACK_PROMPT = """Eres FarmaFacil, un asistente de WhatsApp que ayuda a personas en Venezuela a encontrar medicamentos en farmacias cercanas.

Tu personalidad: amigable, servicial, empático. Hablas español venezolano natural. Eres conciso (esto es WhatsApp).

REGLAS:
- NO diagnostiques ni recomiendes dosis — sugiere consultar al médico
- Si no entiendes el mensaje, pide que reformulen
- Responde siempre en español"""


async def generate_response(
    message: str,
    user_id: int,
    user_name: str,
) -> AiResponse:
    """Generate an AI response using the role-based system.

    Routes the message to the appropriate role, assembles the full prompt,
    calls the LLM, and schedules memory update.

    Args:
        message: The user's message text.
        user_id: The user's database ID.
        user_name: The user's display name.

    Returns:
        AiResponse with the generated text and metadata.
    """
    # 1. Route to the appropriate role
    role_name = await route_to_role(message)

    # 2. Load role config
    role = await get_role(role_name)
    if not role:
        role = await get_role(DEFAULT_ROLE)

    # 3. Load client memory
    client_memory = await get_memory(user_id)

    # 4. Assemble the full system prompt
    if role:
        system_prompt = assemble_prompt(role, client_memory)
        role_used = role.name
    else:
        # Ultimate fallback — no roles in DB at all
        system_prompt = _FALLBACK_PROMPT
        if client_memory:
            system_prompt += f"\n\n## Client Context\n\n{client_memory}"
        role_used = "fallback"
        logger.warning("No AI roles in DB — using hardcoded fallback prompt")

    # 5. Call the LLM
    response_text, input_tokens, output_tokens = await _call_llm(
        system_prompt, message, user_name
    )

    # 6. Schedule async memory update (don't block the response)
    try:
        await auto_update_memory(user_id, user_name, message, response_text)
    except Exception:
        logger.error("Memory update failed (non-blocking)", exc_info=True)

    return AiResponse(
        text=response_text,
        role_used=role_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def classify_with_ai(message: str, user_id: int, user_name: str) -> AiResponse:
    """Classify a message and generate a response using AI.

    Used for complex messages that need both intent classification and
    a response. Returns structured data including detected name, location,
    and drug query alongside the response text.

    Args:
        message: The user's message text.
        user_id: The user's database ID.
        user_name: The user's display name.

    Returns:
        AiResponse with classification data and response text.
    """
    # Load the pharmacy_advisor role for classification
    role = await get_role("pharmacy_advisor")
    client_memory = await get_memory(user_id)

    if role:
        base_prompt = assemble_prompt(role, client_memory)
    else:
        base_prompt = _FALLBACK_PROMPT

    # Add classification instructions
    system_prompt = base_prompt + """

INSTRUCCIONES ADICIONALES: Analiza el mensaje del usuario y responde en formato estructurado. Extrae TODA la información que puedas del mensaje.

FORMATO DE RESPUESTA (usa exactamente estas líneas, omite las que no apliquen):
ACTION: [greeting|drug_search|question|unknown]
DRUG: [nombre del medicamento o producto tal como lo escribió el usuario]
NAME: [nombre de la persona si se presenta]
LOCATION: [zona/barrio/ciudad si menciona ubicación]
RESPONSE: [respuesta conversacional si es una pregunta]

REGLAS:
- Si el usuario da un nombre ESPECÍFICO de producto (con dosis, marca, presentación), usa ese nombre COMPLETO en DRUG
- Si mencionan síntomas, traduce al medicamento genérico más probable
- Si mencionan nombre y medicamento en el mismo mensaje, extrae ambos
- Si preguntan sobre salud, responde brevemente y recuérdales que pueden buscar medicamentos
- Si no entiendes: ACTION: unknown"""

    if not ANTHROPIC_API_KEY:
        return AiResponse(
            text="",
            role_used="fallback",
            action="drug_search",
            drug_query=message.strip(),
        )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )
        reply = response.content[0].text.strip()
        logger.info("AI classify for '%s': %s", message[:50], reply[:200])

        parsed = _parse_structured_response(reply)
        parsed.role_used = role.name if role else "fallback"
        parsed.input_tokens = response.usage.input_tokens
        parsed.output_tokens = response.usage.output_tokens
        return parsed

    except Exception:
        logger.error("AI classification failed", exc_info=True)
        return AiResponse(
            text="",
            role_used="fallback",
            action="drug_search",
            drug_query=message.strip(),
        )


async def _call_llm(
    system_prompt: str, message: str, user_name: str,
) -> tuple[str, int, int]:
    """Make the LLM call with the assembled prompt.

    Args:
        system_prompt: The full system prompt (role + rules + skills + memory).
        message: The user's message.
        user_name: The user's display name (for context).

    Returns:
        Tuple of (response_text, input_tokens, output_tokens).
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY — cannot generate AI response")
        return ("Lo siento, no puedo responder en este momento. Enviame el nombre de un medicamento para buscar.", 0, 0)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=500,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"[{user_name}]: {message}",
                }
            ],
        )
        return (
            response.content[0].text.strip(),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    except Exception:
        logger.error("LLM call failed", exc_info=True)
        return ("Lo siento, tuve un error. Enviame el nombre de un medicamento para buscar.", 0, 0)


def _parse_structured_response(reply: str) -> AiResponse:
    """Parse the structured LLM response into an AiResponse.

    Args:
        reply: Raw LLM response with ACTION/DRUG/NAME/LOCATION/RESPONSE lines.

    Returns:
        Parsed AiResponse.
    """
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

    return AiResponse(
        text=fields.get("RESPONSE", ""),
        role_used="",
        action=action,
        drug_query=fields.get("DRUG"),
        detected_name=fields.get("NAME"),
        detected_location=fields.get("LOCATION"),
    )
