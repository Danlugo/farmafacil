"""Handle incoming WhatsApp messages — smart conversational flow."""

import logging
import os

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_image_message, send_local_image, send_text_message
from farmafacil.models.schemas import DrugResult
from farmafacil.services.ai_responder import classify_with_ai, generate_response
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.image_grid import generate_product_grid
from farmafacil.services.intent import HELP_MESSAGE, classify_intent
from farmafacil.services.search import search_drug
from farmafacil.services.search_feedback import (
    log_search,
    parse_feedback,
    record_feedback,
    record_feedback_detail,
)
from farmafacil.services.chat_debug import build_debug_footer, get_user_stats
from farmafacil.services.settings import get_setting, resolve_chat_debug, resolve_response_mode
from farmafacil.services.store_backfill import format_store_info, lookup_store
from farmafacil.services.users import (
    get_or_create_user,
    increment_token_usage,
    set_onboarding_step,
    update_last_search,
    update_user_location,
    update_user_name,
    update_user_preference,
    validate_user_profile,
)

logger = logging.getLogger(__name__)

# ── Messages ────────────────────────────────────────────────────────────

MSG_WELCOME = (
    "\U0001f48a *Hola! Soy FarmaFacil*\n\n"
    "Te ayudo a encontrar medicamentos en farmacias de Venezuela.\n\n"
    "*Como te llamas?*"
)

MSG_ASK_LOCATION = (
    "Mucho gusto *{name}*! \U0001f91d\n\n"
    "*En que zona o barrio estas?*\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_ASK_PREFERENCE = (
    "\u2705 *{zone}* guardado!\n\n"
    "Como prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande* — un producto a la vez con detalles\n"
    "*2.* \U0001f5bc *Galeria* — varios productos en una imagen\n\n"
    "Responde *1* o *2*"
)

MSG_READY = (
    "\u2705 Listo *{name}*! Ya estas configurado.\n\n"
    "Enviame el nombre de un medicamento.\n"
    "Ejemplo: _losartan_ o _acetaminofen_"
)

MSG_LOCATION_NOT_FOUND = (
    "No logre ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanizacion.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

MSG_INVALID_PREFERENCE = "Responde *1* para imagen grande o *2* para galeria."

MSG_RETURNING = (
    "\U0001f48a *Hola {name}!* Buscando en *{zone}* (_{pref}_).\n\n"
    "Enviame el nombre de un medicamento.\n\n"
    "\U0001f527 _Comandos:_\n"
    "\u2022 _cambiar zona_ — nueva ubicacion\n"
    "\u2022 _cambiar preferencia_ — modo de visualizacion\n"
    "\u2022 _cambiar nombre_ — actualizar nombre\n"
    "\u2022 _ayuda_ — instrucciones"
)

MSG_ASK_NEW_LOCATION = "Dime tu nueva zona o barrio.\nEjemplo: _La Boyera_, _Chacao_, _Maracaibo_"
MSG_ASK_NEW_PREFERENCE = (
    "Como prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande*\n*2.* \U0001f5bc *Galeria*\n\nResponde *1* o *2*"
)
MSG_ASK_NEW_NAME = "Como te llamas?"

MSG_ASK_FEEDBACK = "\u00bfTe sirvi\u00f3? (s\u00ed/no)"
MSG_FEEDBACK_THANKS = "\u00a1Gracias por tu respuesta! \U0001f44d"
MSG_FEEDBACK_SORRY = "Lamento eso. \u00bfQu\u00e9 buscabas exactamente o qu\u00e9 estuvo mal?"
MSG_FEEDBACK_DETAIL_THANKS = "Gracias por explicarnos. Vamos a mejorar. \U0001f4aa"

MSG_NEED_LOCATION = (
    "{name}, necesito saber tu ubicacion para buscarte farmacias cercanas.\n\n"
    "*En que zona o barrio estas?*\nEjemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

# ── Change command keywords ─────────────────────────────────────────────

from farmafacil.services.intent import _get_keyword_cache


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message with smart profile detection.

    The bot extracts name, location, and drug queries from ANY message,
    filling in the user profile progressively instead of forcing a rigid wizard.
    """
    text = message_text.strip()
    if not text:
        return

    user = await get_or_create_user(sender)
    user = await validate_user_profile(user)
    step = user.onboarding_step
    text_lower = text.lower()

    # ── Rigid onboarding steps (only when explicitly waiting for input) ──

    if step == "welcome":
        await set_onboarding_step(sender, "awaiting_name")
        await send_text_message(sender, MSG_WELCOME)
        return

    if step == "awaiting_name":
        # Always use AI here to distinguish greetings from actual names
        ai_result = await classify_with_ai(text, user.id, user.name or "")
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens)

        # If it's just a greeting (hi, hola), re-ask for name
        if ai_result.action == "greeting" and not ai_result.detected_name:
            await send_text_message(
                sender,
                "\U0001f60a Hola! Dime tu nombre para poder atenderte mejor.\n\n"
                "Ejemplo: _Maria_, _Jose_, _Carlos_"
            )
            return

        name = ai_result.detected_name or text.strip().title()

        # Validate name — reject common non-names
        if not _is_valid_name(name):
            await send_text_message(
                sender,
                "No logre entender tu nombre. Dime solo tu nombre, por favor.\n\n"
                "Ejemplo: _Maria_, _Jose_, _Carlos_"
            )
            return

        user = await update_user_name(sender, name)

        # Did they also mention a location?
        if ai_result.detected_location:
            location = await geocode_zone(ai_result.detected_location)
            if location:
                user = await update_user_location(
                    sender, location["lat"], location["lng"],
                    location["zone_name"], location["city"],
                )
                # Skip to preference
                await send_text_message(
                    sender, MSG_ASK_PREFERENCE.format(zone=user.zone_name)
                )
                return

        await send_text_message(sender, MSG_ASK_LOCATION.format(name=user.name))
        return

    if step == "awaiting_location":
        # Try to geocode — but also check if AI can extract location
        intent = await classify_intent(text, user.id, user.name or "")
        location_text = intent.detected_location or text

        location = await geocode_zone(location_text)
        if location:
            user = await update_user_location(
                sender, location["lat"], location["lng"],
                location["zone_name"], location["city"],
            )
            await send_text_message(
                sender, MSG_ASK_PREFERENCE.format(zone=user.zone_name)
            )
        else:
            await send_text_message(sender, MSG_LOCATION_NOT_FOUND)
        return

    if step == "awaiting_preference":
        pref = _parse_preference(text_lower)
        if pref:
            user = await update_user_preference(sender, pref)
            await send_text_message(
                sender, MSG_READY.format(name=user.name or "amigo")
            )
        else:
            await send_text_message(sender, MSG_INVALID_PREFERENCE)
        return

    if step == "awaiting_feedback":
        feedback = parse_feedback(text)
        if feedback == "yes":
            if user.last_search_log_id:
                await record_feedback(user.last_search_log_id, "yes")
            await set_onboarding_step(sender, None)
            await send_text_message(sender, MSG_FEEDBACK_THANKS)
        elif feedback == "no":
            if user.last_search_log_id:
                await record_feedback(user.last_search_log_id, "no")
            await set_onboarding_step(sender, "awaiting_feedback_detail")
            await send_text_message(sender, MSG_FEEDBACK_SORRY)
        else:
            # Not a feedback response — clear step and process normally
            await set_onboarding_step(sender, None)
            # Fall through to normal message handling below
            step = None
        if feedback is not None:
            return

    if step == "awaiting_feedback_detail":
        if user.last_search_log_id:
            await record_feedback_detail(user.last_search_log_id, text)
        await set_onboarding_step(sender, None)
        await send_text_message(sender, MSG_FEEDBACK_DETAIL_THANKS)
        return

    # ── Onboarding complete — smart conversational flow ─────────────────

    # Resolve response mode and debug mode: user override → global setting
    global_mode = await get_setting("response_mode")
    global_debug = await get_setting("chat_debug")
    mode = resolve_response_mode(user.response_mode, global_mode)
    debug_on = resolve_chat_debug(user.chat_debug, global_debug)
    display_name = user.name or "amigo"

    # AI-only mode — bypass keyword routing, send everything to AI
    if mode == "ai_only":
        logger.info("AI-only mode for %s — routing to AI classifier", sender)
        ai_result = await classify_with_ai(text, user.id, display_name)
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens)
        logger.info("AI classify (action=%s) for '%s'", ai_result.action, text[:50])

        # If AI detects a drug search, perform it
        if ai_result.action == "drug_search" and ai_result.drug_query:
            if not user.latitude:
                await set_onboarding_step(sender, "awaiting_location")
                await send_text_message(
                    sender, MSG_NEED_LOCATION.format(name=display_name)
                )
                return
            await _handle_drug_search(
                sender, user, ai_result.drug_query, display_name,
                debug_on=debug_on, ai_result=ai_result,
            )
            return

        # For all other actions, generate a full AI response
        if ai_result.text:
            reply = ai_result.text
            tokens_ai = ai_result
        else:
            full_result = await generate_response(text, user.id, display_name)
            await increment_token_usage(user.id, full_result.input_tokens, full_result.output_tokens)
            reply = full_result.text
            tokens_ai = full_result

        if debug_on:
            reply += await _build_debug(sender, user.id, tokens_ai)
        await send_text_message(sender, reply)
        return

    # ── Hybrid mode — check keywords first, AI fallback ───────────────

    # Check change commands via DB keywords (before LLM call)
    cache = await _get_keyword_cache()
    if text_lower in cache:
        action, response = cache[text_lower]
        if action == "location_change":
            await set_onboarding_step(sender, "awaiting_location")
            await send_text_message(sender, MSG_ASK_NEW_LOCATION)
            return
        if action == "preference_change":
            await set_onboarding_step(sender, "awaiting_preference")
            await send_text_message(sender, MSG_ASK_NEW_PREFERENCE)
            return
        if action == "name_change":
            await set_onboarding_step(sender, "awaiting_name")
            await send_text_message(sender, MSG_ASK_NEW_NAME)
            return
        if action == "farewell" and response:
            await send_text_message(sender, response)
            return
        if action == "view_similar":
            await _handle_view_similar(sender, user)
            return

    # Classify intent (keywords first, AI fallback)
    intent = await classify_intent(text, user.id, user.name or "")
    await increment_token_usage(user.id, intent.input_tokens, intent.output_tokens)

    # Auto-update profile if LLM detected new info
    if intent.detected_name and intent.detected_name != user.name:
        user = await update_user_name(sender, intent.detected_name)
        # Reset onboarding_step to None since they're already onboarded
        await set_onboarding_step(sender, None)
        user = await get_or_create_user(sender)
        display_name = user.name

    if intent.detected_location and intent.detected_location.lower() != (user.zone_name or "").lower():
        location = await geocode_zone(intent.detected_location)
        if location:
            user = await update_user_location(
                sender, location["lat"], location["lng"],
                location["zone_name"], location["city"],
            )
            await set_onboarding_step(sender, None)
            user = await get_or_create_user(sender)

    # Route by action
    if intent.action == "greeting":
        pref_label = "galeria" if user.display_preference == "grid" else "imagen grande"
        await send_text_message(
            sender,
            MSG_RETURNING.format(name=display_name, zone=user.zone_name, pref=pref_label),
        )

    elif intent.action == "help":
        await send_text_message(sender, HELP_MESSAGE)

    elif intent.action == "drug_search":
        # Make sure we have location before searching
        if not user.latitude:
            await set_onboarding_step(sender, "awaiting_location")
            await send_text_message(
                sender, MSG_NEED_LOCATION.format(name=display_name)
            )
            return

        query = intent.drug_query or text
        await _handle_drug_search(sender, user, query, display_name, debug_on=debug_on)

    elif intent.action == "question":
        # Check if the question is about a pharmacy store
        store = await _try_store_lookup(text)
        if store:
            await send_text_message(sender, format_store_info(store))
        else:
            # Use AI responder for complex questions
            ai_result = await generate_response(text, user.id, display_name)
            await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens)
            logger.info("AI response (role=%s) for '%s'", ai_result.role_used, text[:50])
            reply = ai_result.text
            if debug_on:
                reply += await _build_debug(sender, user.id, ai_result)
            await send_text_message(sender, reply)

    else:
        # Unknown intent — try AI responder before giving up
        ai_result = await generate_response(text, user.id, display_name)
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens)
        logger.info("AI fallback (role=%s) for '%s'", ai_result.role_used, text[:50])
        reply = ai_result.text
        if debug_on:
            reply += await _build_debug(sender, user.id, ai_result)
        await send_text_message(sender, reply)


async def _handle_drug_search(
    sender: str,
    user,
    query: str,
    display_name: str,
    debug_on: bool = False,
    ai_result=None,
) -> None:
    """Perform a drug search and send results to the user.

    After showing results, asks for feedback: "¿Te sirvió? (sí/no)".

    Args:
        sender: WhatsApp phone number.
        user: User record with location and preferences.
        query: Drug name or search term.
        display_name: User's display name.
        debug_on: Whether to append debug footer.
        ai_result: AiResponse from classification (for token stats).
    """
    logger.info("Drug search from %s/%s (%s): '%s'", sender, display_name, user.zone_name, query)
    response = await search_drug(
        query=query,
        city_code=user.city_code,
        latitude=user.latitude,
        longitude=user.longitude,
        zone_name=user.zone_name,
    )

    # Log the search and save the log ID for feedback tracking
    results_count = len(response.results) if response.results else 0
    search_log_id = await log_search(user.id, query, results_count)
    await update_last_search(sender, query, search_log_id)

    # Send grid/detail image FIRST, then text summary below
    if response.results:
        if user.display_preference == "detail":
            await _send_detail_images(sender, response.results)
        else:
            await _send_grid_image(sender, response)

    reply = format_search_results(response)
    if debug_on:
        reply += await _build_debug(sender, user.id, ai_result)
    await send_text_message(sender, reply)

    # Ask for feedback
    await set_onboarding_step(sender, "awaiting_feedback")
    await send_text_message(sender, MSG_ASK_FEEDBACK)


async def _try_store_lookup(text: str) -> object | None:
    """Try to find a pharmacy store name mentioned in the text.

    Checks for patterns like "donde queda TEPUY", "TEPUY", "farmacia TEPUY".
    """
    # Extract potential store name from common patterns
    text_lower = text.lower().strip()
    # Remove common question words
    for prefix in ("donde queda ", "donde esta ", "donde está ", "direccion de ",
                    "dirección de ", "ubicacion de ", "ubicación de ", "farmacia "):
        if text_lower.startswith(prefix):
            store_name = text_lower[len(prefix):].strip().rstrip("?")
            store = await lookup_store(store_name)
            if store:
                return store

    # Try the whole text as a store name (user might just type "TEPUY")
    # Only if it's short (1-2 words) to avoid false matches
    words = text_lower.split()
    if len(words) <= 2:
        store = await lookup_store(text_lower.rstrip("?"))
        if store:
            return store

    return None


_NOT_NAMES = {
    "hi", "hello", "hey", "hola", "buenas", "buenos", "ola", "que tal",
    "good", "ok", "si", "no", "yes", "gracias", "thanks", "bye", "chao",
    "ayuda", "help", "losartan", "acetaminofen", "ibuprofeno", "1", "2",
}


def _is_valid_name(name: str) -> bool:
    """Check if a string looks like a real person's name."""
    if not name or len(name.strip()) < 2:
        return False
    if name.lower().strip() in _NOT_NAMES:
        return False
    # Reject if it's all digits or has special characters
    stripped = name.strip()
    if stripped.isdigit():
        return False
    # Reject very long "names" (likely a sentence)
    if len(stripped.split()) > 4:
        return False
    return True


def _parse_preference(text: str) -> str | None:
    """Parse user's display preference response."""
    if text in ("1", "imagen grande", "imagen", "grande", "detalle", "detail"):
        return "detail"
    if text in ("2", "galeria", "galería", "grid", "grilla", "varios"):
        return "grid"
    return None


async def _send_detail_images(sender: str, results: list[DrugResult]) -> None:
    """Send individual product images with rich captions (top 3)."""
    for result in results[:3]:
        if result.image_url:
            caption = _build_product_caption(result)
            await send_image_message(sender, result.image_url, caption)


async def _send_grid_image(sender: str, response) -> None:
    """Generate and send a product grid image."""
    grid_path = await generate_product_grid(response.results)
    if grid_path:
        caption = f"Resultados para *{response.query}*"
        if response.zone:
            caption += f" cerca de *{response.zone}*"
        await send_local_image(sender, grid_path, caption)
        os.unlink(grid_path)


def _build_product_caption(result: DrugResult) -> str:
    """Build a Farmatodo-style product card caption for WhatsApp."""
    lines = []
    if result.discount_pct:
        lines.append(f"\U0001f7e2 *{result.discount_pct} DCTO*")
    if result.brand:
        lines.append(f"_{result.brand}_")
    lines.append(f"*{result.drug_name}*")
    if result.price_bs is not None:
        price_line = f"*Bs. {result.price_bs:,.2f}*"
        if result.full_price_bs and result.full_price_bs != result.price_bs:
            price_line += f"  ~Bs. {result.full_price_bs:,.2f}~"
        lines.append(price_line)
    if result.unit_label:
        lines.append(result.unit_label)
    if result.requires_prescription:
        lines.append("\U0001f4cb Requiere receta")
    if result.stores_in_stock > 0:
        lines.append(f"\u2705 Disponible en {result.stores_in_stock} tiendas")
    if result.nearby_stores:
        closest = result.nearby_stores[0]
        lines.append(f"\U0001f4cd {closest.store_name} — {closest.distance_km:.1f} km")
    return "\n".join(lines)


async def _build_debug(sender: str, user_id: int, ai_result=None) -> str:
    """Build a debug footer from AI response and user stats.

    Args:
        sender: WhatsApp phone number.
        user_id: User database ID.
        ai_result: AiResponse with role_used and token counts (optional).

    Returns:
        Formatted debug footer string.
    """
    stats = await get_user_stats(sender, user_id)
    role = getattr(ai_result, "role_used", "keyword") if ai_result else "keyword"
    in_tok = getattr(ai_result, "input_tokens", 0) if ai_result else 0
    out_tok = getattr(ai_result, "output_tokens", 0) if ai_result else 0
    return build_debug_footer(
        role_used=role,
        input_tokens=in_tok,
        output_tokens=out_tok,
        total_questions=stats["total_questions"],
        total_success=stats["total_success"],
        total_tokens_in=stats["total_tokens_in"],
        total_tokens_out=stats["total_tokens_out"],
    )


async def _handle_view_similar(sender: str, user) -> None:
    """Handle 'ver similares' command — re-run last search without exact filtering.

    Loads the user's last search query and re-runs it with show_all=True
    to show all product variants instead of just the exact match.

    Args:
        sender: WhatsApp phone number.
        user: User record with last_search_query.
    """
    if not user.last_search_query:
        await send_text_message(
            sender,
            "No tienes una busqueda reciente. Enviame el nombre de un medicamento.",
        )
        return

    display_name = user.name or "amigo"
    query = user.last_search_query

    if not user.latitude:
        await send_text_message(
            sender, MSG_NEED_LOCATION.format(name=display_name)
        )
        return

    logger.info(
        "View similar from %s/%s: '%s'", sender, display_name, query
    )
    response = await search_drug(
        query=query,
        city_code=user.city_code,
        latitude=user.latitude,
        longitude=user.longitude,
        zone_name=user.zone_name,
        show_all=True,
    )

    if response.results:
        if user.display_preference == "detail":
            await _send_detail_images(sender, response.results)
        else:
            await _send_grid_image(sender, response)

    reply = format_search_results(response)
    await send_text_message(sender, reply)
