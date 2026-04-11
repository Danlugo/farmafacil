"""Handle incoming WhatsApp messages — smart conversational flow."""

import asyncio
import logging
import os

from sqlalchemy.exc import SQLAlchemyError

from farmafacil.bot.formatter import format_nearby_stores, format_search_results

from farmafacil.bot.whatsapp import (
    send_image_message,
    send_interactive_list,
    send_local_image,
    send_read_receipt,
    send_text_message,
)
from farmafacil.models.schemas import DrugResult
from farmafacil.services.ai_responder import classify_with_ai, generate_response, refine_clarified_query
from farmafacil.services.user_memory import auto_update_memory, get_memory
from farmafacil.services.geocode import geocode_zone, reverse_geocode
from farmafacil.services.image_grid import generate_product_grid
from farmafacil.services.intent import HELP_MESSAGE, _get_keyword_cache, classify_intent
from farmafacil.services.search import ACTIVE_SCRAPERS, search_drug
from farmafacil.services.search_feedback import (
    log_search,
    parse_feedback,
    record_feedback,
    record_feedback_detail,
)
from farmafacil.config import LLM_MODEL
from farmafacil.services.chat_debug import build_debug_footer, estimate_cost, get_user_stats
from farmafacil.services.drug_interactions import (
    check_interactions,
    extract_medications_from_memory,
    format_interaction_warning,
)
from farmafacil.services.settings import get_setting, resolve_chat_debug, resolve_response_mode
from farmafacil.services.store_backfill import format_store_info, lookup_store
from farmafacil.services.store_locations import get_all_nearby_stores
from farmafacil.services.user_feedback import create_feedback, parse_feedback_command
from farmafacil.services.users import (
    get_or_create_user,
    increment_token_usage,
    set_awaiting_category_search,
    set_awaiting_clarification,
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
    "Te ayudo a encontrar productos en farmacias de Venezuela.\n\n"
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
    "Enviame el nombre de un producto de farmacia.\n"
    "Ejemplo: _losartan_, _protector solar_, _vitamina C_"
)

MSG_LOCATION_NOT_FOUND = (
    "No logre ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanizacion.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

# Shown when a user shares a GPS location pin that cannot be reverse-geocoded
# (unreachable Nominatim, coordinates outside Venezuela, malformed response).
# Added in v0.13.0 (Item 24) — users can fall back to typing a city name.
MSG_LOCATION_PIN_NOT_FOUND = (
    "No pude ubicar las coordenadas que compartiste.\n"
    "Por favor, envia el *nombre de tu zona o barrio* por texto.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

MSG_INVALID_PREFERENCE = "Responde *1* para imagen grande o *2* para galeria."

MSG_RETURNING = (
    "\U0001f48a *Hola {name}!* Buscando en *{zone}* (_{pref}_).\n\n"
    "Enviame el nombre de un producto de farmacia.\n\n"
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

MSG_FEEDBACK_EMPTY = (
    "Por favor incluye tu {label} despu\u00e9s del comando.\n\n"
    "Ejemplo: _/{cmd} la b\u00fasqueda de losartan no me devolvi\u00f3 resultados_"
)
MSG_FEEDBACK_REGISTERED = (
    "\u2705 \u00a1Gracias! Tu {label} ha sido registrado.\n\n"
    "\U0001f4cb *Caso #{case_id}*\n\n"
    "Nuestro equipo lo revisar\u00e1 pronto."
)
MSG_FEEDBACK_ERROR = (
    "Lo siento, no pude registrar tu comentario en este momento. "
    "Por favor int\u00e9ntalo de nuevo en unos minutos."
)

MSG_NEED_LOCATION = (
    "{name}, necesito saber tu ubicacion para buscarte farmacias cercanas.\n\n"
    "*En que zona o barrio estas?*\nEjemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_CLARIFY_CANCELED = (
    "Listo, cancele la busqueda anterior. Dime que necesitas \U0001f64c"
)

# Shown instead of the ¿Te sirvió? prompt when a drug search returns zero
# results. Asking "did it help?" when there is literally nothing to rate was
# a UX confusion signal in the v0.12.3 prod test (Item 34).
MSG_RETRY_DIFFERENT_NAME = (
    "\U0001f4a1 Si no encontraste lo que buscabas, prueba con otro nombre "
    "o el principio activo. Ejemplo: _acetaminofen_ en vez de _tachipirin_."
)

# Words that cancel a pending clarification and reset the state.
_CLARIFY_CANCEL_WORDS = {
    "cancelar", "cancela", "cancel", "olvidalo", "olvídalo", "nada",
    "dejalo", "déjalo", "no", "ninguno", "ninguna",
}

# ── Category quick-reply menu (Item 29, v0.13.2) ────────────────────────
# Shown as a WhatsApp interactive list when an onboarded user sends a bare
# greeting. Each tuple is (reply_id, display_title). Reply IDs are stable
# machine identifiers — the title is what the user sees in the list AND
# what we echo back in the follow-up prompt.
#
# Category set chosen after Item 29 product review (2026-04-11):
# kept Medicamentos + Cuidado Personal (core pharmacy volume), added
# Belleza (high-margin), Alimentos (Farmatodo sells food), and
# Artículos Hogar (pharmacy-adjacent). Dropped Higiene (overlap with
# Cuidado Personal) and Equipos Ortopédicos (too niche). See MEMORY.md.
CATEGORIES: list[tuple[str, str]] = [
    ("cat_medicamentos", "Medicamentos"),
    ("cat_cuidado_personal", "Cuidado Personal"),
    ("cat_belleza", "Belleza"),
    ("cat_alimentos", "Alimentos"),
    ("cat_hogar", "Articulos Hogar"),
]
_CATEGORY_BY_ID: dict[str, str] = {cat_id: title for cat_id, title in CATEGORIES}

MSG_CATEGORY_LIST_BODY = (
    "\U0001f48a Hola *{name}*! \u00bfQu\u00e9 estas buscando hoy?\n\n"
    "Elegi una categoria o escribi directamente el nombre de un producto."
)
MSG_CATEGORY_LIST_BUTTON = "Ver categorias"
MSG_CATEGORY_LIST_HEADER = "FarmaFacil"
MSG_CATEGORY_LIST_FOOTER = "Podes cancelar en cualquier momento"

MSG_CATEGORY_PROMPT = (
    "\U0001f6cd *{category}* - \u00bfQue producto buscas?\n\n"
    "Escribi el nombre y lo busco para vos.\n"
    "Ejemplo: _losartan_, _shampoo_, _vitamina C_"
)
MSG_CATEGORY_CANCELED = (
    "Listo, cancele la busqueda por categoria. Dime que necesitas \U0001f64c"
)

async def _update_memory_safe(
    user_id: int, user_name: str, user_message: str, bot_response: str,
) -> None:
    """Non-blocking memory update — errors are logged, never raised."""
    try:
        await auto_update_memory(user_id, user_name, user_message, bot_response)
    except Exception:
        # Last-resort catch: memory update is a background enhancement and
        # must NEVER propagate errors to the WhatsApp reply path. Specific
        # error types are already logged one layer down in
        # ``user_memory.auto_update_memory`` itself.
        logger.error("Memory update failed (non-blocking)", exc_info=True)


async def handle_location_message(
    sender: str,
    latitude: float,
    longitude: float,
    wa_message_id: str = "",
) -> None:
    """Handle an inbound WhatsApp location pin share.

    Users can share their GPS location instead of typing a zone name.
    Added in v0.13.0 (Item 24). Behaviour:

    - If the user is onboarding (awaiting_name / awaiting_location / welcome
      / no profile yet), reverse-geocode the coordinates and advance
      onboarding to the preference step. Name may still be missing — in
      that case we update location first and then re-ask for the name.
    - If the user is already onboarded, treat this as a "cambiar zona"
      — persist the new location and acknowledge the change without
      resetting any other profile fields.
    - If reverse-geocoding fails or the coordinates are outside
      Venezuela, send ``MSG_LOCATION_PIN_NOT_FOUND`` and leave the user's
      current state untouched so they can recover by typing a city.

    Args:
        sender: The WhatsApp phone number of the sender.
        latitude: Latitude from the WhatsApp location payload.
        longitude: Longitude from the WhatsApp location payload.
        wa_message_id: The WhatsApp message ID (for read receipts).
    """
    user = await get_or_create_user(sender)
    user = await validate_user_profile(user)

    # Blue checks + typing bubble (fire-and-forget)
    if wa_message_id:
        asyncio.create_task(send_read_receipt(sender, wa_message_id))

    # Snapshot the prior onboarding step BEFORE we update the location —
    # ``update_user_location`` unconditionally sets step to
    # ``awaiting_preference``, so we use the pre-update value to decide
    # whether this user is still onboarding or already fully onboarded.
    # ``display_preference`` has a non-nullable default of ``"grid"`` so
    # it cannot be used as an "already onboarded" signal.
    prior_step = user.onboarding_step

    location = await reverse_geocode(latitude, longitude)
    if not location:
        await send_text_message(sender, MSG_LOCATION_PIN_NOT_FOUND)
        return

    user = await update_user_location(
        sender,
        location["lat"],
        location["lng"],
        location["zone_name"],
        location["city"],
    )

    logger.info(
        "Location pin accepted from %s: %s (%s) — prior step=%s",
        sender, user.zone_name, user.city_code, prior_step,
    )

    # If the user hasn't told us their name yet, location came before
    # name — save location, then loop back to asking for the name so
    # onboarding doesn't skip that step.
    if not user.name:
        await set_onboarding_step(sender, "awaiting_name")
        await send_text_message(
            sender,
            f"\u2705 *{user.zone_name}* guardado!\n\n*Como te llamas?*",
        )
        return

    # Still onboarding? (prior step was welcome / awaiting_name /
    # awaiting_location). Continue onboarding by asking for a display
    # preference — ``update_user_location`` already advanced the step.
    if prior_step is not None:
        await send_text_message(
            sender, MSG_ASK_PREFERENCE.format(zone=user.zone_name),
        )
        return

    # Already fully onboarded (prior step was None) — this is a
    # "cambiar zona" — acknowledge the updated zone and clear the step
    # that ``update_user_location`` set.
    await set_onboarding_step(sender, None)
    await send_text_message(
        sender,
        f"\u2705 Zona actualizada a *{user.zone_name}*. "
        "Ahora buscare farmacias cerca de ti.",
    )


async def _send_category_list(sender: str, display_name: str) -> None:
    """Send the category quick-reply list message (Item 29, v0.13.2).

    Wraps the WhatsApp interactive-list payload so the greeting branch in
    ``handle_incoming_message`` stays readable. Uses the ``CATEGORIES``
    constant as the source of truth — to add/remove a category, edit the
    tuple list above.

    Args:
        sender: Recipient WhatsApp phone number.
        display_name: User's name for the body greeting.
    """
    rows = [
        {"id": cat_id, "title": title}
        for cat_id, title in CATEGORIES
    ]
    await send_interactive_list(
        to=sender,
        body=MSG_CATEGORY_LIST_BODY.format(name=display_name),
        button=MSG_CATEGORY_LIST_BUTTON,
        rows=rows,
        header=MSG_CATEGORY_LIST_HEADER,
        footer=MSG_CATEGORY_LIST_FOOTER,
        section_title="Categorias",
    )


async def handle_list_reply(
    sender: str, reply_id: str, wa_message_id: str = "",
) -> None:
    """Handle a WhatsApp interactive list reply (Item 29, v0.13.2).

    Fired when a user taps a row in an interactive list message. Currently
    only category rows from the greeting menu are supported — unknown reply
    IDs are logged and ignored so a stale or malformed payload never crashes
    the webhook.

    Args:
        sender: WhatsApp phone number of the sender.
        reply_id: The ``id`` field from the list reply payload.
        wa_message_id: The WhatsApp message ID (for read receipts).
    """
    user = await get_or_create_user(sender)
    user = await validate_user_profile(user)

    if wa_message_id:
        asyncio.create_task(send_read_receipt(sender, wa_message_id))

    category = _CATEGORY_BY_ID.get(reply_id)
    if category is None:
        logger.warning(
            "Unknown list_reply id from %s: %r (ignoring)", sender, reply_id,
        )
        return

    # Stash the picked category and prompt the user for a concrete product.
    # The next free-text message is routed through the normal drug-search
    # pipeline in handle_incoming_message.
    await set_awaiting_category_search(sender, category)
    await send_text_message(
        sender, MSG_CATEGORY_PROMPT.format(category=category),
    )
    logger.info(
        "Category menu pick from %s: %s (awaiting_category_search set)",
        sender, category,
    )


async def handle_incoming_message(
    sender: str, message_text: str, wa_message_id: str = "",
) -> None:
    """Process an incoming WhatsApp message with smart profile detection.

    The bot extracts name, location, and drug queries from ANY message,
    filling in the user profile progressively instead of forcing a rigid wizard.

    Args:
        sender: The WhatsApp phone number of the sender.
        message_text: The message text.
        wa_message_id: The WhatsApp message ID (for read receipts).
    """
    text = message_text.strip()
    if not text:
        return

    user = await get_or_create_user(sender)
    user = await validate_user_profile(user)
    step = user.onboarding_step
    text_lower = text.lower()

    # Send read receipt (fire-and-forget) — shows blue checks + typing bubble
    if wa_message_id:
        asyncio.create_task(send_read_receipt(sender, wa_message_id))

    # ── Feedback commands (/bug, /comentario) — intercept before anything ──
    # Intercepted here so users can report issues even if they're stuck in
    # an onboarding state (e.g., awaiting_feedback with a confused prompt).
    feedback_cmd = parse_feedback_command(text)
    if feedback_cmd is not None:
        feedback_type, body = feedback_cmd
        label = "reporte" if feedback_type == "bug" else "comentario"

        # If the user is stuck in a feedback-related onboarding state, clear
        # it BEFORE we do anything else — the `/bug` command is an escape
        # hatch and must release the state even if the feedback save fails
        # or the body is empty.
        if step in ("awaiting_feedback", "awaiting_feedback_detail"):
            await set_onboarding_step(sender, None)

        if not body:
            await send_text_message(
                sender,
                MSG_FEEDBACK_EMPTY.format(label=label, cmd=feedback_type),
            )
            return
        try:
            case_id = await create_feedback(
                user_id=user.id,
                feedback_type=feedback_type,
                message=body,
                phone_number=sender,
            )
        except ValueError as exc:
            logger.warning("Invalid feedback submission from %s: %s", sender, exc)
            await send_text_message(sender, MSG_FEEDBACK_ERROR)
            return
        except SQLAlchemyError:
            logger.error(
                "Failed to create feedback case (DB error)", exc_info=True,
            )
            await send_text_message(sender, MSG_FEEDBACK_ERROR)
            return
        except Exception:
            # Last-resort: /bug is an escape-hatch command — we already
            # cleared the user's stuck state above, so even an unexpected
            # error must reply with the error message (not leave the user
            # hanging).
            logger.error(
                "Failed to create feedback case (unexpected error)",
                exc_info=True,
            )
            await send_text_message(sender, MSG_FEEDBACK_ERROR)
            return
        await send_text_message(
            sender,
            MSG_FEEDBACK_REGISTERED.format(label=label, case_id=case_id),
        )
        return

    # ── Clarification reply handling ────────────────────────────────────
    # If the bot previously asked a clarifying question for a vague query
    # (e.g., "memory medicines" → "pills or drinks?"), the NEXT message
    # from the user is the answer. We merge the original context with the
    # answer into a refined query and dispatch it as a direct drug search.
    # This runs AFTER the /bug escape hatch but BEFORE onboarding / intent
    # routing so the user is always free to cancel or report a bug first.
    if user.awaiting_clarification_context and step is None:
        pending_context = user.awaiting_clarification_context
        # Escape hatch — user wants to abandon the clarification.
        if text_lower in _CLARIFY_CANCEL_WORDS:
            await set_awaiting_clarification(sender, None)
            await send_text_message(sender, MSG_CLARIFY_CANCELED)
            return
        # Clear the pending context BEFORE dispatching so that a failure
        # downstream cannot leave the user trapped in the clarify state.
        await set_awaiting_clarification(sender, None)
        # Ask Claude Haiku to distill the vague context + the user's answer
        # into a concrete 2-5 word search term. Without this step the scraper
        # would receive a 15-word natural-language sentence that no product
        # catalog can match (regression from v0.12.3, fixed in v0.12.4).
        refined_query, r_in, r_out = await refine_clarified_query(
            pending_context, text,
        )
        if r_in or r_out:
            await increment_token_usage(user.id, r_in, r_out, model=LLM_MODEL)
        logger.info(
            "Clarification refinement for %s: %r + %r -> %r",
            sender, pending_context, text, refined_query,
        )
        if not user.latitude:
            await set_onboarding_step(sender, "awaiting_location")
            await send_text_message(
                sender, MSG_NEED_LOCATION.format(name=user.name or "amigo")
            )
            return
        await _handle_drug_search(
            sender, user, refined_query, user.name or "amigo",
            debug_on=resolve_chat_debug(
                user.chat_debug, await get_setting("chat_debug")
            ),
        )
        # Remember the chosen preference so we don't ask again next time.
        await _update_memory_safe(
            user.id, user.name or "amigo",
            f"{pending_context} (clarified: {text})",
            f"User specified preference: {text}",
        )
        return

    # ── Category menu follow-up (Item 29, v0.13.2) ──────────────────────
    # If the user picked a category from the greeting list, their next
    # free-text message is the product name. Merge state -> dispatch -> clear.
    # Runs AFTER /bug + clarification so those escape hatches still work,
    # and BEFORE onboarding so it only fires for fully-onboarded users.
    if user.awaiting_category_search and step is None:
        pending_category = user.awaiting_category_search
        # Escape hatch — user wants to abandon the category search.
        if text_lower in _CLARIFY_CANCEL_WORDS:
            await set_awaiting_category_search(sender, None)
            await send_text_message(sender, MSG_CATEGORY_CANCELED)
            return
        # Clear the stash BEFORE dispatching so a downstream failure cannot
        # leave the user trapped in the category-search state (fail-safe
        # pattern from Item 31).
        await set_awaiting_category_search(sender, None)
        logger.info(
            "Category freeform from %s: category=%r query=%r",
            sender, pending_category, text,
        )
        if not user.latitude:
            await set_onboarding_step(sender, "awaiting_location")
            await send_text_message(
                sender, MSG_NEED_LOCATION.format(name=user.name or "amigo"),
            )
            return
        await _handle_drug_search(
            sender, user, text, user.name or "amigo",
            debug_on=resolve_chat_debug(
                user.chat_debug, await get_setting("chat_debug"),
            ),
        )
        await _update_memory_safe(
            user.id, user.name or "amigo",
            f"[{pending_category}] {text}",
            f"User searched within category: {pending_category}",
        )
        return

    # ── Rigid onboarding steps (only when explicitly waiting for input) ──

    if step == "welcome":
        await set_onboarding_step(sender, "awaiting_name")
        await send_text_message(sender, MSG_WELCOME)
        return

    if step == "awaiting_name":
        # Always use AI here to distinguish greetings from actual names
        ai_result = await classify_with_ai(text, user.id, user.name or "")
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens, model=LLM_MODEL)

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

    # ── Special commands (available in all modes) ──────────────────────
    if text_lower == "/stats":
        if debug_on:
            stats = await get_user_stats(sender, user.id)
            from farmafacil import __version__
            from farmafacil.services.chat_debug import estimate_cost_breakdown
            last_cost = estimate_cost(
                stats["last_tokens_in"], stats["last_tokens_out"], LLM_MODEL
            )
            user_costs = estimate_cost_breakdown(stats)
            g_stats = {
                "tokens_in_haiku": stats["global_tokens_in_haiku"],
                "tokens_out_haiku": stats["global_tokens_out_haiku"],
                "tokens_in_sonnet": stats["global_tokens_in_sonnet"],
                "tokens_out_sonnet": stats["global_tokens_out_sonnet"],
            }
            global_costs = estimate_cost_breakdown(g_stats)
            msg = (
                "\U0001f4ca *FarmaFacil Stats*\n\n"
                f"app version: *{__version__}*\n"
                f"ai model: *{LLM_MODEL}*\n\n"
                f"\U0001f464 *Mi cuenta ({display_name}):*\n"
                f"  preguntas: {stats['total_questions']}\n"
                f"  busquedas exitosas: {stats['total_success']}\n\n"
                f"  _Ultima llamada:_\n"
                f"  tokens: {stats['last_tokens_in']} in / {stats['last_tokens_out']} out\n"
                f"  est costo: ${last_cost:.4f}\n\n"
                f"  _Acumulado:_\n"
                f"  tokens: {stats['total_tokens_in']} in / {stats['total_tokens_out']} out\n"
                f"  haiku: {stats['calls_haiku']} calls, ${user_costs['cost_haiku']:.4f}\n"
                f"  sonnet: {stats['calls_sonnet']} calls, ${user_costs['cost_sonnet']:.4f}\n"
                f"  est costo total: ${user_costs['cost_total']:.4f}\n\n"
                f"\U0001f30d *Global (todos los usuarios):*\n"
                f"  tokens: {stats['global_tokens_in']} in / {stats['global_tokens_out']} out\n"
                f"  haiku: {stats['global_calls_haiku']} calls, ${global_costs['cost_haiku']:.4f}\n"
                f"  sonnet: {stats['global_calls_sonnet']} calls, ${global_costs['cost_sonnet']:.4f}\n"
                f"  est costo total: ${global_costs['cost_total']:.4f}"
            )
            await send_text_message(sender, msg)
        else:
            await send_text_message(
                sender, "Este comando no esta disponible."
            )
        return

    # AI-only mode — bypass keyword routing, send everything to AI
    if mode == "ai_only":
        logger.info("AI-only mode for %s — routing to AI classifier", sender)
        ai_result = await classify_with_ai(text, user.id, display_name)
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens, model=LLM_MODEL)
        logger.info("AI classify (action=%s) for '%s'", ai_result.action, text[:50])

        # If AI detects a medical emergency, send response immediately — no search
        if ai_result.action == "emergency":
            reply = ai_result.text or (
                "\U0001f6a8 Esto suena como una emergencia médica. Por favor:\n"
                "1. Llama al *911* o ve a la emergencia más cercana AHORA\n"
                "2. Línea de emergencias nacional: *171*\n\n"
                "NO busques medicamentos para emergencias — ve al médico de inmediato."
            )
            if debug_on:
                reply += await _build_debug(sender, user.id, ai_result)
            await send_text_message(sender, reply)
            return

        # If AI detects a view_similar request, re-run last search
        if ai_result.action == "view_similar":
            await _handle_view_similar(sender, user)
            return

        # If AI detects a nearest_store query, show nearby pharmacies
        if ai_result.action == "nearest_store":
            if not user.latitude:
                await set_onboarding_step(sender, "awaiting_location")
                await send_text_message(
                    sender, MSG_NEED_LOCATION.format(name=display_name)
                )
                return
            if ai_result.text:
                await send_text_message(sender, ai_result.text)
            await _handle_nearest_store(
                sender, user, display_name, debug_on=debug_on, ai_result=ai_result,
            )
            return

        # If AI detects a drug search, perform it
        if ai_result.action == "drug_search" and ai_result.drug_query:
            if not user.latitude:
                await set_onboarding_step(sender, "awaiting_location")
                await send_text_message(
                    sender, MSG_NEED_LOCATION.format(name=display_name)
                )
                return
            # If AI included a conversational response (e.g., symptom acknowledgment),
            # send it before the search results
            if ai_result.text:
                await send_text_message(sender, ai_result.text)
            await _handle_drug_search(
                sender, user, ai_result.drug_query, display_name,
                debug_on=debug_on, ai_result=ai_result,
            )
            return

        # If AI wants to clarify a vague category query, ask the question
        # and stash the original context so the next reply is merged back in.
        if ai_result.action == "clarify_needed" and ai_result.clarify_question:
            context = ai_result.clarify_context or text
            await set_awaiting_clarification(sender, context)
            reply = ai_result.clarify_question
            if debug_on:
                reply += await _build_debug(sender, user.id, ai_result)
            await send_text_message(sender, reply)
            await _update_memory_safe(
                user.id, display_name, text, ai_result.clarify_question,
            )
            return

        # For all other actions, generate a full AI response
        if ai_result.text:
            reply = ai_result.text
            tokens_ai = ai_result
        else:
            full_result = await generate_response(text, user.id, display_name)
            await increment_token_usage(user.id, full_result.input_tokens, full_result.output_tokens, model=LLM_MODEL)
            reply = full_result.text
            tokens_ai = full_result

        if debug_on:
            reply += await _build_debug(sender, user.id, tokens_ai)
        await send_text_message(sender, reply)
        await _update_memory_safe(user.id, display_name, text, reply)
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
        if action == "nearest_store":
            if not user.latitude:
                await set_onboarding_step(sender, "awaiting_location")
                await send_text_message(
                    sender, MSG_NEED_LOCATION.format(name=display_name)
                )
                return
            await _handle_nearest_store(sender, user, display_name, debug_on=debug_on)
            return

    # Classify intent (keywords first, AI fallback)
    intent = await classify_intent(text, user.id, user.name or "")
    await increment_token_usage(user.id, intent.input_tokens, intent.output_tokens, model=LLM_MODEL)

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
        # Item 29 (v0.13.2) — show the category quick-reply menu instead
        # of the legacy MSG_RETURNING text when the kill-switch is on and
        # the user is fully onboarded. Setting evaluates as "true"/"false"
        # (string) — anything other than the literal "true" falls back to
        # the legacy path so a misconfigured setting is never catastrophic.
        menu_setting = (await get_setting("category_menu_enabled")).strip().lower()
        if menu_setting == "true":
            await _send_category_list(sender, display_name)
        else:
            pref_label = (
                "galeria" if user.display_preference == "grid" else "imagen grande"
            )
            await send_text_message(
                sender,
                MSG_RETURNING.format(
                    name=display_name, zone=user.zone_name, pref=pref_label,
                ),
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

        # If AI included a conversational response (symptom acknowledgment), send first
        if intent.response_text:
            await send_text_message(sender, intent.response_text)

        query = intent.drug_query or text
        await _handle_drug_search(sender, user, query, display_name, debug_on=debug_on)

    elif intent.action == "clarify_needed" and intent.clarify_question:
        # Vague category — ask a clarifying question and stash the original
        # query so the next reply is merged back in as a refined search.
        context = intent.clarify_context or text
        await set_awaiting_clarification(sender, context)
        reply = intent.clarify_question
        if debug_on:
            reply += await _build_debug(sender, user.id)
        await send_text_message(sender, reply)
        await _update_memory_safe(
            user.id, display_name, text, intent.clarify_question,
        )

    elif intent.action == "nearest_store":
        if not user.latitude:
            await set_onboarding_step(sender, "awaiting_location")
            await send_text_message(
                sender, MSG_NEED_LOCATION.format(name=display_name)
            )
            return
        if intent.response_text:
            await send_text_message(sender, intent.response_text)
        await _handle_nearest_store(sender, user, display_name, debug_on=debug_on)

    elif intent.action == "emergency":
        reply = intent.response_text or (
            "\U0001f6a8 Esto suena como una emergencia médica. Por favor:\n"
            "1. Llama al *911* o ve a la emergencia más cercana AHORA\n"
            "2. Línea de emergencias nacional: *171*\n\n"
            "NO busques medicamentos para emergencias — ve al médico de inmediato."
        )
        if debug_on:
            reply += await _build_debug(sender, user.id)
        await send_text_message(sender, reply)

    elif intent.action == "question":
        # Check if the question is about a pharmacy store
        store = await _try_store_lookup(text)
        if store:
            await send_text_message(sender, format_store_info(store))
        else:
            # Use AI responder for complex questions
            ai_result = await generate_response(text, user.id, display_name)
            await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens, model=LLM_MODEL)
            logger.info("AI response (role=%s) for '%s'", ai_result.role_used, text[:50])
            reply = ai_result.text
            if debug_on:
                reply += await _build_debug(sender, user.id, ai_result)
            await send_text_message(sender, reply)
            await _update_memory_safe(user.id, display_name, text, reply)

    else:
        # Unknown intent — try AI responder before giving up
        ai_result = await generate_response(text, user.id, display_name)
        await increment_token_usage(user.id, ai_result.input_tokens, ai_result.output_tokens, model=LLM_MODEL)
        logger.info("AI fallback (role=%s) for '%s'", ai_result.role_used, text[:50])
        reply = ai_result.text
        if debug_on:
            reply += await _build_debug(sender, user.id, ai_result)
        await send_text_message(sender, reply)
        await _update_memory_safe(user.id, display_name, text, reply)


def _should_ask_feedback(response) -> bool:
    """Decide whether to append the ¿Te sirvió? prompt after a drug search.

    The prompt only makes sense when the user actually has something to rate.
    Returns False when:
      1. The response has zero results (nothing to rate).
      2. Every active scraper failed (total outage — also zero results).

    Partial failures (1 of 3 scrapers down but at least one returned products)
    still get the prompt — the user has real results to evaluate even if
    coverage was incomplete.

    Args:
        response: SearchResponse from ``search_drug``.

    Returns:
        True if the feedback prompt should be sent, False otherwise.
    """
    if not response.results:
        return False
    total_active = len(ACTIVE_SCRAPERS)
    failed_count = len(response.failed_pharmacies or [])
    if total_active > 0 and failed_count >= total_active:
        # Defensive — if every scraper failed we would not have any results,
        # so this branch is technically unreachable after the first check.
        # Kept as an explicit guard against future regressions where we might
        # start returning cached or partial data alongside a total outage.
        return False
    return True


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
    The feedback prompt is suppressed on zero-result and total-failure
    responses — see `_should_ask_feedback`.

    Args:
        sender: WhatsApp phone number.
        user: User record with location and preferences.
        query: Drug name or search term.
        display_name: User's display name.
        debug_on: Whether to append debug footer.
        ai_result: AiResponse from classification (for token stats).
    """
    logger.info("Drug search from %s/%s (%s): '%s'", sender, display_name, user.zone_name, query)

    # Check for drug interactions with user's known medications
    client_memory = await get_memory(user.id)
    known_meds = extract_medications_from_memory(client_memory)
    if known_meds:
        # Check interactions between the searched drug and known medications
        drugs_to_check = [query] + known_meds
        interaction_result = await check_interactions(drugs_to_check)
        if interaction_result.has_interactions:
            warning = format_interaction_warning(interaction_result)
            await send_text_message(sender, warning)
            logger.info(
                "Drug interaction warning for %s: %s + %s",
                sender, query, known_meds,
            )

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

    # Ask for feedback ONLY when there is something to rate (Item 34).
    # Zero-result or total-outage responses get a retry hint instead —
    # asking "¿Te sirvió?" when the bot showed no products is a UX
    # confusion signal and trains users that the feedback prompt means
    # "did you understand me?" rather than "did these results help?".
    if _should_ask_feedback(response):
        await set_onboarding_step(sender, "awaiting_feedback")
        await send_text_message(sender, MSG_ASK_FEEDBACK)
    else:
        await send_text_message(sender, MSG_RETRY_DIFFERENT_NAME)
        logger.info(
            "Skipped ¿Te sirvió? for %s (query=%r, results=%d, failed=%s)",
            sender, query, results_count, response.failed_pharmacies,
        )

    # Update user memory with search context
    summary = f"Searched: {query} → {results_count} results"
    await _update_memory_safe(user.id, display_name, query, summary)


async def _handle_nearest_store(
    sender: str,
    user,
    display_name: str,
    debug_on: bool = False,
    ai_result=None,
) -> None:
    """Find and send nearest pharmacy stores to the user.

    Queries all pharmacy chains from the pharmacy_locations DB table
    and returns them sorted by distance from the user's location.

    Args:
        sender: WhatsApp phone number.
        user: User record with location data.
        display_name: User's display name.
        debug_on: Whether to append debug footer.
        ai_result: AiResponse from classification (for token stats).
    """
    logger.info(
        "Nearest store query from %s/%s (%s)",
        sender, display_name, user.zone_name,
    )

    stores = await get_all_nearby_stores(
        latitude=user.latitude,
        longitude=user.longitude,
    )

    reply = format_nearby_stores(stores, zone_name=user.zone_name)
    if debug_on:
        reply += await _build_debug(sender, user.id, ai_result)
    await send_text_message(sender, reply)

    # Ask for feedback (same as drug search)
    await set_onboarding_step(sender, "awaiting_feedback")
    await send_text_message(sender, MSG_ASK_FEEDBACK)

    await _update_memory_safe(
        user.id, display_name,
        "farmacia cercana",
        f"Showed {len(stores)} nearby stores",
    )


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
        global_tokens_in=stats["global_tokens_in"],
        global_tokens_out=stats["global_tokens_out"],
        model_used=getattr(ai_result, "model", "") if ai_result else "",
        calls_haiku=stats["calls_haiku"],
        calls_sonnet=stats["calls_sonnet"],
        global_calls_haiku=stats["global_calls_haiku"],
        global_calls_sonnet=stats["global_calls_sonnet"],
        global_tokens_in_haiku=stats["global_tokens_in_haiku"],
        global_tokens_out_haiku=stats["global_tokens_out_haiku"],
        global_tokens_in_sonnet=stats["global_tokens_in_sonnet"],
        global_tokens_out_sonnet=stats["global_tokens_out_sonnet"],
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
            "No tienes una busqueda reciente. Enviame el nombre de un producto de farmacia.",
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
