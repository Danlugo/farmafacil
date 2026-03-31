"""Handle incoming WhatsApp messages — state machine with onboarding flow."""

import logging
import os

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_image_message, send_local_image, send_text_message
from farmafacil.models.schemas import DrugResult
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.image_grid import generate_product_grid
from farmafacil.services.intent import HELP_MESSAGE, classify_intent
from farmafacil.services.search import search_drug
from farmafacil.services.users import (
    get_or_create_user,
    set_onboarding_step,
    update_user_location,
    update_user_name,
    update_user_preference,
)

logger = logging.getLogger(__name__)

# ── Messages (all in Spanish for Venezuelan users) ──────────────────────

MSG_WELCOME = (
    "\U0001f48a *Hola! Soy FarmaFacil*\n\n"
    "Te ayudo a encontrar medicamentos en farmacias de Venezuela.\n\n"
    "Para empezar, *como te llamas?*"
)

MSG_ASK_LOCATION = (
    "Mucho gusto *{name}*! \U0001f91d\n\n"
    "Ahora dime, *en que zona o barrio estas?*\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_LOCATION_SAVED = (
    "\u2705 Guardado: *{zone}*\n\n"
    "Como prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande* — un producto a la vez con todos los detalles\n"
    "*2.* \U0001f5bc *Galeria* — varios productos en una sola imagen\n\n"
    "Responde *1* o *2*"
)

MSG_PREFERENCE_SAVED = (
    "\u2705 Listo *{name}*! Ya estas configurado.\n\n"
    "Enviame el nombre de un medicamento y te busco donde esta disponible.\n"
    "Ejemplo: _losartan_ o _acetaminofen_"
)

MSG_LOCATION_NOT_FOUND = (
    "No logre ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanizacion.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Maracaibo_"
)

MSG_INVALID_PREFERENCE = (
    "Por favor responde *1* para imagen grande o *2* para galeria."
)

MSG_RETURNING_USER = (
    "\U0001f48a *Hola de nuevo {name}!*\n"
    "Buscando en *{zone}* (modo _{pref}_).\n\n"
    "Enviame el nombre de un medicamento para buscar.\n\n"
    "\U0001f527 _Comandos:_\n"
    "\u2022 _cambiar zona_ — nueva ubicacion\n"
    "\u2022 _cambiar preferencia_ — modo de visualizacion\n"
    "\u2022 _cambiar nombre_ — actualizar tu nombre\n"
    "\u2022 _ayuda_ — instrucciones"
)

MSG_ASK_NEW_LOCATION = (
    "Dime tu nueva zona o barrio.\n\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

MSG_ASK_NEW_PREFERENCE = (
    "Como prefieres ver los resultados?\n\n"
    "*1.* \U0001f4f8 *Imagen grande* — un producto a la vez\n"
    "*2.* \U0001f5bc *Galeria* — varios productos en una imagen\n\n"
    "Responde *1* o *2*"
)

MSG_ASK_NEW_NAME = "Como te llamas?"

# ── Keyword sets ────────────────────────────────────────────────────────

PREFERENCE_CHANGE_WORDS = {
    "cambiar preferencia", "cambiar vista", "cambiar modo",
    "otra vista", "otro modo",
}

LOCATION_CHANGE_WORDS = {
    "cambiar ubicacion", "cambiar ubicación", "cambiar zona",
    "nueva ubicacion", "nueva ubicación", "otra zona",
}

NAME_CHANGE_WORDS = {
    "cambiar nombre", "nuevo nombre",
}


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message using DB-persisted state.

    Args:
        sender: Phone number of the sender.
        message_text: The text content of the message.
    """
    text = message_text.strip()
    if not text:
        return

    user = await get_or_create_user(sender)
    step = user.onboarding_step
    text_lower = text.lower()

    # ── Onboarding: welcome (first contact) ───────────────────────────
    if step == "welcome":
        await set_onboarding_step(sender, "awaiting_name")
        await send_text_message(sender, MSG_WELCOME)
        return

    # ── Onboarding: awaiting_name ───────────────────────────────────────
    if step == "awaiting_name":
        name = text.strip().title()
        user = await update_user_name(sender, name)
        await send_text_message(sender, MSG_ASK_LOCATION.format(name=name))
        return

    # ── Onboarding: awaiting_location ───────────────────────────────────
    if step == "awaiting_location":
        location = await geocode_zone(text)
        if location:
            user = await update_user_location(
                phone_number=sender,
                latitude=location["lat"],
                longitude=location["lng"],
                zone_name=location["zone_name"],
                city_code=location["city"],
            )
            await send_text_message(
                sender, MSG_LOCATION_SAVED.format(zone=user.zone_name)
            )
        else:
            await send_text_message(sender, MSG_LOCATION_NOT_FOUND)
        return

    # ── Onboarding: awaiting_preference ─────────────────────────────────
    if step == "awaiting_preference":
        pref = _parse_preference(text_lower)
        if pref:
            user = await update_user_preference(sender, pref)
            await send_text_message(
                sender, MSG_PREFERENCE_SAVED.format(name=user.name or "amigo")
            )
        else:
            await send_text_message(sender, MSG_INVALID_PREFERENCE)
        return

    # ── Onboarding complete — normal flow ───────────────────────────────

    # Check for settings change commands first (before intent classification)
    if text_lower in LOCATION_CHANGE_WORDS:
        await set_onboarding_step(sender, "awaiting_location")
        await send_text_message(sender, MSG_ASK_NEW_LOCATION)
        return

    if text_lower in PREFERENCE_CHANGE_WORDS:
        await set_onboarding_step(sender, "awaiting_preference")
        await send_text_message(sender, MSG_ASK_NEW_PREFERENCE)
        return

    if text_lower in NAME_CHANGE_WORDS:
        await set_onboarding_step(sender, "awaiting_name")
        await send_text_message(sender, MSG_ASK_NEW_NAME)
        return

    # Classify intent
    intent = await classify_intent(text)
    display_name = user.name or "amigo"
    pref_label = "galeria" if user.display_preference == "grid" else "imagen grande"

    if intent.action == "greeting":
        await send_text_message(
            sender,
            MSG_RETURNING_USER.format(
                name=display_name, zone=user.zone_name, pref=pref_label
            ),
        )

    elif intent.action == "help":
        await send_text_message(sender, HELP_MESSAGE)

    elif intent.action == "drug_search":
        query = intent.drug_query or text
        logger.info("Drug search from %s/%s (%s): '%s'", sender, display_name, user.zone_name, query)
        response = await search_drug(
            query=query,
            city_code=user.city_code,
            latitude=user.latitude,
            longitude=user.longitude,
            zone_name=user.zone_name,
        )
        reply = format_search_results(response)
        await send_text_message(sender, reply)

        # Send images based on user preference
        if response.results:
            if user.display_preference == "detail":
                await _send_detail_images(sender, response.results)
            else:
                await _send_grid_image(sender, response)

    elif intent.action == "question" and intent.response_text:
        await send_text_message(sender, intent.response_text)

    else:
        await send_text_message(
            sender,
            f"*{display_name}*, no estoy seguro de lo que necesitas.\n"
            "Enviame el nombre de un medicamento para buscar.\n\n"
            "Escribe _ayuda_ para ver las instrucciones.",
        )


def _parse_preference(text: str) -> str | None:
    """Parse user's display preference response.

    Args:
        text: Lowercased user input.

    Returns:
        "detail", "grid", or None if invalid.
    """
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
        lines.append(f"{result.unit_label}")

    if result.requires_prescription:
        lines.append("\U0001f4cb Requiere receta")

    if result.stores_in_stock > 0:
        lines.append(f"\u2705 Disponible en {result.stores_in_stock} tiendas")

    if result.nearby_stores:
        closest = result.nearby_stores[0]
        lines.append(f"\U0001f4cd {closest.store_name} — {closest.distance_km:.1f} km")

    return "\n".join(lines)
