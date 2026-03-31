"""Handle incoming WhatsApp messages and dispatch responses."""

import logging

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_local_image, send_text_message
from farmafacil.models.schemas import DrugResult
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.image_grid import generate_product_grid
from farmafacil.services.intent import HELP_MESSAGE, classify_intent
from farmafacil.services.search import search_drug
from farmafacil.services.users import get_or_create_user, update_user_location

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "\U0001f48a *Bienvenido a FarmaFacil!*\n\n"
    "Busco medicamentos en farmacias de Venezuela por ti.\n\n"
    "Para empezar, necesito saber tu ubicacion.\n"
    "Dime tu zona o barrio (ej: _La Boyera_, _Chacao_, _Altamira_)"
)

LOCATION_ASK_MESSAGE = (
    "Dime tu nueva zona o barrio.\n\n"
    "Ejemplo: _La Boyera_, _Chacao_, _Maracaibo_"
)

LOCATION_NOT_FOUND_MESSAGE = (
    "No logre ubicar esa zona en Venezuela.\n"
    "Intenta con el nombre de tu barrio o urbanizacion.\n\n"
    "Ejemplos: _La Boyera_, _El Cafetal_, _Chacao_, _Altamira_, _Maracaibo_"
)

LOCATION_REQUIRED_MESSAGE = (
    "Primero necesito saber tu ubicacion para buscarte "
    "medicamentos en farmacias cercanas.\n\n"
    "Dime tu zona o barrio (ej: _La Boyera_, _Chacao_, _Altamira_)"
)

# In-memory set: users who have been asked for location and we're waiting for their reply.
_awaiting_location: set[str] = set()


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message and send a response.

    Args:
        sender: Phone number of the sender (with country code).
        message_text: The text content of the message.
    """
    text = message_text.strip()
    if not text:
        return

    # Get or create user
    user = await get_or_create_user(sender)

    # ── State: Awaiting location (user was explicitly asked for zone) ──
    if sender in _awaiting_location:
        location = await geocode_zone(text)
        if location:
            _awaiting_location.discard(sender)
            user = await update_user_location(
                phone_number=sender,
                latitude=location["lat"],
                longitude=location["lng"],
                zone_name=location["zone_name"],
                city_code=location["city"],
            )
            await send_text_message(
                sender,
                f"\u2705 Ubicacion guardada: *{user.zone_name}*\n\n"
                "Ahora envia el nombre de un medicamento para buscar.\n"
                "Ejemplo: _losartan_ o _acetaminofen_",
            )
        else:
            await send_text_message(sender, LOCATION_NOT_FOUND_MESSAGE)
        return

    # ── State: New user with no location ──
    if user.latitude is None:
        # Classify intent to handle greetings/help, but for everything else
        # ask for location first — do NOT try to geocode random messages.
        intent = await classify_intent(text)
        if intent.action == "greeting":
            _awaiting_location.add(sender)
            await send_text_message(sender, WELCOME_MESSAGE)
        elif intent.action == "help":
            await send_text_message(sender, HELP_MESSAGE)
        else:
            # They sent a drug name or question, but we have no location yet.
            _awaiting_location.add(sender)
            await send_text_message(sender, LOCATION_REQUIRED_MESSAGE)
        return

    # ── State: Existing user with location — classify intent ──
    intent = await classify_intent(text)

    if intent.action == "greeting":
        await send_text_message(
            sender,
            f"\U0001f48a *Hola de nuevo!* Buscando en *{user.zone_name}*.\n\n"
            "Envia el nombre de un medicamento para buscar.\n"
            "Escribe _cambiar zona_ para cambiar tu ubicacion.",
        )

    elif intent.action == "location_change":
        _awaiting_location.add(sender)
        await send_text_message(sender, LOCATION_ASK_MESSAGE)

    elif intent.action == "help":
        await send_text_message(sender, HELP_MESSAGE)

    elif intent.action == "drug_search":
        query = intent.drug_query or text
        logger.info("Drug search from %s (%s): '%s'", sender, user.zone_name, query)
        response = await search_drug(
            query=query,
            city_code=user.city_code,
            latitude=user.latitude,
            longitude=user.longitude,
            zone_name=user.zone_name,
        )
        reply = format_search_results(response)
        await send_text_message(sender, reply)

        # Generate and send product grid image
        if response.results:
            grid_path = await generate_product_grid(response.results, max_products=6)
            if grid_path:
                caption = f"Resultados para *{response.query}*"
                if response.zone:
                    caption += f" cerca de *{response.zone}*"
                await send_local_image(sender, grid_path, caption)
                # Clean up temp file
                import os
                os.unlink(grid_path)

    elif intent.action == "question" and intent.response_text:
        await send_text_message(sender, intent.response_text)

    else:
        await send_text_message(
            sender,
            "No estoy seguro de lo que necesitas.\n"
            "Envia el nombre de un medicamento y te busco donde esta disponible.\n\n"
            "Escribe _ayuda_ para ver las instrucciones.",
        )


def _build_product_caption(result: DrugResult) -> str:
    """Build a Farmatodo-style product card caption for WhatsApp.

    Mimics the Farmatodo app card layout:
    - Discount badge
    - Brand
    - Product name
    - Offer price + original price strikethrough
    - Per-unit price
    - Nearby store + distance

    Args:
        result: Drug search result with pricing and store data.

    Returns:
        Formatted WhatsApp caption string.
    """
    lines = []

    # Discount badge
    if result.discount_pct:
        lines.append(f"\U0001f7e2 *{result.discount_pct} DCTO*")

    # Brand
    if result.brand:
        lines.append(f"_{result.brand}_")

    # Product name
    lines.append(f"*{result.drug_name}*")

    # Price line: offer price + original strikethrough
    if result.price_bs is not None:
        price_line = f"*Bs. {result.price_bs:,.2f}*"
        if result.full_price_bs and result.full_price_bs != result.price_bs:
            price_line += f"  ~Bs. {result.full_price_bs:,.2f}~"
        lines.append(price_line)

    # Per-unit price
    if result.unit_label:
        lines.append(f"{result.unit_label}")

    # Prescription required
    if result.requires_prescription:
        lines.append("\U0001f4cb Requiere receta")

    # Stock info
    if result.stores_in_stock > 0:
        lines.append(f"\u2705 Disponible en {result.stores_in_stock} tiendas")

    # Nearest store
    if result.nearby_stores:
        closest = result.nearby_stores[0]
        lines.append(f"\U0001f4cd {closest.store_name} — {closest.distance_km:.1f} km")

    return "\n".join(lines)
