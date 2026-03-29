"""Handle incoming WhatsApp messages and dispatch responses."""

import logging

from farmafacil.bot.formatter import format_search_results
from farmafacil.bot.whatsapp import send_text_message
from farmafacil.services.geocode import geocode_zone
from farmafacil.services.search import search_drug
from farmafacil.services.users import get_or_create_user, update_user_location, user_has_location

logger = logging.getLogger(__name__)

GREETING_WORDS = {
    "hola", "hi", "hello", "hey", "buenos dias", "buenas tardes",
    "buenas noches", "buenas", "saludos",
}

LOCATION_CHANGE_WORDS = {
    "cambiar ubicacion", "cambiar ubicación", "cambiar zona",
    "nueva ubicacion", "nueva ubicación", "otra zona", "moverme",
}

WELCOME_MESSAGE = (
    "\U0001f48a *Bienvenido a FarmaFacil!*\n\n"
    "Busco medicamentos en farmacias de Venezuela por ti.\n\n"
    "Para empezar, necesito saber tu ubicacion.\n"
    "Dime tu zona o barrio (ej: _El Cafetal_, _Chacao_, _Altamira_)"
)

LOCATION_ASK_MESSAGE = (
    "Para buscarte medicamentos cerca, dime tu zona o barrio.\n\n"
    "Ejemplo: _El Cafetal_, _Chacao_, _Maracaibo_"
)

LOCATION_NOT_FOUND_MESSAGE = (
    "No reconozco esa zona. Intenta con el nombre de tu barrio "
    "o una zona conocida de tu ciudad.\n\n"
    "Ejemplos: _El Cafetal_, _Chacao_, _Altamira_, _Maracaibo_, _Valencia_"
)


async def handle_incoming_message(sender: str, message_text: str) -> None:
    """Process an incoming WhatsApp message and send a response.

    Args:
        sender: Phone number of the sender (with country code).
        message_text: The text content of the message.
    """
    text = message_text.strip()
    if not text:
        return

    text_lower = text.lower()

    # Get or create user
    user = await get_or_create_user(sender)

    # Check for greetings
    if text_lower in GREETING_WORDS:
        if user.latitude is not None:
            await send_text_message(
                sender,
                f"\U0001f48a *Hola de nuevo!* Buscando en *{user.zone_name}*.\n\n"
                "Envia el nombre de un medicamento para buscar.\n"
                "Escribe _cambiar zona_ para cambiar tu ubicacion.",
            )
        else:
            await send_text_message(sender, WELCOME_MESSAGE)
        return

    # Check for location change request
    if text_lower in LOCATION_CHANGE_WORDS:
        await send_text_message(sender, LOCATION_ASK_MESSAGE)
        return

    # If user has no location, try to geocode their message as a zone name
    if user.latitude is None:
        location = geocode_zone(text)
        if location:
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

    # User has location — check if they're updating it
    location = geocode_zone(text)
    if location and len(text.split()) <= 3:
        # Short text that matches a zone — probably updating location, not searching
        user = await update_user_location(
            phone_number=sender,
            latitude=location["lat"],
            longitude=location["lng"],
            zone_name=location["zone_name"],
            city_code=location["city"],
        )
        await send_text_message(
            sender,
            f"\u2705 Ubicacion actualizada: *{user.zone_name}*\n\n"
            "Envia el nombre de un medicamento para buscar.",
        )
        return

    # User has location — treat as drug search
    logger.info(
        "Drug search from %s (%s): '%s'",
        sender,
        user.zone_name,
        text,
    )
    response = await search_drug(
        query=text,
        city_code=user.city_code,
        latitude=user.latitude,
        longitude=user.longitude,
        zone_name=user.zone_name,
    )
    reply = format_search_results(response)
    await send_text_message(sender, reply)
