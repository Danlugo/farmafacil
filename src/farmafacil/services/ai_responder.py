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
from dataclasses import dataclass, field

import anthropic
from anthropic import APIConnectionError, APIError
from sqlalchemy import select

from farmafacil.config import ANTHROPIC_API_KEY, LLM_MODEL_OPUS
from farmafacil.db.session import async_session
from farmafacil.models.database import User
from farmafacil.services.ai_roles import assemble_prompt, get_role
from farmafacil.services.ai_router import DEFAULT_ROLE, route_to_role
from farmafacil.services.settings import resolve_user_model
from farmafacil.services.user_memory import get_memory

logger = logging.getLogger(__name__)

# ── Classification instructions appended to every classify_with_ai call ──
# Extracted as a module-level constant so test_drug_liability.py can verify
# consistency with the seed rules (no_drug_recommendations, symptom_acknowledgment).
# If you change symptom-handling policy in seed.py, update this too — and vice versa.
CLASSIFY_INSTRUCTIONS = """

INSTRUCCIONES ADICIONALES: Analiza el mensaje del usuario y responde en formato estructurado. Extrae TODA la información que puedas del mensaje.

FORMATO DE RESPUESTA (usa exactamente estas líneas, omite las que no apliquen):
ACTION: [greeting|drug_search|clarify_needed|nearest_store|view_similar|emergency|question|unknown]
DRUG: [nombre del producto tal como lo escribió el usuario]
NAME: [nombre de la persona si se presenta]
LOCATION: [zona/barrio/ciudad si menciona ubicación]
CLARIFY_QUESTION: [pregunta de clarificación si ACTION=clarify_needed]
CLARIFY_CONTEXT: [la consulta original vaga del usuario, tal como la escribió]
RESPONSE: [respuesta conversacional si es una pregunta]

REGLAS:
- Si el usuario pregunta por la farmacia más cercana, farmacias cerca, dónde comprar, o cualquier variación de "qué farmacia queda cerca": clasifica como nearest_store. NO hagas preguntas — el sistema mostrará las farmacias más cercanas automáticamente. Si el usuario tiene historial de búsquedas o menciona una cadena preferida, puedes incluir un RESPONSE breve mencionándolo.
- Si el usuario pide CUALQUIER producto de farmacia (medicamentos, skincare, vitaminas, cuidado personal, belleza, higiene, bebé, etc.), clasifica como drug_search con el nombre en DRUG
- Si el usuario pide un producto por nombre SIN mencionar síntomas (solo "aspirina", "omeprazol", "protector solar"), usa el nombre en DRUG y NO incluyas RESPONSE — busca directamente
- Si mencionan SÍNTOMAS Y un producto (ej: "tengo dolor de cabeza, busca aspirina"): clasifica como drug_search, pon el producto mencionado en DRUG, incluye RESPONSE breve reconociendo el síntoma + "consulta con tu médico".
- ⚠️ Si mencionan SOLO SÍNTOMAS sin nombrar un producto (ej: "me duele la cabeza", "tengo acidez", "dolor de estomago"): clasifica como question (NO drug_search). En RESPONSE: (1) reconoce el síntoma, (2) LISTA opciones OTC comunes para ese síntoma (ej: para estómago: Omeprazol, Ranitidina, antiácidos; para dolor de cabeza: Acetaminofén, Ibuprofeno, Aspirina), (3) pregunta cuál quiere buscar, (4) incluye "consulta con tu médico". NUNCA elijas un medicamento por el usuario ni clasifiques como drug_search cuando no nombran un producto.
- ⭐ CLARIFICACIÓN para CATEGORÍAS VAGAS con múltiples formatos: Si el usuario pide una CATEGORÍA de productos que viene en varios formatos/marcas/tipos distintos Y NO especifica cuál (ej: "medicinas para la memoria", "algo para dormir", "vitaminas", "suplementos", "productos para la piel", "algo para el cabello", "cosas para bebé", "condones", "anticonceptivos", "lentes de contacto", "kit dental", "productos de higiene íntima"), NO busques directamente — esto evita gastar llamadas a las APIs de farmacias antes de saber qué buscar. Clasifica como clarify_needed, pon la consulta original del usuario en CLARIFY_CONTEXT, y en CLARIFY_QUESTION haz UNA pregunta corta y amigable que le ayude a escoger: formato (pastillas / jarabe / gotas / bebibles / masticables / cremas), edad (adulto / niño), tipo, o marca preferida. Ejemplos:
  * "medicinas para la memoria" → CLARIFY_QUESTION: "¿Prefieres pastillas o bebibles? ¿Es para adulto o niño? Así te busco la mejor opción."
  * "algo para dormir" → CLARIFY_QUESTION: "¿Buscas algo natural (tipo melatonina o valeriana) o un medicamento recetado? ¿Pastillas o gotas?"
  * "vitaminas" → CLARIFY_QUESTION: "¿Qué tipo de vitaminas? (multivitamínico, vitamina C, D, B12, etc.) ¿Pastillas, gomitas o líquido?"
  * "necesito condones" → CLARIFY_QUESTION: "¿Tienes una marca preferida (Trojan, Durex, Sico)? ¿Algún tipo en particular (lubricado, ultradelgado, retardante)?"
  * "anticonceptivos" → CLARIFY_QUESTION: "¿Pastillas anticonceptivas, condones, o algo de emergencia? Si son pastillas, ¿tienes una marca recetada?"
  * "lentes de contacto" → CLARIFY_QUESTION: "¿Sabes la graduación o la marca que usas? ¿Diarios, mensuales, o de uso prolongado?"
  * "kit dental" → CLARIFY_QUESTION: "¿Buscas un kit completo (cepillo + pasta + hilo) o algo específico? ¿Adulto o niño?"
  * "productos de higiene íntima" → CLARIFY_QUESTION: "¿Para hombre o mujer? ¿Jabón, toallitas, o algo específico?"
  * "protector solar" (producto específico, NO vago) → drug_search directo, NO clarificar
  * "omeprazol" (producto específico) → drug_search directo, NO clarificar
  * "Trojan ultradelgado" (marca + tipo específico) → drug_search directo, NO clarificar
  NO uses clarify_needed si el usuario ya nombró un producto específico, una marca, o un ingrediente activo. Solo para categorías genéricas ambiguas con múltiples marcas/tipos.
- Si mencionan nombre y medicamento en el mismo mensaje, extrae ambos
- Solo clasifica como question/unknown si el producto claramente NO se vende en farmacias
- En caso de duda, SIEMPRE clasifica como drug_search — es mejor buscar y no encontrar que rechazar
- EXCEPCIÓN DE SEGURIDAD: Si el usuario menciona que TOMA otro medicamento o tiene una condición médica (ej: "tomo warfarina", "soy diabético", "estoy embarazada", "tomo anticoagulantes"), SIEMPRE incluye RESPONSE con: (1) advertencia de que podría haber interacciones, (2) recomendación FIRME de consultar con su médico o farmacéutico ANTES de tomar el producto, (3) busca el producto de todas formas pero con la advertencia. Ejemplo: "⚠️ Mencionas que tomas warfarina. Aspirina puede interactuar con anticoagulantes y aumentar riesgo de sangrado. Te recomiendo CONSULTAR CON TU MÉDICO antes de combinarlos. Te busco Aspirina de todas formas para que veas disponibilidad."
- Si el usuario dice "ver similares", "similares", "ver otros", "mostrar similares", o "ver mas": clasifica como view_similar. El sistema re-ejecutará la última búsqueda mostrando más variantes del producto.
- ⚠️ EMERGENCIA: Si el usuario describe una emergencia médica (dolor de pecho, no puede respirar, convulsiones, sobredosis, sangrado severo, pensamientos suicidas), clasifica INMEDIATAMENTE como emergency con RESPONSE que incluya números de emergencia. NO busques productos — esto tiene PRIORIDAD MÁXIMA.
- En general NO hagas preguntas de seguimiento. Da información útil, alternativas, y advertencias directamente. Solo pregunta si hay una preocupación de seguridad real (medicamentos que podrían interactuar).
- Si no entiendes: ACTION: unknown"""


@dataclass
class AiResponse:
    """Response from the AI responder."""

    text: str
    role_used: str
    action: str = "ai_response"
    drug_query: str | None = None
    detected_name: str | None = None
    detected_location: str | None = None
    # Clarification flow: when action == "clarify_needed", the bot asks
    # clarify_question and stores clarify_context (the original vague query)
    # in user.awaiting_clarification_context to merge with the next reply.
    clarify_question: str | None = None
    clarify_context: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # Concrete model name actually used for this call (e.g.
    # ``claude-sonnet-4-20250514``). Threaded through so the chat debug
    # footer can render the live model and ``increment_token_usage`` can
    # route tokens to the correct per-model bucket. Empty string means
    # "no LLM call was made" (e.g. fallback path with no API key).
    # (v0.19.2, Item 49 — admin set_default_model now actually takes effect.)
    model: str = ""


@dataclass
class AdminTurnResult:
    """Result of a full ``run_admin_turn`` loop (Item 35, v0.14.0).

    A single admin "turn" can make multiple LLM calls (one per tool step),
    so ``input_tokens`` / ``output_tokens`` are summed across ALL iterations.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0
    tools_used: list[str] = field(default_factory=list)


# ── Hardcoded fallback prompt (safety net if no roles in DB) ──────────

async def _get_user_profile(user_id: int) -> dict | None:
    """Load live user profile data for prompt injection.

    Returns:
        Dict with name, zone, city_code, preference — or None if not found.
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            return None
        return {
            "name": user.name,
            "zone": user.zone_name,
            "city_code": user.city_code,
            "preference": user.display_preference,
        }


_FALLBACK_PROMPT = """Eres FarmaFacil, un asistente de WhatsApp que ayuda a personas en Venezuela a encontrar productos en farmacias cercanas (medicamentos, cuidado personal, belleza, vitaminas, suplementos, productos para bebé, y más).

Tu personalidad: amigable, servicial, empático. Hablas español venezolano natural. Eres conciso (esto es WhatsApp).

REGLAS:
- NO diagnostiques ni recomiendes dosis — sugiere consultar al médico
- Si el usuario pide cualquier producto de farmacia, SIEMPRE intenta buscarlo
- Solo rechaza búsquedas de productos que NO se venden en farmacias
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

    # 3. Load client memory and live profile
    client_memory = await get_memory(user_id)
    user_profile = await _get_user_profile(user_id)

    # 4. Assemble the full system prompt
    if role:
        system_prompt = assemble_prompt(role, client_memory, user_profile)
        role_used = role.name
    else:
        # Ultimate fallback — no roles in DB at all
        system_prompt = _FALLBACK_PROMPT
        if client_memory:
            system_prompt += f"\n\n## Client Context\n\n{client_memory}"
        role_used = "fallback"
        logger.warning("No AI roles in DB — using hardcoded fallback prompt")

    # 5. Call the LLM
    response_text, input_tokens, output_tokens, model_used = await _call_llm(
        system_prompt, message, user_name
    )

    # Note: memory update is handled by the caller (handler.py) to ensure
    # ALL interaction types (drug searches, questions, etc.) build memory.

    return AiResponse(
        text=response_text,
        role_used=role_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model_used,
    )


async def classify_with_ai(
    message: str,
    user_id: int,
    user_name: str,
    phone_number: str | None = None,
) -> AiResponse:
    """Classify a message and generate a response using AI.

    Used for complex messages that need both intent classification and
    a response. Returns structured data including detected name, location,
    and drug query alongside the response text.

    When ``phone_number`` is provided, recent conversation history is
    included so the AI has context for follow-up questions like "which
    one is cheapest?" after a search.

    Args:
        message: The user's message text.
        user_id: The user's database ID.
        user_name: The user's display name.
        phone_number: WhatsApp phone number for conversation history lookup.

    Returns:
        AiResponse with classification data and response text.
    """
    # Load the pharmacy_advisor role for classification
    role = await get_role("pharmacy_advisor")
    client_memory = await get_memory(user_id)
    user_profile = await _get_user_profile(user_id)

    if role:
        base_prompt = assemble_prompt(role, client_memory, user_profile)
    else:
        base_prompt = _FALLBACK_PROMPT

    # Add classification instructions (extracted to module-level constant
    # so test_drug_liability.py can verify consistency with seed rules).
    system_prompt = base_prompt + CLASSIFY_INSTRUCTIONS

    if not ANTHROPIC_API_KEY:
        return AiResponse(
            text="",
            role_used="fallback",
            action="drug_search",
            drug_query=message.strip(),
        )

    try:
        # Build messages list — include recent conversation history for
        # follow-up context (e.g., "which is cheapest?" after a search).
        messages: list[dict[str, str]] = []
        if phone_number:
            from farmafacil.services.conversation_log import get_recent_history

            history = await get_recent_history(phone_number, limit=10)
            # Anthropic requires alternating user/assistant roles.
            # Deduplicate consecutive same-role messages by merging.
            for msg in history:
                if messages and messages[-1]["role"] == msg["role"]:
                    messages[-1]["content"] += "\n" + msg["content"]
                else:
                    messages.append(dict(msg))

        # Append the current message (it may already be in history from
        # the inbound log, so replace the last user message if it matches)
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = message
        else:
            messages.append({"role": "user", "content": message})

        # Resolve the user-facing model from app_settings.default_model so
        # the admin /model command (and admin chat tool set_default_model)
        # actually changes which model the bot uses. (v0.19.2, Item 49.)
        resolved_model = await resolve_user_model()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=resolved_model,
            max_tokens=500,
            system=system_prompt,
            messages=messages,
        )
        reply = response.content[0].text.strip()
        logger.info(
            "AI classify (model=%s) for '%s': %s",
            resolved_model, message[:50], reply[:200],
        )

        parsed = _parse_structured_response(reply)
        parsed.role_used = role.name if role else "fallback"
        parsed.input_tokens = response.usage.input_tokens
        parsed.output_tokens = response.usage.output_tokens
        parsed.model = resolved_model
        return parsed

    except (APIError, APIConnectionError) as exc:
        logger.error("AI classification — Anthropic API error: %s", exc)
        return AiResponse(
            text="",
            role_used="fallback",
            action="drug_search",
            drug_query=message.strip(),
        )
    except Exception:
        # Last-resort: parser / response shape bugs should not take the bot
        # down. Fall back to treating the message as a drug search.
        logger.error("AI classification — unexpected error", exc_info=True)
        return AiResponse(
            text="",
            role_used="fallback",
            action="drug_search",
            drug_query=message.strip(),
        )


async def _call_llm(
    system_prompt: str, message: str, user_name: str,
) -> tuple[str, int, int, str]:
    """Make the LLM call with the assembled prompt.

    Args:
        system_prompt: The full system prompt (role + rules + skills + memory).
        message: The user's message.
        user_name: The user's display name (for context).

    Returns:
        Tuple of (response_text, input_tokens, output_tokens, model_used).
        ``model_used`` is the concrete model id resolved from
        ``app_settings.default_model``, or "" if no API call was made.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY — cannot generate AI response")
        return (
            "Lo siento, no puedo responder en este momento. "
            "Enviame el nombre de un producto de farmacia para buscar.",
            0, 0, "",
        )

    try:
        # Resolve the user-facing model from app_settings.default_model.
        # (v0.19.2, Item 49 — was hardcoded to LLM_MODEL/haiku before.)
        resolved_model = await resolve_user_model()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=resolved_model,
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
            resolved_model,
        )

    except (APIError, APIConnectionError) as exc:
        logger.error("LLM call — Anthropic API error: %s", exc)
        return (
            "Lo siento, tuve un error. "
            "Enviame el nombre de un producto de farmacia para buscar.",
            0, 0, "",
        )
    except Exception:
        # Last-resort: unexpected response shape / parsing issue. Still
        # return a safe fallback rather than crashing the caller.
        logger.error("LLM call — unexpected error", exc_info=True)
        return (
            "Lo siento, tuve un error. "
            "Enviame el nombre de un producto de farmacia para buscar.",
            0, 0, "",
        )


# ── Clarified-query refiner ────────────────────────────────────────────

_REFINER_SYSTEM_PROMPT = """Eres un asistente de busqueda de productos de farmacia en Venezuela.

El usuario hizo una pregunta VAGA sobre una categoria de productos y despues respondio una pregunta aclaratoria sobre el formato o preferencia. Tu trabajo es convertir AMBAS entradas en UN solo termino de busqueda corto y concreto que funcione en un catalogo de productos de farmacia (Algolia/VTEX).

REGLAS ESTRICTAS:
- Responde SOLO con el termino de busqueda. Sin explicacion, sin puntuacion final, sin comillas, sin prefijos.
- 2 a 5 palabras maximo.
- En minusculas, sin tildes si es posible, sin signos de puntuacion.
- Usa nombres de productos o ingredientes activos reales, no frases conversacionales.
- Si la respuesta del usuario menciona una forma farmaceutica (pastillas, gomitas, jarabe, capsulas, bebible, crema, gel), INCLUYELA.
- Si menciona edad (niño, adulto, bebe), puedes incluirla como palabra clave.
- NO incluyas frases como "que recomiendas", "algo para", "me gusta", "para mi".

EJEMPLOS:
Vaga: "medicinas para la memoria" / Respuesta: "adulto, gomitas" -> ginkgo gomitas adulto
Vaga: "algo para dormir" / Respuesta: "pastillas" -> melatonina pastillas
Vaga: "vitaminas" / Respuesta: "para niño, bebible" -> multivitaminico niños jarabe
Vaga: "algo para el cabello" / Respuesta: "caida" -> biotina cabello
Vaga: "suplementos" / Respuesta: "para energia, capsulas" -> vitamina b12 capsulas
Vaga: "algo para la tos" / Respuesta: "jarabe, adulto" -> jarabe tos adulto
Vaga: "protector solar" / Respuesta: "para cara" -> protector solar facial
Vaga: "necesito condones" / Respuesta: "trojan ultradelgado" -> trojan ultradelgado
Vaga: "condones" / Respuesta: "durex" -> condones durex
Vaga: "anticonceptivos" / Respuesta: "pastillas yasmin" -> yasmin pastillas
Vaga: "lentes de contacto" / Respuesta: "mensuales" -> lentes contacto mensuales
Vaga: "kit dental" / Respuesta: "adulto cepillo y pasta" -> kit dental adulto"""


async def refine_clarified_query(
    original_context: str, user_answer: str,
) -> tuple[str, int, int, str]:
    """Distill a vague query + clarifying answer into a concrete search term.

    Takes the original vague query that triggered the clarification and the
    user's answer to the clarifying question, and asks the LLM to produce a
    short (2-5 words) product search keyword suitable for a pharmacy catalog.

    If the LLM call fails OR the API key is missing, falls back to returning
    the user's answer alone (which is usually closer to a real product name
    than the vague original context).

    Args:
        original_context: The original vague query, e.g. "medicinas para la memoria".
        user_answer: The user's clarifying answer, e.g. "gomitas adulto".

    Returns:
        Tuple of ``(refined_query, input_tokens, output_tokens, model_used)``.
        ``model_used`` is the concrete model id resolved from
        ``app_settings.default_model``, or "" if no API call was made.
        (v0.19.2 added the model field so callers can route token usage to
        the correct per-model bucket.)
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("refine_clarified_query: no API key, falling back to answer")
        return (user_answer.strip(), 0, 0, "")

    user_message = (
        f"Pregunta vaga: {original_context}\n"
        f"Respuesta del usuario: {user_answer}\n"
        f"Termino de busqueda:"
    )

    try:
        # Resolve the user-facing model from app_settings.default_model.
        # (v0.19.2, Item 49 — was hardcoded to LLM_MODEL/haiku before.)
        resolved_model = await resolve_user_model()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=resolved_model,
            max_tokens=40,
            system=_REFINER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        refined = response.content[0].text.strip()
        # Strip any stray punctuation/quotes the LLM might still emit.
        refined = refined.strip(" \t\n\"'`.,;:!?")
        if not refined:
            logger.warning(
                "refine_clarified_query: empty LLM response, falling back. "
                "context=%r answer=%r", original_context, user_answer,
            )
            return (
                user_answer.strip(),
                response.usage.input_tokens,
                response.usage.output_tokens,
                resolved_model,
            )
        logger.info(
            "refine_clarified_query (model=%s): %r + %r -> %r",
            resolved_model, original_context, user_answer, refined,
        )
        return (
            refined,
            response.usage.input_tokens,
            response.usage.output_tokens,
            resolved_model,
        )
    except (APIError, APIConnectionError) as exc:
        logger.error(
            "refine_clarified_query — Anthropic API error: %s "
            "(falling back to raw answer)", exc,
        )
        return (user_answer.strip(), 0, 0, "")
    except Exception:
        # Last-resort: unexpected error should still fall back so the user
        # always gets a search dispatched.
        logger.error(
            "refine_clarified_query — unexpected error, "
            "falling back to raw answer", exc_info=True,
        )
        return (user_answer.strip(), 0, 0, "")


# ── App Admin chat turn runner ────────────────────────────────────────

# Maximum tool-call steps per admin turn. Each step is one LLM roundtrip.
# A cap prevents a confused LLM from looping forever on a malformed tool.
MAX_ADMIN_STEPS = 5


def _parse_admin_action(reply: str) -> tuple[str, dict[str, str]]:
    """Parse an App Admin LLM reply into (action, fields).

    The admin LLM is instructed to emit either::

        ACTION: TOOL_CALL
        TOOL: <tool_name>
        ARGS: <json_object>

    OR::

        ACTION: FINAL
        RESPONSE: <text shown to the user>

    We parse loosely to be robust to minor format drift — the RESPONSE /
    ARGS values can span multiple lines, so everything after the key marker
    is consumed until the next recognised key OR end of string.
    """
    fields: dict[str, str] = {}
    current_key: str | None = None
    buffer: list[str] = []
    keys = ("ACTION", "TOOL", "ARGS", "RESPONSE")
    for line in reply.splitlines():
        stripped = line.strip()
        matched_key = None
        for key in keys:
            prefix = f"{key}:"
            if stripped.upper().startswith(prefix):
                matched_key = key
                break
        if matched_key:
            if current_key is not None:
                fields[current_key] = "\n".join(buffer).strip()
            current_key = matched_key
            # Take whatever is after the "KEY:" marker on the same line
            buffer = [stripped[len(matched_key) + 1 :].strip()]
        else:
            if current_key is not None:
                buffer.append(line)
    if current_key is not None:
        fields[current_key] = "\n".join(buffer).strip()
    action = fields.pop("ACTION", "").upper() or "FINAL"
    return action, fields


async def run_admin_turn(
    user_message: str | list[dict],
    system_prompt: str,
    history: list[dict[str, str]] | None = None,
    *,
    admin_user_id: int | None = None,
) -> AdminTurnResult:
    """Run a single admin chat turn with tool-call loop.

    The admin AI is HARDCODED to Claude Opus regardless of the
    ``default_model`` app_setting — admin work benefits from Opus's
    superior reasoning, and admin calls are a tiny fraction of traffic
    so the cost difference is negligible. Admin cost is also tracked in
    its own bucket (``tokens_*_admin`` / ``calls_admin``) so it never
    pollutes user-facing cost metrics.

    The loop:
      1. Calls Opus with the assembled admin prompt + user message.
      2. Parses the reply — if it's a TOOL_CALL, executes the tool via
         ``admin_chat.execute_tool`` and appends the result as a user
         turn, then loops.
      3. If it's a FINAL response OR the tool-step budget is exhausted,
         returns the accumulated text + tokens.

    Args:
        user_message: The admin's raw chat message (already stripped of
            the ``/admin`` prefix).
        system_prompt: Fully-assembled system prompt (admin role + rules
            + skills + memory + live tool manifest).
        history: Optional prior turns in Anthropic message format to
            preserve short-term context across the same admin session.
            Must contain ONLY valid ``{"role", "content"}`` dicts — the
            list is forwarded to the Anthropic messages API as-is.
        admin_user_id: The database ID of the admin user making the
            request. Forwarded to ``execute_tool`` so audit-trail tools
            (``report_issue``) can attribute rows to the right admin.

    Returns:
        AdminTurnResult with final text, summed token counts, step count,
        and a flat list of tool names that were invoked.
    """
    # Local import to avoid a cycle (admin_chat imports settings, which
    # imports nothing from here, but the tool registry is only needed when
    # this coroutine actually runs).
    from farmafacil.services.admin_chat import execute_tool, parse_tool_args

    if not ANTHROPIC_API_KEY:
        return AdminTurnResult(
            text=(
                "No puedo ejecutar acciones de admin ahora mismo (falta "
                "ANTHROPIC_API_KEY)."
            ),
        )

    # Defensive-copy history with element-level validation so we never forward
    # malformed dicts (missing role/content, or a stray sentinel) to the
    # Anthropic API — a malformed entry would produce an opaque 400.
    messages: list[dict[str, str]] = []
    dropped = 0
    for m in (history or []):
        if (
            isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and (isinstance(m.get("content"), (str, list)))
        ):
            messages.append({"role": m["role"], "content": m["content"]})
        else:
            dropped += 1
    if dropped:
        logger.warning(
            "run_admin_turn: dropped %d malformed history element(s)", dropped,
        )
    # user_message can be a str or a list of content blocks (for images)
    messages.append({"role": "user", "content": user_message})

    total_in = 0
    total_out = 0
    tools_used: list[str] = []

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception:  # noqa: BLE001 — client init failure must not kill handler
        logger.error("run_admin_turn: failed to init Anthropic client", exc_info=True)
        return AdminTurnResult(
            text="Error interno inicializando el cliente de IA.",
        )

    for step in range(1, MAX_ADMIN_STEPS + 1):
        try:
            response = client.messages.create(
                model=LLM_MODEL_OPUS,
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            )
        except (APIError, APIConnectionError) as exc:
            logger.error("run_admin_turn step %d — API error: %s", step, exc)
            return AdminTurnResult(
                text=f"Error llamando al modelo: {exc}",
                input_tokens=total_in,
                output_tokens=total_out,
                steps=step - 1,
                tools_used=tools_used,
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "run_admin_turn step %d — unexpected error", step, exc_info=True,
            )
            return AdminTurnResult(
                text="Error inesperado en el loop de admin.",
                input_tokens=total_in,
                output_tokens=total_out,
                steps=step - 1,
                tools_used=tools_used,
            )

        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens
        reply_text = response.content[0].text.strip() if response.content else ""
        logger.info(
            "admin turn step=%d in=%d out=%d reply=%r",
            step, response.usage.input_tokens, response.usage.output_tokens,
            reply_text[:200],
        )

        action, fields = _parse_admin_action(reply_text)

        if action == "TOOL_CALL":
            tool_name = fields.get("TOOL", "").strip()
            tool_args = parse_tool_args(fields.get("ARGS", ""))
            if not tool_name:
                # Malformed tool call — surface back to the LLM so it
                # can retry with a correct shape.
                messages.append({"role": "assistant", "content": reply_text})
                messages.append({
                    "role": "user",
                    "content": "Tool call sin TOOL. Repite con TOOL + ARGS.",
                })
                continue
            tools_used.append(tool_name)
            tool_result = await execute_tool(
                tool_name, tool_args, admin_user_id=admin_user_id,
            )
            # Append both sides so the LLM sees its own request and the
            # observation, then let it decide what to do next.
            messages.append({"role": "assistant", "content": reply_text})
            messages.append({
                "role": "user",
                "content": f"TOOL_RESULT {tool_name}:\n{tool_result}",
            })
            continue

        # ACTION: FINAL (or anything else — treat as final so a malformed
        # reply still gets surfaced to the user instead of looping).
        final_text = fields.get("RESPONSE", "").strip() or reply_text
        return AdminTurnResult(
            text=final_text,
            input_tokens=total_in,
            output_tokens=total_out,
            steps=step,
            tools_used=tools_used,
        )

    # Step budget exhausted — return whatever we have with a cap notice.
    return AdminTurnResult(
        text=(
            "Se alcanzó el límite de pasos del admin. Intenta dividir la "
            "tarea en pasos más chicos."
        ),
        input_tokens=total_in,
        output_tokens=total_out,
        steps=MAX_ADMIN_STEPS,
        tools_used=tools_used,
    )


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
            if key in (
                "ACTION",
                "DRUG",
                "NAME",
                "LOCATION",
                "RESPONSE",
                "CLARIFY_QUESTION",
                "CLARIFY_CONTEXT",
            ):
                fields[key] = value

    action = fields.get("ACTION", "unknown").lower()
    valid_actions = (
        "greeting",
        "drug_search",
        "clarify_needed",
        "nearest_store",
        "view_similar",
        "emergency",
        "question",
        "unknown",
    )
    if action not in valid_actions:
        action = "question" if fields.get("RESPONSE") else "unknown"

    # Defensive: if LLM said clarify_needed but didn't provide a question,
    # degrade to drug_search so we don't leave the user hanging.
    if action == "clarify_needed" and not fields.get("CLARIFY_QUESTION"):
        action = "drug_search"

    return AiResponse(
        text=fields.get("RESPONSE", ""),
        role_used="",
        action=action,
        drug_query=fields.get("DRUG"),
        detected_name=fields.get("NAME"),
        detected_location=fields.get("LOCATION"),
        clarify_question=fields.get("CLARIFY_QUESTION"),
        clarify_context=fields.get("CLARIFY_CONTEXT"),
    )
