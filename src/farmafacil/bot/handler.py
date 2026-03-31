"""Handle incoming WhatsApp messages — smart conversational flow."""

import logging
import os

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_image_message, send_local_image, send_text_message
from farmafacil.models.schemas import DrugResult
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.image_grid import generate_product_grid
from farmafacil.services.intent import HELP_MESSAGE, classify_intent
from farmafacil.services.search import search_drug
from farmafacil.services.store_backfill import format_store_info, lookup_store
from farmafacil.services.users import (
    get_or_create_user,
    set_onboarding_step,
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
        # Always use LLM here to distinguish greetings from actual names
        from farmafacil.services.intent import classify_intent_llm
        intent = await classify_intent_llm(text)

        # If it's just a greeting (hi, hola), re-ask for name
        if intent.action == "greeting" and not intent.detected_name:
            await send_text_message(
                sender,
                "\U0001f60a Hola! Dime tu nombre para poder atenderte mejor.\n\n"
                "Ejemplo: _Maria_, _Jose_, _Carlos_"
            )
            return

        name = intent.detected_name or text.strip().title()

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
        if intent.detected_location:
            location = await geocode_zone(intent.detected_location)
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
        # Try to geocode — but also check if LLM can extract location
        intent = await classify_intent(text)
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

    # ── Onboarding complete — smart conversational flow ─────────────────

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

    # Classify intent (keywords first, LLM fallback)
    intent = await classify_intent(text)
    display_name = user.name or "amigo"

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
        logger.info("Drug search from %s/%s (%s): '%s'", sender, display_name, user.zone_name, query)
        response = await search_drug(
            query=query,
            city_code=user.city_code,
            latitude=user.latitude,
            longitude=user.longitude,
            zone_name=user.zone_name,
        )
        # Send grid/detail image FIRST, then text summary below
        if response.results:
            if user.display_preference == "detail":
                await _send_detail_images(sender, response.results)
            else:
                await _send_grid_image(sender, response)

        reply = format_search_results(response)
        await send_text_message(sender, reply)

    elif intent.action == "question":
        # Check if the question is about a pharmacy store
        store = await _try_store_lookup(text)
        if store:
            await send_text_message(sender, format_store_info(store))
        elif intent.response_text:
            await send_text_message(sender, intent.response_text)
        else:
            await send_text_message(
                sender,
                "No tengo informacion sobre eso. Enviame el nombre de un medicamento para buscar.",
            )

    else:
        await send_text_message(
            sender,
            f"*{display_name}*, no estoy seguro de lo que necesitas.\n"
            "Enviame el nombre de un medicamento para buscar.\n\n"
            "Escribe _ayuda_ para ver las instrucciones.",
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
    grid_path = await generate_product_grid(response.results, max_products=6)
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
